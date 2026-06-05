"""Wallet Editor auto-enable orchestrator.

Phase A: read-only registry planning and Telegram report.
Phase B (future): single Antares session per card — open once, read status/partners,
decide, and execute set_status/add_partner in the same pass (no separate pre-scan).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import pandas as pd

from core.datetime_utils import now_msk
from core.job_runner import Actor
from core.rules_provider import get_indexes_v2, get_snapshot_v2
from core.rules_v2.accessors import BaseRulesAccessor
from integrations.dropbox_watcher import download_file_with_rev
from integrations.telegram_bot import send_message_sync
from integrations.telegram_routes import resolve_route_chat_id, routes_from_rules_v2_enabled
from integrations.wallet_editor_auto_enable_eligibility import (
    CandidateRow,
    EligibilityResult,
    calculate_batch_timeout,
    select_auto_enable_candidates,
    split_batches,
)
from integrations.wallet_editor_auto_enable_settings import (
    AutoEnableSettings,
    load_auto_enable_settings,
)
from integrations.wallet_editor_registry import wallet_editor_dropbox_path
from integrations.wallet_editor_registry_lifecycle import recalculate_all_results
from integrations.wallet_editor_registry_xlsx import load_registry_frames
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

PHASE_LABEL = "Phase A dry-run"


@dataclass(frozen=True, slots=True)
class AutoEnableRunResult:
    sent: bool
    report_text: str
    skipped_reason: str | None = None


def _send_to_route(route_key: str, text: str) -> bool:
    resolution = resolve_route_chat_id(route_key)
    if resolution.chat_id:
        try:
            send_message_sync(text, chat_id=str(resolution.chat_id))
            return True
        except Exception:
            log.exception("[AutoEnable] failed to send telegram route=%s", route_key)
            return False

    try:
        snapshot = get_snapshot_v2(force_sync=False)
        indexes = get_indexes_v2(force_sync=False)
        accessor = BaseRulesAccessor(snapshot=snapshot, indexes=indexes)
        chat_id = accessor.get_telegram_chat_id(route_key)
        if chat_id:
            send_message_sync(text, chat_id=str(chat_id))
            return True
    except Exception:
        log.exception("[AutoEnable] rules route lookup failed route=%s", route_key)

    log.warning(
        "[AutoEnable] telegram route unresolved route=%s source=%s rules_flag=%s",
        route_key,
        resolution.source,
        routes_from_rules_v2_enabled(),
    )
    return False


def load_registry_frames_for_planning(
    *,
    today: date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Download registry from Dropbox and return recalculated all_results frames."""
    dropbox_path = wallet_editor_dropbox_path()
    if not dropbox_path:
        raise RuntimeError("DROPBOX_WALLET_EDITOR_PATH is not set")

    today = today or now_msk().date()

    with tempfile.TemporaryDirectory(prefix="we_auto_enable_") as tmp:
        local_path = Path(tmp) / "wallet_editor.xlsx"
        status, _rev = download_file_with_rev(dropbox_path, str(local_path))
        if status == "error":
            raise RuntimeError(f"registry download failed path={dropbox_path}")

        all_df, _runs_df, hold_df, otlezka_df, _hold_exists, _otlezka_exists = (
            load_registry_frames(local_path, status)
        )

    recalculated, _missing = recalculate_all_results(
        all_df,
        hold_df,
        otlezka_df,
        today=today,
    )
    return recalculated, hold_df, otlezka_df, all_df


def build_phase_a_report(
    *,
    settings: AutoEnableSettings,
    eligibility: EligibilityResult,
    batches: tuple[tuple[CandidateRow, ...], ...],
    manual: bool,
    actor: Actor | None = None,
) -> str:
    batch_sizes = [len(batch) for batch in batches]
    timeout_lines = [
        (
            f"  batch {index + 1}: size={size}, "
            f"timeout_est={calculate_batch_timeout(size, seconds_per_card_timeout=settings.seconds_per_card_timeout, batch_timeout_buffer_seconds=settings.batch_timeout_buffer_seconds)}s"
        )
        for index, size in enumerate(batch_sizes)
    ]

    execution_note = (
        "dry_run=1: plan only."
        if settings.dry_run
        else "dry_run=0: Phase A does not execute Antares (execution is Phase B)."
    )

    actor_line = ""
    if actor is not None:
        actor_line = (
            f"actor: kind={actor.kind} chat_id={actor.chat_id} user_id={actor.user_id}\n"
        )

    lines = [
        "🧩 WalletEditor Auto-Enable",
        f"mode: {PHASE_LABEL}",
        f"trigger: {'manual /auto_enable_run' if manual else 'scheduled'}",
        actor_line.rstrip(),
        "",
        "job_params:",
        f"- enabled: {settings.enabled}",
        f"- dry_run: {settings.dry_run}",
        f"- approval_required: {settings.approval_required}",
        f"- include_overdue: {settings.include_overdue}",
        f"- max_rows_per_batch: {settings.max_rows_per_batch}",
        f"- seconds_per_card_timeout: {settings.seconds_per_card_timeout}",
        f"- batch_timeout_buffer_seconds: {settings.batch_timeout_buffer_seconds}",
        "",
        "selection:",
        f"- eligible before dedup: {eligibility.eligible_before_dedup}",
        f"- selected after dedup: {len(eligibility.selected)}",
        f"- duplicates skipped: {eligibility.duplicates_skipped}",
        "",
        "breakdown (selected):",
        f"- К ВКЛЮЧЕНИЮ: {eligibility.breakdown.k_vklyucheniyu}",
        f"- ПРОСРОЧЕНО: {eligibility.breakdown.prosrocheno}",
        f"- FAIL retry: {eligibility.breakdown.fail_retry}",
        f"- empty Включено: {eligibility.breakdown.empty_vklyucheno}",
        "",
        "batches:",
        f"- batch count: {len(batches)}",
        f"- batch sizes: {batch_sizes or '[]'}",
        "timeout estimates:",
        *(timeout_lines or ["  (none)"]),
        "",
        execution_note,
        "⚠️ Antares не изменялся. Registry не изменялся.",
    ]
    return "\n".join(line for line in lines if line is not None)


def _disabled_report(settings: AutoEnableSettings, *, manual: bool, actor: Actor | None) -> str:
    actor_line = ""
    if actor is not None:
        actor_line = f"actor: kind={actor.kind} chat_id={actor.chat_id} user_id={actor.user_id}\n"
    return "\n".join(
        [
            "🧩 WalletEditor Auto-Enable",
            f"mode: {PHASE_LABEL}",
            f"trigger: {'manual /auto_enable_run' if manual else 'scheduled'}",
            actor_line.rstrip(),
            "",
            "status: disabled (job_params enabled=0)",
            "no registry read performed.",
            "⚠️ Antares не изменялся. Registry не изменялся.",
        ]
    ).strip()


def run_auto_enable(
    actor: Actor | None = None,
    *,
    manual: bool = False,
    settings: AutoEnableSettings | None = None,
    today: date | None = None,
    registry_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None,
) -> AutoEnableRunResult:
    """
    Phase A entrypoint: plan/report only.

    Does not call engine.run(), does not patch registry.
    """
    settings = settings or load_auto_enable_settings()

    if not settings.enabled:
        report = _disabled_report(settings, manual=manual, actor=actor)
        log.info("[AutoEnable] skipped: enabled=0")
        sent = _send_to_route(settings.telegram_route_report, report)
        return AutoEnableRunResult(sent=sent, report_text=report, skipped_reason="disabled")

    try:
        if registry_frames is None:
            recalculated, _hold_df, _otlezka_df, _raw = load_registry_frames_for_planning(today=today)
        else:
            recalculated = registry_frames[0]

        eligibility = select_auto_enable_candidates(
            recalculated,
            include_overdue=settings.include_overdue,
        )
        batches = split_batches(
            eligibility.selected,
            max_rows_per_batch=settings.max_rows_per_batch,
        )
        report = build_phase_a_report(
            settings=settings,
            eligibility=eligibility,
            batches=batches,
            manual=manual,
            actor=actor,
        )
        log.info(
            "[AutoEnable] plan ready selected=%s batches=%s duplicates_skipped=%s",
            len(eligibility.selected),
            len(batches),
            eligibility.duplicates_skipped,
        )
        sent = _send_to_route(settings.telegram_route_report, report)
        return AutoEnableRunResult(sent=sent, report_text=report)

    except Exception as exc:
        log.exception("[AutoEnable] plan failed")
        report = "\n".join(
            [
                "🧩 WalletEditor Auto-Enable",
                f"mode: {PHASE_LABEL}",
                "",
                f"status: error — {exc}",
                "⚠️ Antares не изменялся. Registry не изменялся.",
            ]
        )
        sent = _send_to_route(settings.telegram_route_report, report)
        return AutoEnableRunResult(sent=sent, report_text=report, skipped_reason="error")
