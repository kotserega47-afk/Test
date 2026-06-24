"""Async PostgreSQL shadow-write after successful Excel registry commits."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from core.event_log import append_event
from integrations.wallet_editor_registry_db.config import mirror_enabled
from integrations.wallet_editor_registry_db.connection import connect
from integrations.wallet_editor_registry_db.mapping import map_all_results_row
from integrations.wallet_editor_registry_db.mirror_state import (
    record_mirror_failure,
    record_mirror_success,
)
from integrations.wallet_editor_registry_db.models import RegistryResultRow, RegistryRunRow
from integrations.wallet_editor_registry_db.store import PostgresRegistryStore
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)


@dataclass(frozen=True, slots=True)
class MirrorResultBatch:
    """Rows to mirror after Excel upload success."""

    results: tuple[RegistryResultRow, ...] = ()
    runs: tuple[RegistryRunRow, ...] = ()


def write_mirror_batch(batch: MirrorResultBatch) -> None:
    """Synchronous mirror write (used by worker thread and tests)."""
    if not batch.results and not batch.runs:
        return

    with connect(for_mirror=True) as conn:
        with conn.cursor() as cur:
            store = PostgresRegistryStore(cur)
            for run in batch.runs:
                store.upsert_run(run)
            for result in batch.results:
                store.upsert_result(result)


def schedule_mirror_batch(batch: MirrorResultBatch | None, *, operation: str) -> None:
    """
    Fire-and-forget mirror write when flag enabled.

    Must be called only after Excel upload success (I-REG-08).
    """
    if batch is None or (not batch.results and not batch.runs):
        return
    if not mirror_enabled():
        return

    def _run() -> None:
        try:
            write_mirror_batch(batch)
            record_mirror_success(operation=operation)
            log.info(
                "[WalletEditorRegistryMirror] success operation=%s runs=%s results=%s",
                operation,
                len(batch.runs),
                len(batch.results),
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            record_mirror_failure(operation=operation, error=error)
            append_event(
                type="wallet_editor_registry_mirror_failed",
                job_type="wallet_editor",
                payload={
                    "operation": operation,
                    "error": error,
                    "runs": len(batch.runs),
                    "results": len(batch.results),
                },
            )
            log.exception(
                "[WalletEditorRegistryMirror] failed operation=%s runs=%s results=%s",
                operation,
                len(batch.runs),
                len(batch.results),
            )

    thread = threading.Thread(
        target=_run,
        name=f"we-registry-mirror-{operation}",
        daemon=True,
    )
    thread.start()
    log.debug(
        "[WalletEditorRegistryMirror] scheduled operation=%s runs=%s results=%s",
        operation,
        len(batch.runs),
        len(batch.results),
    )


def mirror_rows_for_patch(
    all_results,
    updates,
    *,
    find_row_index,
) -> MirrorResultBatch:
    """Build mirror batch for auto-enable patch rows."""
    rows: list[RegistryResultRow] = []
    for update in updates:
        idx = find_row_index(all_results, update)
        if idx is None:
            continue
        rows.append(
            map_all_results_row(
                all_results.loc[idx],
                source_row_index=int(idx),
            )
        )
    return MirrorResultBatch(results=tuple(rows))


def mirror_rows_for_refresh(before_df, after_df, *, row_changed) -> MirrorResultBatch:
    """Build mirror batch for lifecycle refresh changed rows."""
    rows: list[RegistryResultRow] = []
    for idx in before_df.index:
        if not row_changed(before_df, after_df, idx):
            continue
        rows.append(
            map_all_results_row(
                after_df.loc[idx],
                source_row_index=int(idx),
            )
        )
    return MirrorResultBatch(results=tuple(rows))
