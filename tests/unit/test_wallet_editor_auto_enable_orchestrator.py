from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.job_runner import Actor
from integrations.wallet_editor_auto_enable import (
    PLAN_ONLY_LABEL,
    build_auto_enable_plan,
    build_phase_a_report,
    build_plan_report,
    run_auto_enable,
    run_auto_enable_plan,
)
from integrations.wallet_editor_auto_enable_eligibility import (
    select_auto_enable_candidates,
    split_batches,
)
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from integrations.wallet_editor_registry_lifecycle import STATUS_K_VKLUCHENIYU


def _enabled_settings(**overrides) -> AutoEnableSettings:
    base = dict(
        enabled=True,
        dry_run=True,
        approval_required=True,
        max_rows_per_batch=200,
        max_rows_per_run=0,
        seconds_per_card_timeout=10,
        batch_timeout_buffer_seconds=300,
        working_statuses=("готов к работе", "активный вход", "активный выход"),
        auto_return_statuses=(),
        auto_return_target_status="Готов к работе",
        allowed_statuses_for_enable=(),
        deprecated_working_statuses_fallback=False,
        include_overdue=True,
        telegram_route_report="wallet_editor_auto_enable",
        telegram_route_alert="wallet_editor_auto_enable_alert",
    )
    base.update(overrides)
    return AutoEnableSettings(**base)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "Дата включения": "06.06.2026",
                "Статус включения": STATUS_K_VKLUCHENIYU,
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111",
                "partner": "Ostin",
                "action": "remove_partner",
                "status": "OK",
                "comment": "",
                "hold": "",
            }
        ]
    )


def test_run_auto_enable_disabled_sends_disabled_report():
    settings = _enabled_settings(enabled=False)
    with patch(
        "integrations.wallet_editor_auto_enable._send_to_route",
        return_value=True,
    ) as send_route:
        result = run_auto_enable(settings=settings, manual=True)

    assert result.skipped_reason == "disabled"
    assert "disabled" in result.report_text
    send_route.assert_called_once()


def test_run_auto_enable_plan_only():
    settings = _enabled_settings()
    df = _sample_df()
    with patch(
        "integrations.wallet_editor_auto_enable._send_to_route",
        return_value=True,
    ) as send_route:
        with patch(
            "integrations.wallet_editor_auto_enable.enqueue_auto_enable_batch",
        ) as execute:
            with patch(
                "integrations.wallet_editor_auto_enable.patch_enable_results_in_dropbox_registry",
            ) as patch_registry:
                result = run_auto_enable_plan(
                    actor=Actor(kind="tg", chat_id=-1, user_id=42),
                    manual=True,
                    settings=settings,
                    registry_frames=(df, pd.DataFrame(), pd.DataFrame(), df),
                )

    execute.assert_not_called()
    patch_registry.assert_not_called()
    assert result.skipped_reason is None
    assert PLAN_ONLY_LABEL in result.report_text
    assert "mode: plan-only" in result.report_text
    assert "manual /auto_enable_plan" in result.report_text
    assert "Antares не изменялся" in result.report_text
    assert "eligible before dedup: 1" in result.report_text
    send_route.assert_called_once()


def test_run_auto_enable_dry_run_blocks_execution():
    settings = _enabled_settings(dry_run=True)
    df = _sample_df()
    with patch(
        "integrations.wallet_editor_auto_enable._send_to_route",
        return_value=True,
    ):
        with patch(
            "integrations.wallet_editor_auto_enable.enqueue_auto_enable_batch",
        ) as execute:
            result = run_auto_enable(
                manual=True,
                settings=settings,
                registry_frames=(df, pd.DataFrame(), pd.DataFrame(), df),
            )

    execute.assert_not_called()
    assert "dry_run=1: execution blocked by settings." in result.report_text
    assert result.phase == PLAN_ONLY_LABEL


def test_build_plan_report_includes_remaining_candidates():
    settings = _enabled_settings(max_rows_per_run=1)
    df = pd.concat([_sample_df(), _sample_df().assign(card="4222")], ignore_index=True)
    plan = build_auto_enable_plan(df, settings=settings)
    report = build_plan_report(
        settings=settings,
        plan=plan,
        manual=True,
        trigger="manual /auto_enable_plan",
    )
    assert "remaining candidates after limit: 1" in report
    assert "mode: plan-only" in report


def test_build_phase_a_report_includes_status_whitelists():
    settings = _enabled_settings(
        deprecated_working_statuses_fallback=True,
        allowed_statuses_for_enable=("готов к работе",),
        working_statuses=("готов к работе",),
        auto_return_statuses=("не готов. плановый прозвон",),
    )
    eligibility = select_auto_enable_candidates(_sample_df(), include_overdue=True)
    batches = split_batches(eligibility.selected, max_rows_per_batch=200)
    report = build_phase_a_report(
        settings=settings,
        eligibility=eligibility,
        batches=batches,
        manual=True,
    )
    assert "working_statuses:" in report
    assert "auto_return_statuses:" in report
    assert "auto_return_target_status: Готов к работе" in report
    assert "deprecated allowed_statuses_for_enable fallback" in report


def test_build_phase_a_report_notes_approval_required_skips_execution():
    settings = _enabled_settings(dry_run=False, approval_required=True)
    eligibility = select_auto_enable_candidates(_sample_df(), include_overdue=True)
    batches = split_batches(eligibility.selected, max_rows_per_batch=200)
    report = build_phase_a_report(
        settings=settings,
        eligibility=eligibility,
        batches=batches,
        manual=True,
        actor=None,
    )
    assert "approval_required=1: plan only, execution skipped" in report


def test_cmd_auto_enable_plan_uses_guard_and_runs_plan_only():
    from integrations.tg_commands import cmd_auto_enable_plan

    update = MagicMock()
    update.effective_chat.id = -100
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    with patch(
        "integrations.tg_commands._guard_or_deny",
        new=AsyncMock(return_value=False),
    ) as guard:
        with patch("integrations.tg_commands.run_auto_enable_plan") as plan_fn:
            asyncio.run(cmd_auto_enable_plan(update, MagicMock()))
            guard.assert_awaited_once_with(update, "auto_enable_plan")
            plan_fn.assert_not_called()

    with patch(
        "integrations.tg_commands._guard_or_deny",
        new=AsyncMock(return_value=True),
    ):
        with patch(
            "integrations.tg_commands.run_auto_enable_plan",
            return_value=MagicMock(skipped_reason=None, sent=True),
        ) as plan_fn:
            asyncio.run(cmd_auto_enable_plan(update, MagicMock()))
            plan_fn.assert_called_once()
            assert plan_fn.call_args.kwargs["manual"] is True


def test_cmd_auto_enable_run_uses_guard_and_runs_orchestrator():
    from integrations.tg_commands import cmd_auto_enable_run

    update = MagicMock()
    update.effective_chat.id = -100
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    with patch(
        "integrations.tg_commands._guard_or_deny",
        new=AsyncMock(return_value=False),
    ) as guard:
        with patch("integrations.tg_commands.run_auto_enable") as run_fn:
            asyncio.run(cmd_auto_enable_run(update, MagicMock()))
            guard.assert_awaited_once_with(update, "auto_enable_run")
            run_fn.assert_not_called()

    with patch(
        "integrations.tg_commands._guard_or_deny",
        new=AsyncMock(return_value=True),
    ):
        with patch(
            "integrations.tg_commands.run_auto_enable",
            return_value=MagicMock(skipped_reason=None, sent=True),
        ) as run_fn:
            asyncio.run(cmd_auto_enable_run(update, MagicMock()))
            run_fn.assert_called_once()
            actor = run_fn.call_args.args[0]
            assert actor.kind == "tg"
            assert actor.user_id == 123
            assert run_fn.call_args.kwargs["manual"] is True
