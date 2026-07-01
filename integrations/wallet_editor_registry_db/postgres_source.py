"""Postgres-first registry write path (history source of truth)."""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING, Sequence

import pandas as pd

from automation.audit import Stats
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError, connect
from integrations.wallet_editor_registry_db.frames import (
    load_registry_frames_from_postgres,
    postgres_run_exists,
)
from integrations.wallet_editor_registry_db.manual_readers import (
    ManualReadersNotReadyError,
    load_hold_otlezka_for_runtime,
)
from integrations.wallet_editor_registry_db.mapping import map_all_results_row, map_runs_row
from integrations.wallet_editor_registry_db.mirror import MirrorResultBatch
from integrations.wallet_editor_registry_db.store import PostgresRegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    build_runs_row,
    filter_rows_not_in_registry,
    load_processed_run_ids,
    mark_run_processed,
    missing_result_fingerprints,
    normalize_all_results,
    recalculate_all_results,
    rows_from_result_excel,
    run_id_already_processed,
)
from integrations.wallet_editor_registry_xlsx import card_as_text
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

if TYPE_CHECKING:
    from automation.runtime import WalletEditorTask
    from integrations.wallet_editor_registry import EnableRegistryUpdate, _AppendOutcome, _PatchOutcome

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

RESULT_UPSERT_BATCH = 1000


def _persist_full_registry(
    recalculated: pd.DataFrame,
    runs: pd.DataFrame,
    *,
    new_run: pd.DataFrame | None,
    run_id: str,
    operator_profile: str | None,
    source: str | None,
    new_row_start_index: int,
) -> None:
    recalculated = normalize_all_results(recalculated)
    with connect(for_mirror=False) as conn:
        with conn.cursor() as cur:
            store = PostgresRegistryStore(cur)
            if new_run is not None and not new_run.empty:
                store.upsert_run(
                    map_runs_row(
                        new_run.iloc[0],
                        run_id=run_id,
                        operator_profile=operator_profile,
                        source=source,
                    )
                )

            batch = []
            for index in recalculated.index:
                assigned_run_id = run_id if int(index) >= new_row_start_index else None
                batch.append(
                    map_all_results_row(
                        recalculated.loc[index],
                        run_id=assigned_run_id,
                        source_row_index=int(index),
                    )
                )
                if len(batch) >= RESULT_UPSERT_BATCH:
                    store.upsert_results_batch(batch)
                    conn.commit()
                    batch = []
            if batch:
                store.upsert_results_batch(batch)
            conn.commit()


def append_attempt_postgres(
    task: "WalletEditorTask",
    result_path: str,
    stats: Stats,
    *,
    dropbox_path: str,
    run_started_at: datetime,
    run_finished_at: datetime,
    output_file: str | None,
    resolve_source,
    process_missing_otlezka_warnings,
) -> tuple["_AppendOutcome", str | None, MirrorResultBatch | None]:
    from integrations.wallet_editor_registry import _AppendOutcome

    try:
        if not os.getenv("DATABASE_URL", "").strip():
            raise DatabaseNotConfiguredError("DATABASE_URL is not set")
    except DatabaseNotConfiguredError:
        log.error("[WalletEditorRegistry] postgres append blocked: DATABASE_URL missing")
        return _AppendOutcome.PERMANENT, None, None

    runs_output_file = output_file or os.path.basename(result_path)

    try:
        all_results_df, runs_df = load_registry_frames_from_postgres()
        hold_df, otlezka_df, _hold_exists, _otlezka_exists = load_hold_otlezka_for_runtime(
            dropbox_path,
        )
    except ManualReadersNotReadyError as exc:
        log.error("[WalletEditorRegistry] postgres append blocked: %s", exc)
        return _AppendOutcome.PERMANENT, str(exc), None
    except Exception:
        log.exception("[WalletEditorRegistry] postgres append load failed run_id=%s", task.run_id)
        return _AppendOutcome.TRANSIENT, None, None

    if postgres_run_exists(task.run_id) or run_id_already_processed(
        task.run_id,
        runs_df,
        result_path=result_path,
        all_results_df=all_results_df,
    ):
        log.info(
            "[WalletEditorRegistry] postgres skip duplicate run_id=%s",
            task.run_id,
        )
        return _AppendOutcome.DUPLICATE, None, None

    repair_mode = task.run_id in load_processed_run_ids() and bool(
        missing_result_fingerprints(result_path, all_results_df)
    )
    if repair_mode:
        log.warning(
            "[WalletEditorRegistry] postgres repair re-append run_id=%s",
            task.run_id,
        )

    result_df = pd.read_excel(
        result_path,
        engine="openpyxl",
        converters={"card": card_as_text},
    )
    input_rows = len(result_df)
    new_rows = rows_from_result_excel(result_df)
    if repair_mode:
        new_rows = filter_rows_not_in_registry(new_rows, all_results_df)
        if new_rows.empty:
            return _AppendOutcome.DUPLICATE, None, None

    merged_all = pd.concat([all_results_df, new_rows], ignore_index=True)
    recalculated, missing_partners = recalculate_all_results(
        merged_all,
        hold_df,
        otlezka_df,
    )

    new_run = build_runs_row(
        started_at=run_started_at,
        finished_at=run_finished_at,
        input_rows=input_rows,
        stats_ok=stats.ok,
        stats_fail=stats.fail,
        stats_skip=stats.skip,
        output_file=runs_output_file,
    )
    merged_runs = pd.concat([runs_df, new_run], ignore_index=True)
    new_row_start_index = len(all_results_df)

    try:
        _persist_full_registry(
            recalculated,
            merged_runs,
            new_run=new_run,
            run_id=task.run_id,
            operator_profile=task.operator_profile,
            source=resolve_source(task.operator_profile),
            new_row_start_index=new_row_start_index,
        )
    except Exception:
        log.exception("[WalletEditorRegistry] postgres append commit failed run_id=%s", task.run_id)
        return _AppendOutcome.TRANSIENT, None, None

    mark_run_processed(task.run_id)
    process_missing_otlezka_warnings(task, missing_partners, otlezka_df)

    start_idx = new_row_start_index
    result_rows = tuple(
        map_all_results_row(
            recalculated.iloc[i],
            run_id=task.run_id,
            source_row_index=i,
        )
        for i in range(start_idx, len(recalculated))
    )
    run_row = map_runs_row(
        new_run.iloc[0],
        run_id=task.run_id,
        operator_profile=task.operator_profile,
        source=resolve_source(task.operator_profile),
    )
    mirror_batch = MirrorResultBatch(results=result_rows, runs=(run_row,))

    log.info(
        "[WalletEditorRegistry] postgres appended run_id=%s rows=%s",
        task.run_id,
        len(new_rows),
    )
    return _AppendOutcome.SUCCESS, None, mirror_batch


def patch_attempt_postgres(
    updates: Sequence["EnableRegistryUpdate"],
    *,
    dropbox_path: str,
    apply_enable_updates_to_all_results,
    find_enable_patch_row_index,
) -> tuple["_PatchOutcome", int, str | None, MirrorResultBatch | None]:
    from integrations.wallet_editor_registry_db.mirror import mirror_rows_for_patch
    from integrations.wallet_editor_registry import _PatchOutcome

    try:
        if not os.getenv("DATABASE_URL", "").strip():
            raise DatabaseNotConfiguredError("DATABASE_URL is not set")
    except DatabaseNotConfiguredError:
        return _PatchOutcome.PERMANENT, 0, "DATABASE_URL is not set", None

    try:
        all_results_df, runs_df = load_registry_frames_from_postgres()
        hold_df, otlezka_df, _hold_exists, _otlezka_exists = load_hold_otlezka_for_runtime(
            dropbox_path,
        )
    except ManualReadersNotReadyError as exc:
        return _PatchOutcome.PERMANENT, 0, str(exc), None
    except Exception:
        log.exception("[WalletEditorRegistry] postgres patch load failed")
        return _PatchOutcome.TRANSIENT, 0, "postgres load failed", None

    if all_results_df.empty:
        return _PatchOutcome.PERMANENT, 0, "registry empty in postgres", None

    patched_df, patched_count = apply_enable_updates_to_all_results(
        all_results_df,
        updates,
    )
    recalculated, _missing_partners = recalculate_all_results(
        patched_df,
        hold_df,
        otlezka_df,
    )

    try:
        with connect(for_mirror=False) as conn:
            with conn.cursor() as cur:
                store = PostgresRegistryStore(cur)
                batch = []
                for index in recalculated.index:
                    batch.append(
                        map_all_results_row(
                            recalculated.loc[index],
                            source_row_index=int(index),
                        )
                    )
                    if len(batch) >= RESULT_UPSERT_BATCH:
                        store.upsert_results_batch(batch)
                        conn.commit()
                        batch = []
                if batch:
                    store.upsert_results_batch(batch)
                    conn.commit()
    except Exception:
        log.exception("[WalletEditorRegistry] postgres patch commit failed")
        return _PatchOutcome.TRANSIENT, 0, "postgres commit failed", None

    mirror_batch = mirror_rows_for_patch(
        recalculated,
        updates,
        find_row_index=find_enable_patch_row_index,
    )
    log.info(
        "[WalletEditorRegistry] postgres patch applied patched=%s requested=%s",
        patched_count,
        len(updates),
    )
    return _PatchOutcome.SUCCESS, patched_count, None, mirror_batch


def refresh_attempt_postgres(
    *,
    dropbox_path: str,
    today,
    normalize_all_results,
    recalculate_all_results,
    compute_lifecycle_diff,
    lifecycle_row_changed,
) -> tuple:
    from integrations.wallet_editor_registry_db.mirror import mirror_rows_for_refresh
    from integrations.wallet_editor_registry_refresh import (
        RefreshBreakdown,
        _RefreshOutcome,
    )

    try:
        all_results_df, _runs_df = load_registry_frames_from_postgres()
        hold_df, otlezka_df, _hold_exists, _otlezka_exists = load_hold_otlezka_for_runtime(
            dropbox_path,
        )
    except ManualReadersNotReadyError as exc:
        return _RefreshOutcome.PERMANENT, 0, RefreshBreakdown(), str(exc), None
    except Exception:
        log.exception("[WalletEditorRegistry] postgres refresh load failed")
        return _RefreshOutcome.TRANSIENT, 0, RefreshBreakdown(), "postgres load failed", None

    before_df = normalize_all_results(all_results_df)
    recalculated, _missing = recalculate_all_results(
        before_df,
        hold_df,
        otlezka_df,
        today=today,
    )
    changed_rows, breakdown = compute_lifecycle_diff(before_df, recalculated)
    if changed_rows == 0:
        return _RefreshOutcome.SKIPPED, 0, breakdown, None, None

    try:
        with connect(for_mirror=False) as conn:
            with conn.cursor() as cur:
                store = PostgresRegistryStore(cur)
                batch = []
                for index in recalculated.index:
                    batch.append(
                        map_all_results_row(
                            recalculated.loc[index],
                            source_row_index=int(index),
                        )
                    )
                    if len(batch) >= RESULT_UPSERT_BATCH:
                        store.upsert_results_batch(batch)
                        conn.commit()
                        batch = []
                if batch:
                    store.upsert_results_batch(batch)
                    conn.commit()
    except Exception:
        log.exception("[WalletEditorRegistry] postgres refresh commit failed")
        return _RefreshOutcome.TRANSIENT, changed_rows, breakdown, "postgres commit failed", None

    mirror_batch = mirror_rows_for_refresh(
        before_df,
        recalculated,
        row_changed=lifecycle_row_changed,
    )
    return _RefreshOutcome.SUCCESS, changed_rows, breakdown, None, mirror_batch
