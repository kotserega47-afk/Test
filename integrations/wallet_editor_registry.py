"""Best-effort cumulative Wallet Editor results registry in Dropbox."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Sequence

import pandas as pd

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.dropbox_watcher import (
    download_file_with_rev,
    upload_file_if_rev,
)
from integrations.telegram_bot import send_message_sync
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_REMOVE_PARTNER,
    build_runs_row,
    load_warned_partners,
    mark_run_processed,
    normalize_all_results,
    parse_disable_datetime,
    partners_to_warn,
    recalculate_all_results,
    rows_from_result_excel,
    run_id_already_processed,
    save_warned_partners,
    sync_warned_partners_after_otlezka,
    _cell_str,
    _normalize_key,
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


class _PatchOutcome(str, Enum):
    SUCCESS = "success"
    REV_CONFLICT = "rev_conflict"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass(frozen=True, slots=True)
class EnableRegistryUpdate:
    card: str
    partner: str
    disable_date: str
    vklyucheno: str
    comment: str
    source_row_index: int = -1


@dataclass(frozen=True, slots=True)
class EnablePatchResult:
    success: bool
    patched_count: int
    requested_count: int = 0
    error_reason: str | None = None


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
    output_file: str | None = None,
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
                        output_file=output_file,
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
    output_file: str | None = None,
) -> _AppendOutcome:
    runs_output_file = output_file or os.path.basename(result_path)

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
            output_file=runs_output_file,
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


def _disable_dates_equal(left: object, right: object) -> bool:
    left_dt = parse_disable_datetime(left)
    right_dt = parse_disable_datetime(right)
    if left_dt is not None and right_dt is not None:
        return left_dt == right_dt
    return _cell_str(left) == _cell_str(right)


def _find_enable_patch_row_index(
    df: pd.DataFrame,
    update: EnableRegistryUpdate,
) -> int | None:
    matches: list[int] = []
    for idx in df.index:
        row = df.loc[idx]
        if _cell_str(row.get("action", "")).lower() != ACTION_REMOVE_PARTNER:
            continue
        if _normalize_key(row.get("card", "")) != _normalize_key(update.card):
            continue
        if _normalize_key(row.get("partner", "")) != _normalize_key(update.partner):
            continue
        if not _disable_dates_equal(row.get("Дата отключения", ""), update.disable_date):
            continue
        matches.append(int(idx))

    if not matches:
        return None
    if update.source_row_index >= 0 and update.source_row_index in matches:
        return update.source_row_index
    return matches[-1]


def apply_enable_updates_to_all_results(
    all_results: pd.DataFrame,
    updates: Sequence[EnableRegistryUpdate],
) -> tuple[pd.DataFrame, int]:
    """Apply enable outcomes to matching remove_partner rows. Returns (df, patched_count)."""
    df = normalize_all_results(all_results)
    patched_count = 0
    for update in updates:
        idx = _find_enable_patch_row_index(df, update)
        if idx is None:
            log.warning(
                "[WalletEditorRegistry] enable patch row not found card=%s partner=%s disable=%s",
                update.card,
                update.partner,
                update.disable_date,
            )
            continue
        df.at[idx, "Включено"] = _cell_str(update.vklyucheno)
        df.at[idx, "Комментарий включения"] = _cell_str(update.comment)
        patched_count += 1
    return df, patched_count


def patch_enable_results_in_dropbox_registry(
    updates: Sequence[EnableRegistryUpdate],
    *,
    settings: RegistrySettings | None = None,
    run_id: str | None = None,
) -> EnablePatchResult:
    """
    Patch Включено / Комментарий включения for auto-enable outcomes.

    Atomic per call: one download, apply all updates, recalc lifecycle, one upload.
    Retries rev conflicts / transient errors until registry_timeout_seconds.
    """
    requested_count = len(updates)
    if not updates:
        return EnablePatchResult(success=True, patched_count=0, requested_count=0)

    dropbox_path = wallet_editor_dropbox_path()
    if not dropbox_path:
        return EnablePatchResult(
            success=False,
            patched_count=0,
            requested_count=requested_count,
            error_reason="DROPBOX_WALLET_EDITOR_PATH is not set",
        )

    settings = settings or load_registry_settings()
    started = time.monotonic()
    deadline = started + settings.registry_timeout_seconds
    last_error = "unknown error"

    while time.monotonic() < deadline:
        try:
            with _lock:
                outcome, patched_count, error_reason = _patch_attempt(
                    updates,
                    dropbox_path=dropbox_path,
                )
        except Exception as exc:
            log.exception(
                "[WalletEditorRegistry] enable patch attempt failed run_id=%s",
                run_id,
            )
            outcome = _PatchOutcome.TRANSIENT
            patched_count = 0
            error_reason = str(exc)

        if outcome == _PatchOutcome.SUCCESS:
            return EnablePatchResult(
                success=True,
                patched_count=patched_count,
                requested_count=requested_count,
            )

        if outcome == _PatchOutcome.PERMANENT:
            return EnablePatchResult(
                success=False,
                patched_count=0,
                requested_count=requested_count,
                error_reason=error_reason or last_error,
            )

        last_error = error_reason or outcome.value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleep_for = min(settings.registry_retry_interval_seconds, remaining)
        if sleep_for > 0:
            time.sleep(sleep_for)

    log.warning(
        "[WalletEditorRegistry] enable patch timeout after %ss run_id=%s path=%s",
        settings.registry_timeout_seconds,
        run_id,
        dropbox_path,
    )
    return EnablePatchResult(
        success=False,
        patched_count=0,
        requested_count=requested_count,
        error_reason=f"timeout: {last_error}",
    )


def _patch_attempt(
    updates: Sequence[EnableRegistryUpdate],
    *,
    dropbox_path: str,
) -> tuple[_PatchOutcome, int, str | None]:
    with tempfile.TemporaryDirectory(prefix="we_registry_patch_") as tmp:
        local_path = Path(tmp) / "wallet_editor.xlsx"
        status, download_rev = download_file_with_rev(dropbox_path, str(local_path))

        if status == "error":
            log.error(
                "[WalletEditorRegistry] enable patch download failed path=%s status=%s",
                dropbox_path,
                status,
            )
            return _PatchOutcome.TRANSIENT, 0, "registry download failed"

        is_new_file = status == "not_found"
        if is_new_file:
            return _PatchOutcome.PERMANENT, 0, "registry file not found"

        all_results_df, runs_df, hold_df, otlezka_df, hold_exists, otlezka_exists = (
            load_registry_frames(local_path, status)
        )

        patched_df, patched_count = apply_enable_updates_to_all_results(
            all_results_df,
            updates,
        )
        recalculated, _missing_partners = recalculate_all_results(
            patched_df,
            hold_df,
            otlezka_df,
        )

        save_registry_workbook(
            local_path,
            all_results=recalculated,
            runs=runs_df,
            hold_exists=hold_exists,
            otlezka_exists=otlezka_exists,
            is_new_file=False,
        )

        upload_status = upload_file_if_rev(str(local_path), dropbox_path, download_rev)
        if upload_status == "rev_conflict":
            log.warning(
                "[WalletEditorRegistry] enable patch upload skipped: rev conflict path=%s",
                dropbox_path,
            )
            return _PatchOutcome.REV_CONFLICT, 0, "rev conflict"
        if upload_status != "uploaded":
            log.error(
                "[WalletEditorRegistry] enable patch upload failed path=%s status=%s",
                dropbox_path,
                upload_status,
            )
            return _PatchOutcome.TRANSIENT, 0, f"upload failed: {upload_status}"

        log.info(
            "[WalletEditorRegistry] enable patch applied patched=%s requested=%s path=%s",
            patched_count,
            len(updates),
            dropbox_path,
        )
        return _PatchOutcome.SUCCESS, patched_count, None
