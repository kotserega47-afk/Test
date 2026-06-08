"""Daily / manual Wallet Editor registry lifecycle refresh (Dropbox workbook only)."""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from pathlib import Path

import pandas as pd

from core.datetime_utils import now_msk
from core.job_runner import Actor
from integrations.dropbox_watcher import download_file_with_rev, upload_file_if_rev
from integrations.telegram_routes import resolve_route_chat_id, routes_from_rules_v2_enabled
from integrations.telegram_bot import send_message_sync
from integrations.wallet_editor_registry import _lock, wallet_editor_dropbox_path
from integrations.wallet_editor_registry_lifecycle import (
    MISSING_OTLEZKA_STATUS,
    STATUS_HOLD,
    STATUS_K_VKLUCHENIYU,
    STATUS_OZHIDAET,
    STATUS_PROSROCHENO,
    _cell_str,
    normalize_all_results,
    recalculate_all_results,
)
from integrations.wallet_editor_registry_settings import RegistrySettings, load_registry_settings
from integrations.wallet_editor_registry_xlsx import load_registry_frames, save_registry_workbook
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

ROUTE_REPORT = "wallet_editor_registry_refresh"
ROUTE_ALERT = "wallet_editor_auto_enable_alert"

LIFECYCLE_DIFF_COLUMNS = ("Дата включения", "Статус включения", "hold")


class _RefreshOutcome(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    REV_CONFLICT = "rev_conflict"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass(frozen=True, slots=True)
class RefreshBreakdown:
    waiting_to_ready: int = 0
    ready_to_overdue: int = 0
    to_hold: int = 0
    missing_otlezka: int = 0
    other: int = 0


@dataclass(frozen=True, slots=True)
class RefreshResult:
    success: bool
    changed_rows: int
    uploaded: bool
    skipped_no_changes: bool
    breakdown: RefreshBreakdown
    duration_sec: float
    error: str | None = None
    report_text: str = ""
    report_sent: bool = False


def _lifecycle_cell(df: pd.DataFrame, idx: int, column: str) -> str:
    return _cell_str(df.at[idx, column])


def _lifecycle_row_changed(before: pd.DataFrame, after: pd.DataFrame, idx: int) -> bool:
    for col in LIFECYCLE_DIFF_COLUMNS:
        if _lifecycle_cell(before, idx, col) != _lifecycle_cell(after, idx, col):
            return True
    return False


def compute_lifecycle_diff(
    before: pd.DataFrame,
    after: pd.DataFrame,
) -> tuple[int, RefreshBreakdown]:
    """Return (changed_row_count, transition breakdown)."""
    before_norm = normalize_all_results(before)
    after_norm = normalize_all_results(after)
    if len(before_norm) != len(after_norm):
        changed = max(len(before_norm), len(after_norm))
        return changed, RefreshBreakdown(other=changed)

    breakdown = RefreshBreakdown()
    changed_rows = 0
    for idx in before_norm.index:
        if not _lifecycle_row_changed(before_norm, after_norm, idx):
            continue
        changed_rows += 1
        old_status = _lifecycle_cell(before_norm, idx, "Статус включения")
        new_status = _lifecycle_cell(after_norm, idx, "Статус включения")
        old_hold = _lifecycle_cell(before_norm, idx, "hold")
        new_hold = _lifecycle_cell(after_norm, idx, "hold")

        if old_status == STATUS_OZHIDAET and new_status == STATUS_K_VKLUCHENIYU:
            breakdown = RefreshBreakdown(
                waiting_to_ready=breakdown.waiting_to_ready + 1,
                ready_to_overdue=breakdown.ready_to_overdue,
                to_hold=breakdown.to_hold,
                missing_otlezka=breakdown.missing_otlezka,
                other=breakdown.other,
            )
        elif old_status == STATUS_K_VKLUCHENIYU and new_status == STATUS_PROSROCHENO:
            breakdown = RefreshBreakdown(
                waiting_to_ready=breakdown.waiting_to_ready,
                ready_to_overdue=breakdown.ready_to_overdue + 1,
                to_hold=breakdown.to_hold,
                missing_otlezka=breakdown.missing_otlezka,
                other=breakdown.other,
            )
        elif new_hold == STATUS_HOLD and old_hold != STATUS_HOLD:
            breakdown = RefreshBreakdown(
                waiting_to_ready=breakdown.waiting_to_ready,
                ready_to_overdue=breakdown.ready_to_overdue,
                to_hold=breakdown.to_hold + 1,
                missing_otlezka=breakdown.missing_otlezka,
                other=breakdown.other,
            )
        elif new_status == MISSING_OTLEZKA_STATUS and old_status != new_status:
            breakdown = RefreshBreakdown(
                waiting_to_ready=breakdown.waiting_to_ready,
                ready_to_overdue=breakdown.ready_to_overdue,
                to_hold=breakdown.to_hold,
                missing_otlezka=breakdown.missing_otlezka + 1,
                other=breakdown.other,
            )
        else:
            breakdown = RefreshBreakdown(
                waiting_to_ready=breakdown.waiting_to_ready,
                ready_to_overdue=breakdown.ready_to_overdue,
                to_hold=breakdown.to_hold,
                missing_otlezka=breakdown.missing_otlezka,
                other=breakdown.other + 1,
            )
    return changed_rows, breakdown


def build_refresh_report(
    *,
    today: date,
    changed_rows: int,
    breakdown: RefreshBreakdown,
    uploaded: bool,
    skipped_no_changes: bool,
    duration_sec: float,
    success: bool,
    error: str | None = None,
) -> str:
    lines = [
        "WalletEditor registry lifecycle refresh",
        "",
    ]
    if skipped_no_changes:
        lines.append("changed rows: 0")
    elif not success:
        pass
    else:
        lines.extend(
            [
                f"date: {today.isoformat()}",
                f"changed rows: {changed_rows}",
            ]
        )
    if changed_rows > 0:
        lines.extend(
            [
                "",
                f"ОЖИДАЕТ → К ВКЛЮЧЕНИЮ: {breakdown.waiting_to_ready}",
                f"К ВКЛЮЧЕНИЮ → ПРОСРОЧЕНО: {breakdown.ready_to_overdue}",
                f"→ HOLD: {breakdown.to_hold}",
                f"НЕТ ДАТЫ ОТЛЁЖКИ: {breakdown.missing_otlezka}",
            ]
        )
        if breakdown.other:
            lines.append(f"other: {breakdown.other}")
    if not success:
        lines.extend(
            [
                "",
                "upload: failed",
                f"error: {error or 'unknown error'}",
            ]
        )
    elif skipped_no_changes:
        lines.extend(["", "upload: skipped (no changes)"])
    else:
        lines.extend(["", "upload: OK"])
    lines.append(f"duration: {duration_sec:.1f}s")
    return "\n".join(lines)


def _send_to_route(route_key: str, text: str) -> bool:
    resolution = resolve_route_chat_id(route_key)
    if resolution.chat_id:
        try:
            send_message_sync(text, chat_id=str(resolution.chat_id))
            return True
        except Exception:
            log.exception("[WalletEditorRefresh] failed to send telegram route=%s", route_key)
            return False

    if routes_from_rules_v2_enabled():
        try:
            from core.rules_provider import get_indexes_v2, get_snapshot_v2
            from core.rules_v2.accessors import BaseRulesAccessor

            snapshot = get_snapshot_v2(force_sync=False)
            indexes = get_indexes_v2(force_sync=False)
            accessor = BaseRulesAccessor(snapshot=snapshot, indexes=indexes)
            chat_id = accessor.get_telegram_chat_id(route_key)
            if chat_id:
                send_message_sync(text, chat_id=str(chat_id))
                return True
        except Exception:
            log.exception("[WalletEditorRefresh] rules route lookup failed route=%s", route_key)

    log.warning(
        "[WalletEditorRefresh] telegram route unresolved route=%s source=%s",
        route_key,
        resolution.source,
    )
    return False


def _send_refresh_report(result: RefreshResult) -> bool:
    route = ROUTE_ALERT if not result.success else ROUTE_REPORT
    return _send_to_route(route, result.report_text)


def _refresh_attempt(
    *,
    dropbox_path: str,
    today: date,
) -> tuple[_RefreshOutcome, int, RefreshBreakdown, str | None]:
    with tempfile.TemporaryDirectory(prefix="we_registry_refresh_") as tmp:
        local_path = Path(tmp) / "wallet_editor.xlsx"
        status, download_rev = download_file_with_rev(dropbox_path, str(local_path))

        if status == "error":
            return _RefreshOutcome.TRANSIENT, 0, RefreshBreakdown(), "registry download failed"
        if status == "not_found":
            return _RefreshOutcome.PERMANENT, 0, RefreshBreakdown(), "registry file not found"

        all_results_df, runs_df, hold_df, otlezka_df, hold_exists, otlezka_exists = (
            load_registry_frames(local_path, status)
        )
        before_df = normalize_all_results(all_results_df)
        recalculated, _missing = recalculate_all_results(
            before_df,
            hold_df,
            otlezka_df,
            today=today,
        )
        changed_rows, breakdown = compute_lifecycle_diff(before_df, recalculated)
        if changed_rows == 0:
            return _RefreshOutcome.SKIPPED, 0, breakdown, None

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
            return _RefreshOutcome.REV_CONFLICT, changed_rows, breakdown, "rev conflict"
        if upload_status != "uploaded":
            return _RefreshOutcome.TRANSIENT, changed_rows, breakdown, f"upload failed: {upload_status}"

        return _RefreshOutcome.SUCCESS, changed_rows, breakdown, None


def refresh_wallet_editor_registry_lifecycle(
    *,
    today: date | None = None,
    actor: Actor | None = None,
    settings: RegistrySettings | None = None,
) -> RefreshResult:
    """Recalculate lifecycle fields and upload registry only when lifecycle columns changed."""
    started = time.monotonic()
    today = today or now_msk().date()
    settings = settings or load_registry_settings()
    dropbox_path = wallet_editor_dropbox_path()

    if not dropbox_path:
        duration = time.monotonic() - started
        breakdown = RefreshBreakdown()
        report = build_refresh_report(
            today=today,
            changed_rows=0,
            breakdown=breakdown,
            uploaded=False,
            skipped_no_changes=False,
            duration_sec=duration,
            success=False,
            error="DROPBOX_WALLET_EDITOR_PATH is not set",
        )
        result = RefreshResult(
            success=False,
            changed_rows=0,
            uploaded=False,
            skipped_no_changes=False,
            breakdown=breakdown,
            duration_sec=duration,
            error="DROPBOX_WALLET_EDITOR_PATH is not set",
            report_text=report,
        )
        return replace(result, report_sent=_send_refresh_report(result))

    if actor is not None:
        log.info(
            "[WalletEditorRefresh] started actor=%s today=%s",
            actor.kind,
            today.isoformat(),
        )

    deadline = time.monotonic() + settings.registry_timeout_seconds
    last_error = "unknown error"
    changed_rows = 0
    breakdown = RefreshBreakdown()
    outcome = _RefreshOutcome.TRANSIENT

    while time.monotonic() < deadline:
        try:
            with _lock:
                outcome, changed_rows, breakdown, error_reason = _refresh_attempt(
                    dropbox_path=dropbox_path,
                    today=today,
                )
        except Exception as exc:
            log.exception("[WalletEditorRefresh] attempt failed")
            outcome = _RefreshOutcome.TRANSIENT
            error_reason = str(exc)

        if outcome == _RefreshOutcome.SUCCESS:
            duration = time.monotonic() - started
            report = build_refresh_report(
                today=today,
                changed_rows=changed_rows,
                breakdown=breakdown,
                uploaded=True,
                skipped_no_changes=False,
                duration_sec=duration,
                success=True,
            )
            result = RefreshResult(
                success=True,
                changed_rows=changed_rows,
                uploaded=True,
                skipped_no_changes=False,
                breakdown=breakdown,
                duration_sec=duration,
                report_text=report,
            )
            return replace(result, report_sent=_send_refresh_report(result))

        if outcome == _RefreshOutcome.SKIPPED:
            duration = time.monotonic() - started
            report = build_refresh_report(
                today=today,
                changed_rows=0,
                breakdown=breakdown,
                uploaded=False,
                skipped_no_changes=True,
                duration_sec=duration,
                success=True,
            )
            result = RefreshResult(
                success=True,
                changed_rows=0,
                uploaded=False,
                skipped_no_changes=True,
                breakdown=breakdown,
                duration_sec=duration,
                report_text=report,
            )
            return replace(result, report_sent=_send_refresh_report(result))

        if outcome == _RefreshOutcome.PERMANENT:
            duration = time.monotonic() - started
            report = build_refresh_report(
                today=today,
                changed_rows=changed_rows,
                breakdown=breakdown,
                uploaded=False,
                skipped_no_changes=False,
                duration_sec=duration,
                success=False,
                error=error_reason or last_error,
            )
            result = RefreshResult(
                success=False,
                changed_rows=changed_rows,
                uploaded=False,
                skipped_no_changes=False,
                breakdown=breakdown,
                duration_sec=duration,
                error=error_reason or last_error,
                report_text=report,
            )
            return replace(result, report_sent=_send_refresh_report(result))

        last_error = error_reason or outcome.value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleep_for = min(settings.registry_retry_interval_seconds, remaining)
        if sleep_for > 0:
            time.sleep(sleep_for)

    duration = time.monotonic() - started
    report = build_refresh_report(
        today=today,
        changed_rows=changed_rows,
        breakdown=breakdown,
        uploaded=False,
        skipped_no_changes=False,
        duration_sec=duration,
        success=False,
        error=f"timeout: {last_error}",
    )
    result = RefreshResult(
        success=False,
        changed_rows=changed_rows,
        uploaded=False,
        skipped_no_changes=False,
        breakdown=breakdown,
        duration_sec=duration,
        error=f"timeout: {last_error}",
        report_text=report,
    )
    return replace(result, report_sent=_send_refresh_report(result))


def run_wallet_editor_registry_refresh_job() -> None:
    """JOB_REGISTRY entry — scheduler and /wallet_editor_refresh."""
    result = refresh_wallet_editor_registry_lifecycle(actor=Actor(kind="scheduler"))
    if not result.success:
        raise RuntimeError(result.error or "registry lifecycle refresh failed")
