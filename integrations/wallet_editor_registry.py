"""Best-effort cumulative Wallet Editor results registry in Dropbox."""

from __future__ import annotations

import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from core.datetime_utils import EXCEL_DATETIME_FORMAT, ensure_aware_msk
from integrations.dropbox_watcher import download_file_status, upload_file
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

ALL_RESULTS_COLUMNS = [
    "run_id",
    "run_started_at",
    "run_finished_at",
    "operator_profile",
    "source",
    "input_file",
    "output_file",
    "telegram_chat_id",
    "telegram_user_id",
    "row_index",
    "Дата отключения",
    "card",
    "action",
    "value",
    "status",
    "comment",
]

RUNS_COLUMNS = [
    "run_id",
    "started_at",
    "finished_at",
    "source",
    "operator_profile",
    "input_rows",
    "success_rows",
    "failed_rows",
    "skipped_rows",
    "output_file",
    "telegram_chat_id",
    "telegram_user_id",
]

RESULT_ROW_COLUMNS = ["Дата отключения", "card", "action", "value", "status", "comment"]

_lock = threading.Lock()


def wallet_editor_dropbox_path() -> str | None:
    path = os.getenv(ENV_DROPBOX_WALLET_EDITOR_PATH, "").strip()
    return path or None


def resolve_source(operator_profile: str) -> str:
    if (operator_profile or "").strip().upper() == CONVERSION_OPERATOR_PROFILE:
        return SOURCE_CONVERSION_AUTO
    return SOURCE_TELEGRAM_MANUAL


def new_run_id() -> str:
    return uuid4().hex


def _format_dt(dt: datetime) -> str:
    return ensure_aware_msk(dt).strftime(EXCEL_DATETIME_FORMAT)


def _cell_str(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.strftime(EXCEL_DATETIME_FORMAT)
        return ensure_aware_msk(value).strftime(EXCEL_DATETIME_FORMAT)
    return str(value).strip()


def _empty_sheet(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _normalize_sheet(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_sheet(columns)
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    return out[columns]


def _load_workbook(local_path: Path, download_status: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if download_status == "not_found":
        return _empty_sheet(ALL_RESULTS_COLUMNS), _empty_sheet(RUNS_COLUMNS)

    with pd.ExcelFile(local_path, engine="openpyxl") as book:
        all_df = (
            pd.read_excel(book, sheet_name=SHEET_ALL_RESULTS)
            if SHEET_ALL_RESULTS in book.sheet_names
            else _empty_sheet(ALL_RESULTS_COLUMNS)
        )
        runs_df = (
            pd.read_excel(book, sheet_name=SHEET_RUNS)
            if SHEET_RUNS in book.sheet_names
            else _empty_sheet(RUNS_COLUMNS)
        )
    return _normalize_sheet(all_df, ALL_RESULTS_COLUMNS), _normalize_sheet(runs_df, RUNS_COLUMNS)


def _run_id_exists(runs_df: pd.DataFrame, run_id: str) -> bool:
    if runs_df.empty:
        return False
    return run_id in runs_df["run_id"].astype(str).tolist()


def _save_workbook(
    local_path: Path,
    all_results: pd.DataFrame,
    runs: pd.DataFrame,
) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(local_path, engine="openpyxl") as writer:
        _normalize_sheet(all_results, ALL_RESULTS_COLUMNS).to_excel(
            writer, sheet_name=SHEET_ALL_RESULTS, index=False
        )
        _normalize_sheet(runs, RUNS_COLUMNS).to_excel(writer, sheet_name=SHEET_RUNS, index=False)


def _build_all_results_rows(
    task: WalletEditorTask,
    result_df: pd.DataFrame,
    *,
    source: str,
    output_file: str,
    run_started_at: datetime,
    run_finished_at: datetime,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    started_s = _format_dt(run_started_at)
    finished_s = _format_dt(run_finished_at)

    for row_index, (_, row) in enumerate(result_df.iterrows()):
        entry: dict[str, object] = {
            "run_id": task.run_id,
            "run_started_at": started_s,
            "run_finished_at": finished_s,
            "operator_profile": task.operator_profile,
            "source": source,
            "input_file": task.source_file_name,
            "output_file": output_file,
            "telegram_chat_id": task.chat_id,
            "telegram_user_id": task.telegram_user_id,
            "row_index": row_index,
        }
        for col in RESULT_ROW_COLUMNS:
            entry[col] = _cell_str(row[col]) if col in result_df.columns else ""
        rows.append(entry)

    if not rows:
        return _empty_sheet(ALL_RESULTS_COLUMNS)
    return pd.DataFrame(rows, columns=ALL_RESULTS_COLUMNS)


def _build_runs_row(
    task: WalletEditorTask,
    stats: Stats,
    *,
    source: str,
    output_file: str,
    input_rows: int,
    run_started_at: datetime,
    run_finished_at: datetime,
) -> pd.DataFrame:
    row = {
        "run_id": task.run_id,
        "started_at": _format_dt(run_started_at),
        "finished_at": _format_dt(run_finished_at),
        "source": source,
        "operator_profile": task.operator_profile,
        "input_rows": input_rows,
        "success_rows": stats.ok,
        "failed_rows": stats.fail,
        "skipped_rows": stats.skip,
        "output_file": output_file,
        "telegram_chat_id": task.chat_id,
        "telegram_user_id": task.telegram_user_id,
    }
    return pd.DataFrame([row], columns=RUNS_COLUMNS)


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
    source = resolve_source(task.operator_profile)
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

        all_results_df, runs_df = _load_workbook(local_path, status)

        if _run_id_exists(runs_df, task.run_id):
            log.info(
                "[WalletEditorRegistry] skip duplicate run_id=%s path=%s",
                task.run_id,
                dropbox_path,
            )
            return

        result_df = pd.read_excel(result_path, engine="openpyxl")
        input_rows = len(result_df)
        new_all = _build_all_results_rows(
            task,
            result_df,
            source=source,
            output_file=output_file,
            run_started_at=run_started_at,
            run_finished_at=run_finished_at,
        )
        new_run = _build_runs_row(
            task,
            stats,
            source=source,
            output_file=output_file,
            input_rows=input_rows,
            run_started_at=run_started_at,
            run_finished_at=run_finished_at,
        )

        merged_all = pd.concat([all_results_df, new_all], ignore_index=True)
        merged_runs = pd.concat([runs_df, new_run], ignore_index=True)
        _save_workbook(local_path, merged_all, merged_runs)

        if not upload_file(str(local_path), dropbox_path):
            log.error(
                "[WalletEditorRegistry] upload failed run_id=%s path=%s",
                task.run_id,
                dropbox_path,
            )
            return

        log.info(
            "[WalletEditorRegistry] appended run_id=%s rows=%s path=%s",
            task.run_id,
            input_rows,
            dropbox_path,
        )
