"""Best-effort cumulative Wallet Editor results registry in Dropbox."""

from __future__ import annotations

import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from core.datetime_utils import EXCEL_DATETIME_FORMAT, ensure_aware_msk
from integrations.dropbox_watcher import download_file_status, upload_file
from integrations.telegram_bot import send_message_sync
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    HOLD_COLUMNS,
    OTLEZKA_COLUMNS,
    RUNS_COLUMNS,
    SHEET_HOLD,
    SHEET_OTLEZKA,
    WARN_MESSAGE_TEMPLATE,
    apply_missing_otlezka_red_fill,
    build_runs_row,
    load_warned_partners,
    mark_run_processed,
    migrate_legacy_runs,
    normalize_all_results,
    normalize_sheet,
    partners_to_warn,
    recalculate_all_results,
    rows_from_result_excel,
    run_id_already_processed,
    save_warned_partners,
    sync_warned_partners_after_otlezka,
)
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

ENV_DROPBOX_WALLET_EDITOR_PATH = "DROPBOX_WALLET_EDITOR_PATH"
CONVERSION_OPERATOR_PROFILE = "CONVERSION_AUTO"
SOURCE_CONVERSION_AUTO = "conversion_auto"
SOURCE_TELEGRAM_MANUAL = "telegram_manual"

SHEET_ALL_RESULTS = "all_results"
SHEET_RUNS = "runs"

_lock = threading.Lock()


def wallet_editor_dropbox_path() -> str | None:
    path = os.getenv(ENV_DROPBOX_WALLET_EDITOR_PATH, "").strip()
    return path or None


def resolve_source(operator_profile: str) -> str:
    if (operator_profile or "").strip().upper() == CONVERSION_OPERATOR_PROFILE:
        return SOURCE_CONVERSION_AUTO
    return SOURCE_TELEGRAM_MANUAL


def _load_workbook(
    local_path: Path,
    download_status: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if download_status == "not_found":
        return (
            normalize_sheet(pd.DataFrame(), ALL_RESULTS_COLUMNS),
            normalize_sheet(pd.DataFrame(), RUNS_COLUMNS),
            normalize_sheet(pd.DataFrame(), HOLD_COLUMNS),
            normalize_sheet(pd.DataFrame(), OTLEZKA_COLUMNS),
        )

    with pd.ExcelFile(local_path, engine="openpyxl") as book:
        all_df = (
            pd.read_excel(book, sheet_name=SHEET_ALL_RESULTS)
            if SHEET_ALL_RESULTS in book.sheet_names
            else pd.DataFrame()
        )
        runs_df = (
            pd.read_excel(book, sheet_name=SHEET_RUNS)
            if SHEET_RUNS in book.sheet_names
            else pd.DataFrame()
        )
        hold_df = (
            pd.read_excel(book, sheet_name=SHEET_HOLD)
            if SHEET_HOLD in book.sheet_names
            else pd.DataFrame()
        )
        otlezka_df = (
            pd.read_excel(book, sheet_name=SHEET_OTLEZKA)
            if SHEET_OTLEZKA in book.sheet_names
            else pd.DataFrame()
        )

    all_df = normalize_all_results(all_df)
    runs_df = migrate_legacy_runs(runs_df)
    hold_df = normalize_sheet(hold_df, HOLD_COLUMNS)
    otlezka_df = normalize_sheet(otlezka_df, OTLEZKA_COLUMNS)
    return all_df, runs_df, hold_df, otlezka_df


def _save_workbook(
    local_path: Path,
    all_results: pd.DataFrame,
    runs: pd.DataFrame,
    hold: pd.DataFrame,
    otlezka: pd.DataFrame,
) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(local_path, engine="openpyxl") as writer:
        normalize_sheet(all_results, ALL_RESULTS_COLUMNS).to_excel(
            writer, sheet_name=SHEET_ALL_RESULTS, index=False
        )
        normalize_sheet(runs, RUNS_COLUMNS).to_excel(writer, sheet_name=SHEET_RUNS, index=False)
        normalize_sheet(hold, HOLD_COLUMNS).to_excel(writer, sheet_name=SHEET_HOLD, index=False)
        normalize_sheet(otlezka, OTLEZKA_COLUMNS).to_excel(
            writer, sheet_name=SHEET_OTLEZKA, index=False
        )
    apply_missing_otlezka_red_fill(local_path)


def _send_missing_otlezka_warnings(chat_id: int, partners: list[str]) -> None:
    for partner in partners:
        try:
            send_message_sync(
                WARN_MESSAGE_TEMPLATE.format(partner=partner),
                chat_id=str(chat_id),
            )
        except Exception:
            log.exception(
                "[WalletEditorRegistry] failed to send missing otlezka warning partner=%s",
                partner,
            )


def _process_missing_otlezka_warnings(
    task: WalletEditorTask,
    missing_partners: set[str],
    otlezka_df: pd.DataFrame,
) -> None:
    warned = load_warned_partners()
    warned = sync_warned_partners_after_otlezka(otlezka_df, warned)
    if not missing_partners:
        return
    to_warn = partners_to_warn(missing_partners, warned)
    if not to_warn:
        return
    _send_missing_otlezka_warnings(task.chat_id, to_warn)
    for partner in to_warn:
        warned.add(partner.casefold())
    save_warned_partners(warned)


def append_run_to_dropbox_registry(
    task: WalletEditorTask,
    result_path: str,
    stats: Stats,
    *,
    run_started_at: datetime,
    run_finished_at: datetime,
) -> None:
    """
    Download registry from Dropbox, append this run, upload back.
    Best-effort: never raises; logs errors only.
    """
    dropbox_path = wallet_editor_dropbox_path()
    if not dropbox_path:
        log.info(
            "[WalletEditorRegistry] skipped: %s not set",
            ENV_DROPBOX_WALLET_EDITOR_PATH,
        )
        return

    try:
        with _lock:
            _append_under_lock(
                task,
                result_path,
                stats,
                dropbox_path=dropbox_path,
                run_started_at=run_started_at,
                run_finished_at=run_finished_at,
            )
    except Exception:
        log.exception(
            "[WalletEditorRegistry] append failed run_id=%s profile=%s",
            task.run_id,
            task.operator_profile,
        )


def _append_under_lock(
    task: WalletEditorTask,
    result_path: str,
    stats: Stats,
    *,
    dropbox_path: str,
    run_started_at: datetime,
    run_finished_at: datetime,
) -> None:
    output_file = os.path.basename(result_path)

    with tempfile.TemporaryDirectory(prefix="we_registry_") as tmp:
        local_path = Path(tmp) / "wallet_editor.xlsx"
        status = download_file_status(dropbox_path, str(local_path))

        if status == "error":
            log.error(
                "[WalletEditorRegistry] download failed path=%s status=%s",
                dropbox_path,
                status,
            )
            return

        all_results_df, runs_df, hold_df, otlezka_df = _load_workbook(local_path, status)

        if run_id_already_processed(task.run_id, runs_df):
            log.info(
                "[WalletEditorRegistry] skip duplicate run_id=%s path=%s",
                task.run_id,
                dropbox_path,
            )
            return

        result_df = pd.read_excel(result_path, engine="openpyxl")
        input_rows = len(result_df)
        new_rows = rows_from_result_excel(result_df)

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
            output_file=output_file,
        )
        merged_runs = pd.concat([runs_df, new_run], ignore_index=True)

        _save_workbook(local_path, recalculated, merged_runs, hold_df, otlezka_df)

        if not upload_file(str(local_path), dropbox_path):
            log.error(
                "[WalletEditorRegistry] upload failed run_id=%s path=%s",
                task.run_id,
                dropbox_path,
            )
            return

        mark_run_processed(task.run_id)
        _process_missing_otlezka_warnings(task, missing_partners, otlezka_df)

        log.info(
            "[WalletEditorRegistry] appended run_id=%s rows=%s path=%s",
            task.run_id,
            input_rows,
            dropbox_path,
        )
