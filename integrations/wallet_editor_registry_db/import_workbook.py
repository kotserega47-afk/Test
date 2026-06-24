"""One-shot import of wallet_editor.xlsx into PostgreSQL mirror tables."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from zipfile import BadZipFile

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from integrations.wallet_editor_registry_db.config import get_database_url
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError, connect
from integrations.wallet_editor_registry_db.mapping import map_all_results_row, map_runs_row
from integrations.wallet_editor_registry_db.models import RegistryResultRow, RegistryRunRow
from integrations.wallet_editor_registry_db.store import (
    InMemoryRegistryStore,
    PostgresRegistryStore,
    RegistryStore,
)
from integrations.wallet_editor_registry_lifecycle import (
    SHEET_ALL_RESULTS,
    SHEET_RUNS,
    _cell_str,
    normalize_all_results,
)
from integrations.wallet_editor_registry_xlsx import load_registry_frames
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)


class ImportWorkbookError(RuntimeError):
    """Raised when the registry workbook cannot be parsed for import."""


@dataclass(frozen=True, slots=True)
class ImportStats:
    runs_inserted: int = 0
    runs_skipped: int = 0
    results_inserted: int = 0
    results_updated: int = 0
    results_skipped: int = 0
    results_errors: int = 0
    error_messages: tuple[str, ...] = ()

    @property
    def runs_total(self) -> int:
        return self.runs_inserted + self.runs_skipped

    @property
    def results_total(self) -> int:
        return (
            self.results_inserted
            + self.results_updated
            + self.results_skipped
            + self.results_errors
        )


def synthetic_run_id(row: pd.Series | dict[str, object], *, index: int) -> str:
    """Stable run_id for legacy runs rows without run_id column."""
    if isinstance(row, dict):
        get = row.get
    else:
        get = row.get
    parts = [
        _cell_str(get("started_at", "")),
        _cell_str(get("finished_at", "")),
        _cell_str(get("output_file", "")),
        _cell_str(get("input_rows", "")),
        _cell_str(get("success_rows", "")),
        _cell_str(get("failed_rows", "")),
        _cell_str(get("skipped_rows", "")),
        str(index),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"import-run-{digest}"


def resolve_run_id(row: pd.Series, *, index: int) -> str:
    legacy = _cell_str(row.get("run_id", ""))
    if legacy:
        return legacy
    return synthetic_run_id(row, index=index)


def validate_registry_workbook(path: Path) -> None:
    """Raise ImportWorkbookError when workbook is not a valid registry xlsx."""
    if not path.is_file():
        raise ImportWorkbookError(f"registry workbook not found: {path}")

    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except (InvalidFileException, BadZipFile, OSError) as exc:
        raise ImportWorkbookError(f"invalid registry workbook (not xlsx): {path}") from exc
    except Exception as exc:
        raise ImportWorkbookError(f"failed to open registry workbook: {path}: {exc}") from exc

    try:
        if SHEET_ALL_RESULTS not in wb.sheetnames and SHEET_RUNS not in wb.sheetnames:
            raise ImportWorkbookError(
                f"registry workbook missing required sheets "
                f"({SHEET_ALL_RESULTS!r} or {SHEET_RUNS!r}): {path}"
            )
    finally:
        wb.close()


def load_registry_dataframes(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load normalized all_results and runs frames from a local workbook."""
    validate_registry_workbook(path)
    all_df, runs_df, _hold_df, _otlezka_df, _hold_exists, _otlezka_exists = (
        load_registry_frames(path, "ok")
    )
    return normalize_all_results(all_df), runs_df


IMPORT_COMMIT_BATCH_SIZE = 500
IMPORT_RESULT_BATCH_SIZE = 1000


def _import_registry_frames_postgres_batched(
    store: PostgresRegistryStore,
    all_results: pd.DataFrame,
    runs: pd.DataFrame,
    *,
    commit_callback: Callable[[], None],
    batch_size: int = IMPORT_RESULT_BATCH_SIZE,
) -> ImportStats:
    """Bulk import for PostgreSQL (fewer round-trips over remote connections)."""
    errors: list[str] = []
    run_rows: list[RegistryRunRow] = []

    for index in runs.index:
        row = runs.loc[index]
        try:
            run_rows.append(
                map_runs_row(
                    row,
                    run_id=resolve_run_id(row, index=int(index)),
                    operator_profile=_cell_str(row.get("operator_profile", "")) or None,
                    source=_cell_str(row.get("source", "")) or None,
                )
            )
        except ValueError as exc:
            errors.append(f"runs row {index}: {exc}")

    store.upsert_runs_batch(run_rows)
    commit_callback()
    log.info("[WalletEditorRegistryImport] runs batch upserted count=%s", len(run_rows))

    result_rows: list[RegistryResultRow] = []
    results_errors = 0
    results_processed = 0

    for index in all_results.index:
        row = all_results.loc[index]
        legacy_run_id = _cell_str(row.get("run_id", "")) or None
        try:
            result_rows.append(
                map_all_results_row(
                    row,
                    run_id=legacy_run_id,
                    source_row_index=int(index),
                )
            )
        except ValueError as exc:
            results_errors += 1
            errors.append(f"all_results row {index}: {exc}")
            continue

        if len(result_rows) >= batch_size:
            store.upsert_results_batch(result_rows)
            commit_callback()
            results_processed += len(result_rows)
            log.info(
                "[WalletEditorRegistryImport] results progress processed=%s",
                results_processed,
            )
            result_rows = []

    if result_rows:
        store.upsert_results_batch(result_rows)
        commit_callback()
        results_processed += len(result_rows)

    return ImportStats(
        runs_inserted=len(run_rows),
        runs_skipped=0,
        results_inserted=results_processed,
        results_updated=0,
        results_skipped=0,
        results_errors=results_errors,
        error_messages=tuple(errors),
    )


def import_registry_frames(
    all_results: pd.DataFrame,
    runs: pd.DataFrame,
    store: RegistryStore,
    *,
    commit_callback: Callable[[], None] | None = None,
    commit_batch_size: int = IMPORT_COMMIT_BATCH_SIZE,
) -> ImportStats:
    """Import all_results and runs into a registry store."""
    runs_inserted = runs_skipped = 0
    results_inserted = results_updated = results_skipped = results_errors = 0
    errors: list[str] = []
    rows_since_commit = 0

    def maybe_commit() -> None:
        nonlocal rows_since_commit
        if commit_callback is None:
            return
        rows_since_commit += 1
        if rows_since_commit >= commit_batch_size:
            commit_callback()
            rows_since_commit = 0

    for index in runs.index:
        row = runs.loc[index]
        try:
            mapped = map_runs_row(
                row,
                run_id=resolve_run_id(row, index=int(index)),
                operator_profile=_cell_str(row.get("operator_profile", "")) or None,
                source=_cell_str(row.get("source", "")) or None,
            )
        except ValueError as exc:
            errors.append(f"runs row {index}: {exc}")
            continue

        if store.upsert_run(mapped):
            runs_inserted += 1
        else:
            runs_skipped += 1
        maybe_commit()

    for index in all_results.index:
        row = all_results.loc[index]
        legacy_run_id = _cell_str(row.get("run_id", "")) or None
        try:
            mapped = map_all_results_row(
                row,
                run_id=legacy_run_id,
                source_row_index=int(index),
            )
        except ValueError as exc:
            results_errors += 1
            errors.append(f"all_results row {index}: {exc}")
            continue

        inserted = store.upsert_result(mapped)
        if inserted:
            results_inserted += 1
        else:
            # Postgres upsert updates existing fingerprint; in-memory store overwrites.
            if isinstance(store, InMemoryRegistryStore):
                results_skipped += 1
            else:
                results_updated += 1
        maybe_commit()

    if commit_callback is not None and rows_since_commit:
        commit_callback()

    return ImportStats(
        runs_inserted=runs_inserted,
        runs_skipped=runs_skipped,
        results_inserted=results_inserted,
        results_updated=results_updated,
        results_skipped=results_skipped,
        results_errors=results_errors,
        error_messages=tuple(errors),
    )


def import_registry_workbook(
    path: Path,
    *,
    connection: Any | None = None,
    store: RegistryStore | None = None,
) -> ImportStats:
    """
    Import local wallet_editor.xlsx into PostgreSQL.

    Does not require mirror flag (manual CLI). Requires DATABASE_URL unless
    *store* or *connection* is provided (tests).
    """
    all_results, runs = load_registry_dataframes(path)

    if store is not None:
        stats = import_registry_frames(all_results, runs, store)
        log.info(
            "[WalletEditorRegistryImport] path=%s runs_ins=%s runs_skip=%s "
            "results_ins=%s results_upd=%s results_err=%s",
            path,
            stats.runs_inserted,
            stats.runs_skipped,
            stats.results_inserted,
            stats.results_updated,
            stats.results_errors,
        )
        return stats

    if not get_database_url():
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not set; cannot import registry workbook into PostgreSQL"
        )

    if connection is not None:
        with connection.cursor() as cur:
            store = PostgresRegistryStore(cur)
            stats = _import_registry_frames_postgres_batched(
                store,
                all_results,
                runs,
                commit_callback=connection.commit,
            )
    else:
        with connect(for_mirror=False) as conn:
            with conn.cursor() as cur:
                store = PostgresRegistryStore(cur)
                stats = _import_registry_frames_postgres_batched(
                    store,
                    all_results,
                    runs,
                    commit_callback=conn.commit,
                )

    log.info(
        "[WalletEditorRegistryImport] path=%s runs_ins=%s runs_skip=%s "
        "results_ins=%s results_upd=%s results_err=%s",
        path,
        stats.runs_inserted,
        stats.runs_skipped,
        stats.results_inserted,
        stats.results_updated,
        stats.results_errors,
    )
    return stats


def import_registry_from_dropbox(
    dropbox_path: str,
    *,
    local_path: Path,
) -> ImportStats:
    """Download registry from Dropbox then import (mirror flag not required)."""
    from integrations.dropbox_watcher import download_file_with_rev

    status, _rev = download_file_with_rev(dropbox_path, str(local_path))
    if status == "not_found":
        raise ImportWorkbookError(f"registry workbook not found in Dropbox: {dropbox_path}")
    if status != "ok":
        raise ImportWorkbookError(f"registry workbook download failed: {dropbox_path} status={status}")
    return import_registry_workbook(local_path)
