"""Best-effort cumulative Wallet Editor results registry in Dropbox."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Sequence

import pandas as pd

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from core.event_log import append_event
from integrations.dropbox_watcher import (
    download_file_with_rev,
    upload_file_if_rev,
)
from integrations.telegram_bot import send_message_sync
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_REMOVE_PARTNER,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_SYNCED,
    OUTBOX_STATUS_SYNCING,
    OUTBOX_ACTIVE_STATUSES,
    STATUS_K_VKLUCHENIYU,
    STATUS_PROSROCHENO,
    build_runs_row,
    filter_rows_not_in_registry,
    is_processed_run_ids_corrupted,
    load_processed_run_ids,
    load_processed_run_ids_result,
    processed_run_ids_corruption_error,
    load_warned_partners,
    mark_run_processed,
    missing_result_fingerprints,
    normalize_all_results,
    parse_disable_datetime,
    partners_to_warn,
    recalculate_all_results_runtime,
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
from integrations.wallet_editor_registry_db.mirror import MirrorResultBatch
from integrations.wallet_editor_registry_db.mapping import map_all_results_row, map_runs_row
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


def _send_missing_otlezka_warnings(
    chat_id: int,
    partners: list[str],
    *,
    details: dict[str, str] | None = None,
) -> None:
    from integrations.wallet_editor_registry_lifecycle import WARN_MESSAGE_TEMPLATE

    details = details or {}
    for partner in partners:
        try:
            message = WARN_MESSAGE_TEMPLATE.format(partner=partner)
            extra = (details.get(partner) or "").strip()
            if extra:
                message = message.replace(
                    "Партнёр:\n{partner}\n\n".format(partner=partner),
                    "Партнёр:\n{partner}\n\nПричина:\n{extra}\n\n".format(
                        partner=partner,
                        extra=extra,
                    ),
                    1,
                )
            send_message_sync(
                message,
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
    details: dict[str, str] | None = None,
) -> None:
    from integrations.wallet_editor_partner_resolve import runtime_partner_aliases

    warned = load_warned_partners()
    warned = sync_warned_partners_after_otlezka(
        otlezka_df,
        warned,
        partner_aliases=runtime_partner_aliases(),
    )
    if not missing_partners:
        return
    to_warn = partners_to_warn(missing_partners, warned)
    if not to_warn:
        return
    _send_missing_otlezka_warnings(task.chat_id, to_warn, details=details)
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
    from integrations.wallet_editor_registry_async import update_outbox_status

    from integrations.wallet_editor_registry_db.config import (
        LegacyRegistrySourceError,
        get_database_url,
        manual_readers_source_is_postgres,
        registry_source,
    )

    try:
        registry_source()
    except LegacyRegistrySourceError as exc:
        log.error(
            "[WalletEditorRegistry] append blocked run_id=%s: %s",
            task.run_id,
            exc,
        )
        update_outbox_status(
            task.run_id,
            status=OUTBOX_STATUS_FAILED,
            last_error=str(exc),
            increment_attempt=True,
        )
        return

    dropbox_path = wallet_editor_dropbox_path() or ""

    if not manual_readers_source_is_postgres() and not dropbox_path:
        log.error(
            "[WalletEditorRegistry] postgres append blocked run_id=%s: %s not set "
            "(required for dropbox manual readers)",
            task.run_id,
            ENV_DROPBOX_WALLET_EDITOR_PATH,
        )
        update_outbox_status(
            task.run_id,
            status=OUTBOX_STATUS_FAILED,
            last_error=f"{ENV_DROPBOX_WALLET_EDITOR_PATH} is not set",
            increment_attempt=True,
        )
        return
    elif not get_database_url():
        log.error(
            "[WalletEditorRegistry] postgres append blocked run_id=%s: DATABASE_URL missing",
            task.run_id,
        )
        update_outbox_status(
            task.run_id,
            status=OUTBOX_STATUS_FAILED,
            last_error="DATABASE_URL is not set",
            increment_attempt=True,
        )
        return

    if is_processed_run_ids_corrupted():
        corruption_error = (
            processed_run_ids_corruption_error()
            or "registry_processed_run_ids.json is corrupted"
        )
        log.error(
            "[WalletEditorRegistry] append blocked run_id=%s: %s",
            task.run_id,
            corruption_error,
        )
        update_outbox_status(
            task.run_id,
            status=OUTBOX_STATUS_FAILED,
            last_error=f"processed_run_ids corrupted: {corruption_error}",
            increment_attempt=True,
        )
        _emit_sync_event(
            "wallet_editor_registry_sync_failed",
            task.run_id,
            {"reason": "processed_run_ids_corrupted", "error": corruption_error},
        )
        append_event(
            type="wallet_editor_registry_health_degraded",
            job_type="wallet_editor",
            payload={
                "processed_run_ids_corrupted": True,
                "error": corruption_error,
            },
        )
        return

    if not os.path.isfile(result_path):
        log.error(
            "[WalletEditorRegistry] result file missing run_id=%s path=%s",
            task.run_id,
            result_path,
        )
        update_outbox_status(
            task.run_id,
            status=OUTBOX_STATUS_FAILED,
            last_error="result file missing",
            increment_attempt=True,
        )
        _emit_sync_event(
            "wallet_editor_registry_sync_failed",
            task.run_id,
            {"reason": "result file missing", "path": result_path},
        )
        return

    settings = settings or load_registry_settings()
    started = time.monotonic()
    deadline = started + settings.registry_timeout_seconds
    slow_warning_sent = False
    last_error = "unknown error"

    update_outbox_status(task.run_id, status=OUTBOX_STATUS_SYNCING, increment_attempt=True)
    _emit_sync_event(
        "wallet_editor_registry_sync_started",
        task.run_id,
        {"path": dropbox_path},
    )

    try:
        while time.monotonic() < deadline:
            elapsed = time.monotonic() - started
            if not slow_warning_sent and elapsed >= settings.registry_warning_seconds:
                _send_slow_append_warning(task.chat_id)
                slow_warning_sent = True

            try:
                mirror_batch: MirrorResultBatch | None = None
                with _lock:
                    outcome, upload_rev, mirror_batch = _append_attempt(
                        task,
                        result_path,
                        stats,
                        dropbox_path=dropbox_path,
                        run_started_at=run_started_at,
                        run_finished_at=run_finished_at,
                        output_file=output_file,
                    )
            except Exception as exc:
                log.exception(
                    "[WalletEditorRegistry] append attempt failed run_id=%s",
                    task.run_id,
                )
                outcome = _AppendOutcome.TRANSIENT
                upload_rev = None
                mirror_batch = None
                last_error = str(exc)

            if outcome == _AppendOutcome.SUCCESS:
                update_outbox_status(
                    task.run_id,
                    status=OUTBOX_STATUS_SYNCED,
                    registry_upload_rev=upload_rev,
                )
                _emit_sync_event(
                    "wallet_editor_registry_sync_success",
                    task.run_id,
                    {"path": dropbox_path, "rev": upload_rev},
                )
                return

            if outcome == _AppendOutcome.DUPLICATE:
                update_outbox_status(
                    task.run_id,
                    status=OUTBOX_STATUS_SYNCED,
                    registry_upload_rev=upload_rev,
                )
                _emit_sync_event(
                    "wallet_editor_registry_sync_success",
                    task.run_id,
                    {"path": dropbox_path, "duplicate": True},
                )
                return

            if outcome == _AppendOutcome.PERMANENT:
                update_outbox_status(
                    task.run_id,
                    status=OUTBOX_STATUS_FAILED,
                    last_error=last_error,
                )
                _emit_sync_event(
                    "wallet_editor_registry_sync_failed",
                    task.run_id,
                    {"reason": "permanent", "error": last_error},
                )
                return

            last_error = outcome.value
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
        update_outbox_status(
            task.run_id,
            status=OUTBOX_STATUS_FAILED,
            last_error=f"timeout: {last_error}",
        )
        _emit_sync_event(
            "wallet_editor_registry_sync_failed",
            task.run_id,
            {"reason": "timeout", "error": last_error},
        )
        _send_timeout_warning(task.chat_id)
    except Exception as exc:
        log.exception(
            "[WalletEditorRegistry] append failed run_id=%s profile=%s",
            task.run_id,
            task.operator_profile,
        )
        update_outbox_status(
            task.run_id,
            status=OUTBOX_STATUS_FAILED,
            last_error=str(exc),
        )
        _emit_sync_event(
            "wallet_editor_registry_sync_failed",
            task.run_id,
            {"reason": "exception", "error": str(exc)},
        )


def _emit_sync_event(event_type: str, run_id: str, payload: dict) -> None:
    append_event(
        type=event_type,
        job_type="wallet_editor",
        job_id=run_id,
        payload=payload,
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
) -> tuple[_AppendOutcome, str | None, MirrorResultBatch | None]:
    from integrations.wallet_editor_registry_db.postgres_source import append_attempt_postgres

    return append_attempt_postgres(
        task,
        result_path,
        stats,
        dropbox_path=dropbox_path,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        output_file=output_file,
        resolve_source=resolve_source,
        process_missing_otlezka_warnings=_process_missing_otlezka_warnings,
    )


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

    from integrations.wallet_editor_registry_db.config import (
        LegacyRegistrySourceError,
        manual_readers_source_is_postgres,
        registry_source,
    )

    try:
        registry_source()
    except LegacyRegistrySourceError as exc:
        return EnablePatchResult(
            success=False,
            patched_count=0,
            requested_count=requested_count,
            error_reason=str(exc),
        )

    dropbox_path = wallet_editor_dropbox_path() or ""
    if not manual_readers_source_is_postgres() and not dropbox_path:
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
            mirror_batch: MirrorResultBatch | None = None
            with _lock:
                outcome, patched_count, error_reason, mirror_batch = _patch_attempt(
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
            mirror_batch = None

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
) -> tuple[_PatchOutcome, int, str | None, MirrorResultBatch | None]:
    from integrations.wallet_editor_registry_db.postgres_source import patch_attempt_postgres

    return patch_attempt_postgres(
        updates,
        dropbox_path=dropbox_path,
        apply_enable_updates_to_all_results=apply_enable_updates_to_all_results,
        find_enable_patch_row_index=_find_enable_patch_row_index,
    )


# -----------------------------------------------------------------------------
# Outbox replay and registry health (Phase 1 durability)
# -----------------------------------------------------------------------------

DEFAULT_STALE_OUTBOX_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class RegistryHealthReport:
    outbox_pending_count: int
    outbox_failed_count: int
    outbox_synced_count: int
    oldest_pending_age_sec: float | None
    last_sync_error: str | None
    processed_without_rows_count: int
    processed_run_ids_corrupted: bool
    processed_run_ids_corruption_error: str | None
    overdue_ready_count: int
    missing_otlezka_count: int
    missing_durable_result_count: int
    stale_outbox: bool
    degraded: bool
    mirror_enabled_flag: bool = False
    registry_source: str = "excel"
    mirror_last_error: str | None = None
    mirror_last_success_at: str | None = None
    mirror_last_failure_at: str | None = None
    mirror_recent_failures: int = 0
    mirror_failure_count: int = 0
    mirror_last_operation: str | None = None
    manual_snapshot_block: str | None = None
    manual_readers_source: str = "dropbox"
    manual_readers_no_successful_sync: bool = False


@dataclass(frozen=True, slots=True)
class OutboxReplayResult:
    attempted: int
    synced: int
    failed: int
    skipped: int
    errors: tuple[str, ...]


def _parse_iso_age_seconds(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        from core.datetime_utils import ensure_aware_msk

        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = ensure_aware_msk(dt)
        return max(0.0, (datetime.now(dt.tzinfo) - dt).total_seconds())
    except Exception:
        return None


def _task_from_outbox(record) -> WalletEditorTask:
    return WalletEditorTask(
        file_path=record.result_file_path,
        chat_id=record.chat_id,
        telegram_user_id=record.telegram_user_id,
        operator_profile=record.operator_profile,
        source_file_name=record.source_file_name,
        login="",
        password="",
        auth_state_path="",
        run_id=record.run_id,
    )


def _stats_from_outbox(record) -> Stats:
    stats = Stats()
    stats.ok = record.stats_success_rows
    stats.fail = record.stats_failed_rows
    stats.skip = record.stats_skipped_rows
    return stats


def _datetime_from_iso(iso_ts: str) -> datetime:
    from core.datetime_utils import ensure_aware_msk

    dt = datetime.fromisoformat(iso_ts)
    return ensure_aware_msk(dt)


def replay_pending_outbox_records(
    *,
    include_failed: bool = True,
    limit: int | None = None,
) -> OutboxReplayResult:
    """Replay pending/failed outbox records using durable STATE_DIR result copies."""
    from integrations.wallet_editor_registry_async import (
        load_outbox_records,
        update_outbox_status,
    )
    from integrations.wallet_editor_registry_db.manual_sync import run_manual_sync_prerun_gate

    gate = run_manual_sync_prerun_gate(triggered_by="registry_replay")
    if not gate.ok:
        message = gate.operator_message or "manual sync gate failed"
        log.error("[WalletEditorRegistry] replay blocked: %s", message)
        return OutboxReplayResult(
            attempted=0,
            synced=0,
            failed=0,
            skipped=0,
            errors=(message,),
        )

    if is_processed_run_ids_corrupted():
        corruption_error = (
            processed_run_ids_corruption_error()
            or "registry_processed_run_ids.json is corrupted"
        )
        log.error("[WalletEditorRegistry] replay blocked: %s", corruption_error)
        append_event(
            type="wallet_editor_registry_health_degraded",
            job_type="wallet_editor",
            payload={
                "processed_run_ids_corrupted": True,
                "error": corruption_error,
                "replay_blocked": True,
            },
        )
        return OutboxReplayResult(
            attempted=0,
            synced=0,
            failed=0,
            skipped=0,
            errors=(f"replay blocked: processed_run_ids corrupted: {corruption_error}",),
        )

    statuses = {OUTBOX_STATUS_PENDING}
    if include_failed:
        statuses.add(OUTBOX_STATUS_FAILED)

    records = [r for r in load_outbox_records() if r.status in statuses]
    records.sort(key=lambda r: r.created_at)
    if limit is not None:
        records = records[:limit]

    attempted = synced = failed = skipped = 0
    errors: list[str] = []

    for record in records:
        if not os.path.isfile(record.result_file_path):
            skipped += 1
            msg = f"run_id={record.run_id}: durable result missing"
            errors.append(msg)
            update_outbox_status(
                record.run_id,
                status=OUTBOX_STATUS_FAILED,
                last_error="durable result file missing",
            )
            continue

        attempted += 1
        update_outbox_status(record.run_id, status=OUTBOX_STATUS_PENDING)
        task = _task_from_outbox(record)
        stats = _stats_from_outbox(record)
        append_run_to_dropbox_registry(
            task,
            record.result_file_path,
            stats,
            run_started_at=_datetime_from_iso(record.run_started_at),
            run_finished_at=_datetime_from_iso(record.run_finished_at),
            output_file=record.output_file,
        )
        refreshed = load_outbox_records()
        current = next((r for r in refreshed if r.run_id == record.run_id), None)
        if current and current.status == OUTBOX_STATUS_SYNCED:
            synced += 1
        else:
            failed += 1
            if current and current.last_error:
                errors.append(f"run_id={record.run_id}: {current.last_error}")

    return OutboxReplayResult(
        attempted=attempted,
        synced=synced,
        failed=failed,
        skipped=skipped,
        errors=tuple(errors),
    )


def _count_registry_lifecycle_metrics(
    all_results: pd.DataFrame,
) -> tuple[int, int]:
    from integrations.wallet_editor_registry_lifecycle import (
        MISSING_OTLEZKA_STATUS,
        STATUS_K_VKLUCHENIYU,
        STATUS_PROSROCHENO,
    )

    df = normalize_all_results(all_results)
    overdue = 0
    missing_otlezka = 0
    for idx in df.index:
        status = _cell_str(df.at[idx, "Статус включения"])
        if status == STATUS_PROSROCHENO:
            overdue += 1
        elif status == STATUS_K_VKLUCHENIYU:
            overdue += 1
        if status == MISSING_OTLEZKA_STATUS:
            missing_otlezka += 1
    return overdue, missing_otlezka


def _count_processed_without_rows(dropbox_path: str | None) -> int:
    from integrations.wallet_editor_registry_async import load_outbox_records

    if is_processed_run_ids_corrupted():
        return 0

    processed_result = load_processed_run_ids_result()
    processed = processed_result.run_ids
    if not processed:
        return 0

    outbox_by_id = {r.run_id: r for r in load_outbox_records()}

    try:
        from integrations.wallet_editor_registry_db.frames import (
            load_registry_row_fingerprints_from_postgres,
        )
        from integrations.wallet_editor_registry_lifecycle import (
            missing_result_fingerprints_in_set,
        )

        present_fingerprints = load_registry_row_fingerprints_from_postgres()
    except Exception:
        log.exception(
            "[WalletEditorRegistry] health postgres processed_without_rows read failed"
        )
        return 0

    count = 0
    for run_id in processed:
        record = outbox_by_id.get(run_id)
        result_path = record.result_file_path if record else None
        if not result_path or not os.path.isfile(result_path):
            continue
        if missing_result_fingerprints_in_set(result_path, present_fingerprints):
            count += 1
    return count


def build_registry_health_report(
    *,
    stale_threshold_seconds: int = DEFAULT_STALE_OUTBOX_SECONDS,
) -> RegistryHealthReport:
    from integrations.wallet_editor_registry_async import (
        OUTBOX_STATUS_FAILED,
        OUTBOX_STATUS_PENDING,
        OUTBOX_STATUS_SYNCED,
        load_outbox_records,
    )

    records = load_outbox_records()
    pending = [r for r in records if r.status == OUTBOX_STATUS_PENDING]
    failed = [r for r in records if r.status == OUTBOX_STATUS_FAILED]
    synced = [r for r in records if r.status == OUTBOX_STATUS_SYNCED]

    oldest_pending_age: float | None = None
    for record in pending:
        age = _parse_iso_age_seconds(record.created_at)
        if age is not None:
            oldest_pending_age = age if oldest_pending_age is None else max(oldest_pending_age, age)

    last_sync_error: str | None = None
    for record in sorted(records, key=lambda r: r.last_attempt_at or "", reverse=True):
        if record.last_error:
            last_sync_error = record.last_error
            break

    missing_durable = sum(
        1
        for r in records
        if r.status in OUTBOX_ACTIVE_STATUSES and not os.path.isfile(r.result_file_path)
    )

    dropbox_path = wallet_editor_dropbox_path()
    processed_without_rows = _count_processed_without_rows(dropbox_path)
    processed_run_ids_corrupted = is_processed_run_ids_corrupted()
    processed_run_ids_error = processed_run_ids_corruption_error()

    overdue_ready = 0
    missing_otlezka = 0
    from integrations.wallet_editor_registry_db.config import (
        manual_readers_source,
        mirror_enabled as db_mirror_enabled,
        registry_source,
        registry_source_is_postgres,
    )
    from integrations.wallet_editor_registry_db.manual_readers import (
        load_hold_otlezka_for_runtime,
        pg_manual_readers_missing_successful_sync,
    )
    from integrations.wallet_editor_registry_db.mirror_state import get_mirror_health

    manual_readers_no_sync = pg_manual_readers_missing_successful_sync()

    try:
        from integrations.wallet_editor_registry_db.frames import load_registry_frames_from_postgres

        all_df, _runs = load_registry_frames_from_postgres()
        dropbox_path_for_hold = dropbox_path or ""
        hold_df, otlezka_df, _he, _oe = load_hold_otlezka_for_runtime(dropbox_path_for_hold)
        recalculated, missing_partners = recalculate_all_results_runtime(
            all_df,
            hold_df,
            otlezka_df,
        )
        overdue_ready, missing_otlezka = _count_registry_lifecycle_metrics(recalculated)
        if missing_partners:
            missing_otlezka = max(missing_otlezka, len(missing_partners))
    except Exception:
        log.exception("[WalletEditorRegistry] health registry read failed")

    stale_outbox = bool(
        oldest_pending_age is not None and oldest_pending_age > stale_threshold_seconds
    )

    mirror_health = get_mirror_health()
    mirror_failures = mirror_health.recent_failures

    degraded = bool(
        pending
        or failed
        or processed_without_rows > 0
        or processed_run_ids_corrupted
        or missing_durable > 0
        or stale_outbox
        or mirror_failures > 0
        or manual_readers_no_sync
    )

    if degraded:
        append_event(
            type="wallet_editor_registry_health_degraded",
            job_type="wallet_editor",
            payload={
                "pending": len(pending),
                "failed": len(failed),
                "processed_without_rows": processed_without_rows,
                "processed_run_ids_corrupted": processed_run_ids_corrupted,
                "processed_run_ids_error": processed_run_ids_error,
                "stale_outbox": stale_outbox,
            },
        )

    manual_snapshot_block: str | None = None
    try:
        from integrations.wallet_editor_registry_db.manual_sync import build_manual_snapshot_health_block

        manual_snapshot_block = build_manual_snapshot_health_block()
    except Exception:
        log.exception("[WalletEditorRegistry] manual snapshot health block failed")

    return RegistryHealthReport(
        outbox_pending_count=len(pending),
        outbox_failed_count=len(failed),
        outbox_synced_count=len(synced),
        oldest_pending_age_sec=oldest_pending_age,
        last_sync_error=last_sync_error,
        processed_without_rows_count=processed_without_rows,
        processed_run_ids_corrupted=processed_run_ids_corrupted,
        processed_run_ids_corruption_error=processed_run_ids_error,
        overdue_ready_count=overdue_ready,
        missing_otlezka_count=missing_otlezka,
        missing_durable_result_count=missing_durable,
        stale_outbox=stale_outbox,
        degraded=degraded,
        mirror_enabled_flag=db_mirror_enabled(),
        registry_source=registry_source(),
        mirror_last_error=mirror_health.last_error,
        mirror_last_success_at=mirror_health.last_success_at,
        mirror_last_failure_at=mirror_health.last_failure_at,
        mirror_recent_failures=mirror_failures,
        mirror_failure_count=mirror_health.failure_count,
        mirror_last_operation=mirror_health.last_operation,
        manual_snapshot_block=manual_snapshot_block,
        manual_readers_source=manual_readers_source(),
        manual_readers_no_successful_sync=manual_readers_no_sync,
    )


def format_registry_health_report(report: RegistryHealthReport) -> str:
    lines = [
        "WalletEditor registry health",
        "",
        f"outbox pending: {report.outbox_pending_count}",
        f"outbox failed: {report.outbox_failed_count}",
        f"outbox synced: {report.outbox_synced_count}",
    ]
    if report.oldest_pending_age_sec is not None:
        lines.append(f"oldest pending age: {report.oldest_pending_age_sec:.0f}s")
    if report.last_sync_error:
        lines.append(f"last sync error: {report.last_sync_error}")
    lines.extend(
        [
            f"processed_run_ids without rows: {report.processed_without_rows_count}",
            f"processed_run_ids corrupted: {report.processed_run_ids_corrupted}",
        ]
    )
    if report.processed_run_ids_corruption_error:
        lines.append(
            f"processed_run_ids corruption error: {report.processed_run_ids_corruption_error}"
        )
    lines.extend(
        [
            f"overdue/ready wallets: {report.overdue_ready_count}",
            f"missing Отлёжка partners: {report.missing_otlezka_count}",
            f"missing durable result files: {report.missing_durable_result_count}",
            f"stale outbox: {report.stale_outbox}",
            f"registry source: {report.registry_source}",
            f"mirror enabled: {report.mirror_enabled_flag}",
            f"mirror recent failures: {report.mirror_recent_failures}",
            f"mirror failure count: {report.mirror_failure_count}",
        ]
    )
    if report.mirror_last_operation:
        lines.append(f"mirror last operation: {report.mirror_last_operation}")
    if report.mirror_last_success_at:
        lines.append(f"mirror last success: {report.mirror_last_success_at}")
    if report.mirror_last_failure_at:
        lines.append(f"mirror last failure: {report.mirror_last_failure_at}")
    if report.mirror_last_error:
        lines.append(f"mirror last error: {report.mirror_last_error}")
    lines.append("Registry projection: DISABLED (architecture)")
    lines.append("Registry export: Telegram only")
    lines.append(f"manual readers source: {report.manual_readers_source}")
    if report.manual_readers_no_successful_sync:
        lines.append("DEGRADED: no successful manual sync")
    lines.append(f"status: {'DEGRADED' if report.degraded else 'OK'}")
    if report.processed_run_ids_corrupted:
        lines.append("")
        lines.append(
            "⚠️ CRITICAL: registry_processed_run_ids.json is corrupted. "
            "Replay is blocked until the file is repaired."
        )
    if report.processed_without_rows_count > 0:
        lines.append("")
        lines.append(
            "⚠️ CRITICAL: processed run_ids exist but registry rows are missing. "
            "Use /registry_replay to repair."
        )
    if report.manual_snapshot_block:
        lines.append("")
        lines.append(report.manual_snapshot_block)
    return "\n".join(lines)


def run_registry_outbox_replay_job() -> str:
    """JOB_REGISTRY entry for scheduled / manual outbox replay."""
    result = replay_pending_outbox_records()
    return (
        f"replay attempted={result.attempted} synced={result.synced} "
        f"failed={result.failed} skipped={result.skipped}"
    )


def _registry_completeness_warning_parts(report: RegistryHealthReport) -> list[str]:
    parts: list[str] = []
    if report.stale_outbox:
        age = report.oldest_pending_age_sec or 0.0
        parts.append(f"registry outbox stale (oldest pending {age:.0f}s)")
    if report.outbox_pending_count:
        parts.append(f"pending={report.outbox_pending_count}")
    if report.processed_without_rows_count:
        parts.append(f"processed_without_rows={report.processed_without_rows_count}")
    if report.processed_run_ids_corrupted:
        parts.append("processed_run_ids_corrupted")
    return parts


def _postgres_registry_stale_outbox_warning(report: RegistryHealthReport) -> str | None:
    risk_parts = _registry_completeness_warning_parts(report)
    if risk_parts:
        return "⚠️ Registry may be incomplete: " + ", ".join(risk_parts)

    if report.outbox_failed_count > 0 and report.processed_without_rows_count == 0:
        return (
            f"ℹ️ Historical failed outbox records: {report.outbox_failed_count}\n"
            "Registry integrity verified."
        )
    return None


def registry_stale_outbox_warning(
    *,
    stale_threshold_seconds: int = DEFAULT_STALE_OUTBOX_SECONDS,
) -> str | None:
    """Return warning text when registry durability or completeness may be at risk."""
    report = build_registry_health_report(stale_threshold_seconds=stale_threshold_seconds)
    return _postgres_registry_stale_outbox_warning(report)
