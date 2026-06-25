"""One-shot backfill of missing Postgres registry rows from durable result files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Sequence

import pandas as pd

from integrations.wallet_editor_registry import resolve_source
from integrations.wallet_editor_registry_async import OutboxRecord, load_outbox_records
from integrations.wallet_editor_registry_db.connection import connect
from integrations.wallet_editor_registry_db.diagnose_processed import (
    _load_postgres_fingerprint_set,
)
from integrations.wallet_editor_registry_db.frames import postgres_run_exists
from integrations.wallet_editor_registry_db.mapping import map_all_results_row, map_runs_row
from integrations.wallet_editor_registry_db.store import PostgresRegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    OUTBOX_STATUS_SYNCED,
    build_runs_row,
    load_processed_run_ids,
    load_processed_run_ids_result,
    missing_result_fingerprints_in_set,
    result_row_fingerprint,
    rows_from_result_excel,
)
from integrations.wallet_editor_registry_xlsx import card_as_text
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)


class BackfillStore(Protocol):
    def upsert_run(self, row) -> bool: ...

    def upsert_result(self, row) -> bool: ...


@dataclass
class BackfillSummary:
    scanned: int = 0
    eligible: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    missing_result_file: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = True

    @property
    def error_count(self) -> int:
        return len(self.errors)


def _parse_outbox_datetime(value: str) -> datetime:
    text = (value or "").strip()
    if not text:
        raise ValueError("outbox run timestamp is empty")
    return datetime.fromisoformat(text)


def _rows_to_backfill(
    result_path: str,
    *,
    run_id: str,
    present_fingerprints: frozenset[str],
) -> list:
    missing_fps = set(
        missing_result_fingerprints_in_set(result_path, present_fingerprints)
    )
    if not missing_fps:
        return []

    result_df = pd.read_excel(
        result_path,
        engine="openpyxl",
        converters={"card": card_as_text},
    )
    new_rows = rows_from_result_excel(result_df)
    mapped = []
    for idx in new_rows.index:
        row = new_rows.loc[idx]
        fingerprint = result_row_fingerprint(row)
        if fingerprint not in missing_fps:
            continue
        mapped.append(
            map_all_results_row(
                row,
                run_id=run_id,
                source_row_index=int(idx),
            )
        )
    return mapped


def _build_run_row_from_outbox(record: OutboxRecord):
    runs_df = build_runs_row(
        started_at=_parse_outbox_datetime(record.run_started_at),
        finished_at=_parse_outbox_datetime(record.run_finished_at),
        input_rows=record.stats_input_rows,
        stats_ok=record.stats_success_rows,
        stats_fail=record.stats_failed_rows,
        stats_skip=record.stats_skipped_rows,
        output_file=record.output_file or os.path.basename(record.result_file_path),
    )
    return map_runs_row(
        runs_df.iloc[0],
        run_id=record.run_id,
        operator_profile=record.operator_profile,
        source=resolve_source(record.operator_profile),
    )


def _run_exists_in_store(run_id: str, store: BackfillStore) -> bool:
    runs = getattr(store, "runs", None)
    if isinstance(runs, dict):
        return run_id in runs
    return postgres_run_exists(run_id)


def _apply_run_backfill(
    record: OutboxRecord,
    result_rows: list,
    *,
    store: BackfillStore,
    summary: BackfillSummary,
) -> None:
    if not _run_exists_in_store(record.run_id, store):
        store.upsert_run(_build_run_row_from_outbox(record))

    for row in result_rows:
        if store.upsert_result(row):
            summary.inserted += 1
        else:
            summary.skipped_existing += 1


def backfill_processed_runs(
    *,
    apply: bool = False,
    run_ids: Sequence[str] | None = None,
    store: BackfillStore | None = None,
) -> BackfillSummary:
    """
    Backfill missing ``we_registry_results`` rows from durable outbox result files.

    Default is dry-run (``apply=False``).
    """
    summary = BackfillSummary(dry_run=not apply)
    processed_result = load_processed_run_ids_result()
    if processed_result.corrupted:
        summary.errors.append(
            processed_result.error or "registry_processed_run_ids.json is corrupted"
        )
        return summary

    processed = sorted(load_processed_run_ids())
    if run_ids:
        allowed = frozenset(str(run_id) for run_id in run_ids)
        processed = [run_id for run_id in processed if run_id in allowed]

    summary.scanned = len(processed)
    if not processed:
        return summary

    outbox_by_id = {record.run_id: record for record in load_outbox_records()}
    try:
        present_fingerprints = _load_postgres_fingerprint_set()
    except Exception as exc:
        summary.errors.append(f"postgres fingerprint load failed: {exc}")
        return summary

    pending: list[tuple[OutboxRecord, list]] = []
    for run_id in processed:
        record = outbox_by_id.get(run_id)
        if record is None:
            continue
        if record.status != OUTBOX_STATUS_SYNCED:
            continue

        result_path = record.result_file_path
        if not result_path or not os.path.isfile(result_path):
            summary.missing_result_file += 1
            continue

        try:
            result_rows = _rows_to_backfill(
                result_path,
                run_id=run_id,
                present_fingerprints=present_fingerprints,
            )
        except Exception as exc:
            summary.errors.append(f"{run_id}: {exc}")
            continue

        if not result_rows:
            continue

        summary.eligible += 1
        pending.append((record, result_rows))

    if not apply:
        summary.inserted = sum(len(rows) for _record, rows in pending)
        return summary

    if store is not None:
        for record, result_rows in pending:
            try:
                _apply_run_backfill(record, result_rows, store=store, summary=summary)
            except Exception as exc:
                summary.errors.append(f"{record.run_id}: {exc}")
        return summary

    with connect(for_mirror=False) as conn:
        with conn.cursor() as cur:
            pg_store = PostgresRegistryStore(cur)
            for record, result_rows in pending:
                try:
                    _apply_run_backfill(record, result_rows, store=pg_store, summary=summary)
                    conn.commit()
                except Exception as exc:
                    summary.errors.append(f"{record.run_id}: {exc}")
                    conn.rollback()

    return summary


def format_backfill_summary(summary: BackfillSummary) -> str:
    mode = "apply" if not summary.dry_run else "dry-run"
    lines = [
        "WalletEditor processed_run_ids backfill",
        "",
        f"mode: {mode}",
        f"scanned: {summary.scanned}",
        f"eligible: {summary.eligible}",
        f"inserted: {summary.inserted}",
        f"skipped_existing: {summary.skipped_existing}",
        f"missing_result_file: {summary.missing_result_file}",
        f"errors: {summary.error_count}",
    ]
    if summary.errors:
        lines.append("")
        lines.append("error details:")
        for message in summary.errors:
            lines.append(f"  - {message}")
    return "\n".join(lines)
