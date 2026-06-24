"""Read-only diagnostics for processed_run_ids vs PostgreSQL registry rows."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Sequence

import pandas as pd

from integrations.wallet_editor_registry_db.config import registry_source
from integrations.wallet_editor_registry_db.connection import connect
from integrations.wallet_editor_registry_lifecycle import (
    fingerprints_from_result_excel,
    load_processed_run_ids_result,
    missing_result_fingerprints,
)

REASON_OK = "OK"
REASON_B1_REAL_GAP = "B1_REAL_GAP"
REASON_B2_FINGERPRINT_MISMATCH = "B2_FINGERPRINT_MISMATCH"
REASON_B3_MISSING_DURABLE_RESULT = "B3_MISSING_DURABLE_RESULT"
REASON_B4_HEALTH_BUG = "B4_HEALTH_BUG"
REASON_NO_OUTBOX_RECORD = "NO_OUTBOX_RECORD"
REASON_CORRUPTED_PROCESSED_RUN_IDS = "CORRUPTED_PROCESSED_RUN_IDS"

SAMPLE_MISSING_LIMIT = 3


@dataclass(frozen=True, slots=True)
class DiagnoseRunEntry:
    run_id: str
    result_file_path: str | None
    result_file_exists: bool
    outbox_status: str | None
    result_fingerprints_count: int
    postgres_rows_by_run_id: int
    postgres_fingerprints_found: int
    missing_fingerprints_count: int
    sample_missing_fingerprints: tuple[str, ...]
    health_would_count: bool
    probable_reason: str


@dataclass(frozen=True, slots=True)
class DiagnoseProcessedReport:
    registry_source: str
    processed_run_ids_total: int
    processed_run_ids_corrupted: bool
    processed_run_ids_error: str | None
    limit: int
    entries: tuple[DiagnoseRunEntry, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "registry_source": self.registry_source,
            "processed_run_ids_total": self.processed_run_ids_total,
            "processed_run_ids_corrupted": self.processed_run_ids_corrupted,
            "processed_run_ids_error": self.processed_run_ids_error,
            "limit": self.limit,
            "entries": [asdict(entry) for entry in self.entries],
        }


def _postgres_run_row_counts(run_ids: Sequence[str]) -> dict[str, int]:
    if not run_ids:
        return {}
    with connect(for_mirror=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, count(*)::int
                FROM we_registry_results
                WHERE run_id = ANY(%s)
                GROUP BY run_id
                """,
                (list(run_ids),),
            )
            return {str(run_id): int(count) for run_id, count in cur.fetchall()}


def _postgres_fingerprints_present(fingerprints: Sequence[str]) -> set[str]:
    if not fingerprints:
        return set()
    with connect(for_mirror=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT row_fingerprint
                FROM we_registry_results
                WHERE row_fingerprint = ANY(%s)
                """,
                (list(fingerprints),),
            )
            return {str(row[0]) for row in cur.fetchall()}


def _load_health_all_results_df() -> pd.DataFrame:
    from integrations.wallet_editor_registry_db.frames import load_registry_frames_from_postgres

    all_results_df, _runs_df = load_registry_frames_from_postgres()
    return all_results_df


def _classify_reason(
    *,
    result_file_exists: bool,
    outbox_status: str | None,
    postgres_rows_by_run_id: int,
    missing_fingerprints_count: int,
    health_would_count: bool,
    postgres_fingerprints_found: int,
    result_fingerprints_count: int,
) -> str:
    if outbox_status is None:
        return REASON_NO_OUTBOX_RECORD
    if not result_file_exists:
        return REASON_B3_MISSING_DURABLE_RESULT
    if missing_fingerprints_count == 0 and health_would_count:
        return REASON_B4_HEALTH_BUG
    if missing_fingerprints_count == 0:
        return REASON_OK
    if postgres_rows_by_run_id == 0:
        return REASON_B1_REAL_GAP
    if (
        postgres_fingerprints_found == result_fingerprints_count
        and missing_fingerprints_count > 0
    ):
        return REASON_B4_HEALTH_BUG
    return REASON_B2_FINGERPRINT_MISMATCH


def diagnose_processed_runs(*, limit: int = 20) -> DiagnoseProcessedReport:
    """Diagnose processed run_ids against durable results and PostgreSQL."""
    processed_result = load_processed_run_ids_result()
    run_ids = sorted(processed_result.run_ids)
    limited_run_ids = run_ids[: max(limit, 0)]

    if processed_result.corrupted:
        return DiagnoseProcessedReport(
            registry_source=registry_source(),
            processed_run_ids_total=len(run_ids),
            processed_run_ids_corrupted=True,
            processed_run_ids_error=processed_result.error,
            limit=limit,
            entries=(),
        )

    from integrations.wallet_editor_registry_async import load_outbox_records

    outbox_by_id = {record.run_id: record for record in load_outbox_records()}
    run_row_counts = _postgres_run_row_counts(limited_run_ids)

    all_results_df: pd.DataFrame | None = None
    if registry_source() == "postgres":
        try:
            all_results_df = _load_health_all_results_df()
        except Exception:
            all_results_df = None

    entries: list[DiagnoseRunEntry] = []
    for run_id in limited_run_ids:
        record = outbox_by_id.get(run_id)
        result_path = record.result_file_path if record else None
        result_exists = bool(result_path and os.path.isfile(result_path))

        result_fps: list[str] = []
        if result_exists and result_path is not None:
            result_fps = fingerprints_from_result_excel(result_path)

        postgres_rows = run_row_counts.get(run_id, 0)
        postgres_found = _postgres_fingerprints_present(result_fps)
        missing_fps = [fp for fp in result_fps if fp not in postgres_found]
        missing_count = len(missing_fps)

        health_would_count = False
        if result_exists and result_path is not None and all_results_df is not None:
            health_would_count = bool(missing_result_fingerprints(result_path, all_results_df))

        reason = _classify_reason(
            result_file_exists=result_exists,
            outbox_status=record.status if record else None,
            postgres_rows_by_run_id=postgres_rows,
            missing_fingerprints_count=missing_count,
            health_would_count=health_would_count,
            postgres_fingerprints_found=len(postgres_found),
            result_fingerprints_count=len(result_fps),
        )

        entries.append(
            DiagnoseRunEntry(
                run_id=run_id,
                result_file_path=result_path,
                result_file_exists=result_exists,
                outbox_status=record.status if record else None,
                result_fingerprints_count=len(result_fps),
                postgres_rows_by_run_id=postgres_rows,
                postgres_fingerprints_found=len(postgres_found),
                missing_fingerprints_count=missing_count,
                sample_missing_fingerprints=tuple(missing_fps[:SAMPLE_MISSING_LIMIT]),
                health_would_count=health_would_count,
                probable_reason=reason,
            )
        )

    return DiagnoseProcessedReport(
        registry_source=registry_source(),
        processed_run_ids_total=len(run_ids),
        processed_run_ids_corrupted=False,
        processed_run_ids_error=None,
        limit=limit,
        entries=tuple(entries),
    )


def format_diagnose_processed_report(report: DiagnoseProcessedReport) -> str:
    lines = [
        "WalletEditor processed_run_ids diagnostic",
        "",
        f"registry_source: {report.registry_source}",
        f"processed_run_ids total: {report.processed_run_ids_total}",
        f"showing: {len(report.entries)} (limit {report.limit})",
    ]
    if report.processed_run_ids_corrupted:
        lines.append(f"processed_run_ids corrupted: True")
        if report.processed_run_ids_error:
            lines.append(f"error: {report.processed_run_ids_error}")
        return "\n".join(lines)

    lines.append("")
    if not report.entries:
        lines.append("no processed run_ids to diagnose")
        return "\n".join(lines)

    for entry in report.entries:
        lines.extend(
            [
                f"run_id: {entry.run_id}",
                f"  outbox_status: {entry.outbox_status or '-'}",
                f"  result_file: {entry.result_file_path or '-'}",
                f"  result_file_exists: {entry.result_file_exists}",
                f"  result_fingerprints_count: {entry.result_fingerprints_count}",
                f"  postgres_rows_by_run_id: {entry.postgres_rows_by_run_id}",
                f"  postgres_fingerprints_found: {entry.postgres_fingerprints_found}",
                f"  missing_fingerprints_count: {entry.missing_fingerprints_count}",
            ]
        )
        if entry.sample_missing_fingerprints:
            lines.append(
                "  sample_missing_fingerprints: "
                + ", ".join(entry.sample_missing_fingerprints)
            )
        lines.append(f"  health_would_count: {entry.health_would_count}")
        lines.append(f"  probable_reason: {entry.probable_reason}")
        lines.append("")

    flagged = [e for e in report.entries if e.probable_reason != REASON_OK]
    lines.append(
        f"summary: {len(flagged)} flagged / {len(report.entries)} shown "
        f"(OK={len(report.entries) - len(flagged)})"
    )
    return "\n".join(lines)


def diagnose_processed_report_json(report: DiagnoseProcessedReport) -> str:
    return json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2)
