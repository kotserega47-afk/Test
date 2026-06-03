"""Best-effort cumulative Wallet Editor results registry in Dropbox."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path

import pandas as pd

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.dropbox_watcher import (
    download_file_with_rev,
    upload_file_if_rev,
)
from integrations.telegram_bot import send_message_sync
from integrations.wallet_editor_registry_lifecycle import (
    build_runs_row,
    load_warned_partners,
    mark_run_processed,
    partners_to_warn,
    recalculate_all_results,
    rows_from_result_excel,
    run_id_already_processed,
    save_warned_partners,
    sync_warned_partners_after_otlezka,
)
from integrations.wallet_editor_registry_settings import (
    RegistrySettings,
    load_registry_settings,
)
from integrations.wallet_editor_registry_xlsx import (
    card_as_text,
    load_registry_frames,
    save_registry_workbook,
)
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

ENV_DROPBOX_WALLET_EDITOR_PATH = "DROPBOX_WALLET_EDITOR_PATH"
CONVERSION_OPERATOR_PROFILE = "CONVERSION_AUTO"
SOURCE_CONVERSION_AUTO = "conversion_auto"
SOURCE_TELEGRAM_MANUAL = "telegram_manual"

REV_CONFLICT_MESSAGE = (
    "⚠️ Wallet Editor registry не обновлён\n\n"
    "Файл:\nwallet_editor.xlsx\n\n"
    "Причина:\nфайл был изменён пользователем во время записи\n\n"
    "Wallet Editor результат отправлен отдельно."
)

SLOW_APPEND_MESSAGE = (
    "⚠️ Wallet Editor registry долго обновляется\n\n"
    "Файл:\nwallet_editor.xlsx\n\n"
    "Возможная причина:\nфайл открыт или редактируется пользователем.\n\n"
    "Ожидание продолжается."
)

TIMEOUT_MESSAGE = (
    "⚠️ Wallet Editor registry не обновлён\n\n"
    "Файл:\nwallet_editor.xlsx\n\n"
    "Причина:\nпревышено время ожидания обновления.\n\n"
    "Результат Wallet Editor отправлен отдельно."
)

_lock = threading.Lock()


class _AppendOutcome(str, Enum):
    SUCCESS = "success"
    DUPLICATE = "duplicate"
    REV_CONFLICT = "rev_conflict"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


def wallet_editor_dropbox_path() -> str | None:
    path = os.getenv(ENV_DROPBOX_WALLET_EDITOR_PATH, "").strip()
    return path or None


def resolve_source(operator_profile: str) -> str:
    if (operator_profile or "").strip().upper() == CONVERSION_OPERATOR_PROFILE:
        return SOURCE_CONVERSION_AUTO
    return SOURCE_TELEGRAM_MANUAL


def _send_chat_warning(chat_id: int, text: str) -> None:
    try:
        send_message_sync(text, chat_id=str(chat_id))
    except Exception:
        log.exception("[WalletEditorRegistry] failed to send warning")


def _send_rev_conflict_warning(chat_id: int) -> None:
    _send_chat_warning(chat_id, REV_CONFLICT_MESSAGE)


def _send_slow_append_warning(chat_id: int) -> None:
    _send_chat_warning(chat_id, SLOW_APPEND_MESSAGE)


def _send_timeout_warning(chat_id: int) -> None:
    _send_chat_warning(chat_id, TIMEOUT_MESSAGE)


def _send_missing_otlezka_warnings(chat_id: int, partners: list[str]) -> None:
    from integrations.wallet_editor_registry_lifecycle import WARN_MESSAGE_TEMPLATE

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
    settings: RegistrySettings | None = None,
) -> None:
    """
    Download registry from Dropbox, append this run, upload back.
    Best-effort: never raises; logs errors only.
    Retries transient/rev conflicts until registry_timeout_seconds.
    """
    dropbox_path = wallet_editor_dropbox_path()
    if not dropbox_path:
        log.info(
            "[WalletEditorRegistry] skipped: %s not set",
            ENV_DROPBOX_WALLET_EDITOR_PATH,
        )
        return

    if not os.path.isfile(result_path):
        log.error(
            "[WalletEditorRegistry] result file missing run_id=%s path=%s",
            task.run_id,
            result_path,
        )
        return

    settings = settings or load_registry_settings()
    started = time.monotonic()
    deadline = started + settings.registry_timeout_seconds
    slow_warning_sent = False

    try:
        while time.monotonic() < deadline:
            elapsed = time.monotonic() - started
            if not slow_warning_sent and elapsed >= settings.registry_warning_seconds:
                _send_slow_append_warning(task.chat_id)
                slow_warning_sent = True

            try:
                with _lock:
                    outcome = _append_attempt(
                        task,
                        result_path,
                        stats,
                        dropbox_path=dropbox_path,
                        run_started_at=run_started_at,
                        run_finished_at=run_finished_at,
                    )
            except Exception:
                log.exception(
                    "[WalletEditorRegistry] append attempt failed run_id=%s",
                    task.run_id,
                )
                outcome = _AppendOutcome.TRANSIENT

            if outcome in (_AppendOutcome.SUCCESS, _AppendOutcome.DUPLICATE):
                return

            if outcome == _AppendOutcome.PERMANENT:
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            sleep_for = min(settings.registry_retry_interval_seconds, remaining)
            if sleep_for > 0:
                time.sleep(sleep_for)

        log.warning(
            "[WalletEditorRegistry] timeout after %ss run_id=%s path=%s",
            settings.registry_timeout_seconds,
            task.run_id,
            dropbox_path,
        )
        _send_timeout_warning(task.chat_id)
    except Exception:
        log.exception(
            "[WalletEditorRegistry] append failed run_id=%s profile=%s",
            task.run_id,
            task.operator_profile,
        )


def _append_attempt(
    task: WalletEditorTask,
    result_path: str,
    stats: Stats,
    *,
    dropbox_path: str,
    run_started_at: datetime,
    run_finished_at: datetime,
) -> _AppendOutcome:
    output_file = os.path.basename(result_path)

    with tempfile.TemporaryDirectory(prefix="we_registry_") as tmp:
        local_path = Path(tmp) / "wallet_editor.xlsx"
        status, download_rev = download_file_with_rev(dropbox_path, str(local_path))

        if status == "error":
            log.error(
                "[WalletEditorRegistry] download failed path=%s status=%s",
                dropbox_path,
                status,
            )
            return _AppendOutcome.TRANSIENT

        is_new_file = status == "not_found"
        all_results_df, runs_df, hold_df, otlezka_df, hold_exists, otlezka_exists = (
            load_registry_frames(local_path, status)
        )

        if run_id_already_processed(task.run_id, runs_df):
            log.info(
                "[WalletEditorRegistry] skip duplicate run_id=%s path=%s",
                task.run_id,
                dropbox_path,
            )
            return _AppendOutcome.DUPLICATE

        result_df = pd.read_excel(
            result_path,
            engine="openpyxl",
            converters={"card": card_as_text},
        )
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

        save_registry_workbook(
            local_path,
            all_results=recalculated,
            runs=merged_runs,
            hold_exists=hold_exists,
            otlezka_exists=otlezka_exists,
            is_new_file=is_new_file,
        )

        expected_rev = None if is_new_file else download_rev
        upload_status = upload_file_if_rev(str(local_path), dropbox_path, expected_rev)
        if upload_status == "rev_conflict":
            log.warning(
                "[WalletEditorRegistry] upload skipped: rev conflict run_id=%s path=%s",
                task.run_id,
                dropbox_path,
            )
            return _AppendOutcome.REV_CONFLICT
        if upload_status != "uploaded":
            log.error(
                "[WalletEditorRegistry] upload failed run_id=%s path=%s",
                task.run_id,
                dropbox_path,
            )
            return _AppendOutcome.TRANSIENT

        mark_run_processed(task.run_id)
        _process_missing_otlezka_warnings(task, missing_partners, otlezka_df)

        log.info(
            "[WalletEditorRegistry] appended run_id=%s rows=%s path=%s",
            task.run_id,
            input_rows,
            dropbox_path,
        )
        return _AppendOutcome.SUCCESS
