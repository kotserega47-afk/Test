from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.access_rules import CommandRule
from core.job_runner import Actor
from modules.antares import handlers as antares_handlers
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
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError
from integrations.wallet_editor_registry_lifecycle import STATUS_K_VKLUCHENIYU


@pytest.fixture(autouse=True)
def _isolate_auto_enable_local_state(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Keep orchestrator tests off the working outbox and away from PG/network."""

    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.connection.get_database_url",
        lambda: None,
    )

    @contextmanager
    def _blocked_connect(*, for_mirror: bool = False):
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")
        yield  # pragma: no cover

    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.connection.connect",
        _blocked_connect,
    )


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


_PLAN_ORCH = "integrations.wallet_editor_auto_enable.run_auto_enable_plan"
_RUN_ORCH = "integrations.wallet_editor_auto_enable.run_auto_enable"
_PLAN_START = "🧩 Строю WalletEditor Auto-Enable plan (plan-only)..."
_RUN_START = "🧩 Запускаю WalletEditor Auto-Enable (fresh plan + execution)..."


class _MutableRules:
    def __init__(self) -> None:
        self.commands_map: dict = {}
        self.access_map: dict = {}

    def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
        return SimpleNamespace(commands_map=self.commands_map, access_map=self.access_map)


def _allow(rules: _MutableRules, command: str, *, chat_id: int = -100, user_id: int = 123) -> None:
    rules.commands_map[command] = CommandRule(
        required_level=1,
        allow_private=True,
        allow_groups=True,
        enabled=True,
    )
    rules.access_map[("private", user_id)] = 1
    rules.access_map[(chat_id, user_id)] = 1


def _update(*, chat_id: int = -100, user_id: int = 123) -> MagicMock:
    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = chat_id
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture
def unbound_handlers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(antares_handlers, "_rules", None)
    monkeypatch.setattr(antares_handlers, "_logger", None)
    return antares_handlers


def _bind_allow(unbound, command: str):
    rules = _MutableRules()
    _allow(rules, command)
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    return logger


@pytest.mark.parametrize(
    "callback,command,orch_path,other_path",
    [
        (antares_handlers.cmd_auto_enable_plan, "auto_enable_plan", _PLAN_ORCH, _RUN_ORCH),
        (antares_handlers.cmd_auto_enable_run, "auto_enable_run", _RUN_ORCH, _PLAN_ORCH),
    ],
)
def test_cmd_auto_enable_unbound_skips_orchestrator(
    unbound_handlers, callback, command, orch_path, other_path
):
    update = _update()

    async def _run() -> None:
        with patch(orch_path) as orch:
            with patch(other_path) as other:
                with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                    with pytest.raises(unbound_handlers.HandlerNotBoundError):
                        await callback(update, MagicMock())
                    unbound_handlers.bind_rules(object())
                    with pytest.raises(unbound_handlers.HandlerNotBoundError):
                        await callback(update, MagicMock())
                    unbound_handlers._rules = None
                    unbound_handlers.bind_logger(MagicMock())
                    with pytest.raises(unbound_handlers.HandlerNotBoundError):
                        await callback(update, MagicMock())
                    orch.assert_not_called()
                    other.assert_not_called()
                    dispatch.assert_not_awaited()

    asyncio.run(_run())
    update.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize(
    "callback,command,orch_path",
    [
        (antares_handlers.cmd_auto_enable_plan, "auto_enable_plan", _PLAN_ORCH),
        (antares_handlers.cmd_auto_enable_run, "auto_enable_run", _RUN_ORCH),
    ],
)
def test_cmd_auto_enable_deny_uses_real_acl(unbound_handlers, callback, command, orch_path):
    update = _update()
    unbound_handlers.bind_rules(_MutableRules())
    unbound_handlers.bind_logger(MagicMock())
    seen: list[str] = []
    real_guard = unbound_handlers.guard_or_deny

    async def _spy(update_obj, cmd, rules):
        seen.append(cmd)
        return await real_guard(update_obj, cmd, rules)

    with patch.object(unbound_handlers, "guard_or_deny", side_effect=_spy):
        with patch(orch_path) as orch:
            with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                asyncio.run(callback(update, MagicMock()))
                orch.assert_not_called()
                dispatch.assert_not_awaited()

    assert seen == [command]
    texts = [call.args[0] for call in update.message.reply_text.await_args_list]
    assert _PLAN_START not in texts
    assert _RUN_START not in texts


def test_cmd_auto_enable_plan_allow_actor_order_and_thread(unbound_handlers):
    update = _update()
    _bind_allow(unbound_handlers, "auto_enable_plan")
    order: list[str] = []
    off_loop: list[bool] = []

    def _plan(actor, *, manual):
        order.append("orch")
        off_loop.append(threading.current_thread() is not threading.main_thread())
        assert isinstance(actor, Actor)
        assert actor.kind == "tg"
        assert actor.chat_id == -100
        assert actor.user_id == 123
        assert manual is True
        return SimpleNamespace(skipped_reason=None, sent=True)

    async def _reply(text, *args, **kwargs):
        order.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)

    with patch(_PLAN_ORCH, side_effect=_plan) as plan_fn:
        with patch(_RUN_ORCH) as run_fn:
            with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                asyncio.run(unbound_handlers.cmd_auto_enable_plan(update, MagicMock()))
                run_fn.assert_not_called()
                dispatch.assert_not_awaited()

    plan_fn.assert_called_once()
    assert off_loop == [True]
    assert order[0] == _PLAN_START
    assert order[1] == "orch"
    assert order[2] == "✅ Plan-only report sent=True. Antares/registry unchanged."


def test_cmd_auto_enable_run_allow_actor_order_and_thread(unbound_handlers):
    update = _update()
    _bind_allow(unbound_handlers, "auto_enable_run")
    order: list[str] = []
    off_loop: list[bool] = []

    def _run(actor, *, manual):
        order.append("orch")
        off_loop.append(threading.current_thread() is not threading.main_thread())
        assert isinstance(actor, Actor)
        assert actor.kind == "tg"
        assert actor.chat_id == -100
        assert actor.user_id == 123
        assert manual is True
        return SimpleNamespace(skipped_reason=None, phase="executed", sent=True)

    async def _reply(text, *args, **kwargs):
        order.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)

    with patch(_RUN_ORCH, side_effect=_run) as run_fn:
        with patch(_PLAN_ORCH) as plan_fn:
            with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                asyncio.run(unbound_handlers.cmd_auto_enable_run(update, MagicMock()))
                plan_fn.assert_not_called()
                dispatch.assert_not_awaited()

    run_fn.assert_called_once()
    assert off_loop == [True]
    assert order[0] == _RUN_START
    assert order[1] == "orch"
    assert order[2] == "✅ Auto-Enable execution finished. Telegram report sent=True"


@pytest.mark.parametrize(
    "result,expected",
    [
        (
            SimpleNamespace(skipped_reason="disabled"),
            "ℹ️ Auto-Enable disabled (job_params enabled=0).",
        ),
        (
            SimpleNamespace(skipped_reason="error"),
            "⚠️ Auto-Enable plan failed. См. route-отчёт.",
        ),
        (
            SimpleNamespace(skipped_reason=None, sent=True),
            "✅ Plan-only report sent=True. Antares/registry unchanged.",
        ),
    ],
)
def test_cmd_auto_enable_plan_result_branches(unbound_handlers, result, expected):
    update = _update()
    _bind_allow(unbound_handlers, "auto_enable_plan")
    with patch(_PLAN_ORCH, return_value=result):
        asyncio.run(unbound_handlers.cmd_auto_enable_plan(update, MagicMock()))
    texts = [call.args[0] for call in update.message.reply_text.await_args_list]
    assert texts[0] == _PLAN_START
    assert texts[-1] == expected


@pytest.mark.parametrize(
    "result,expected",
    [
        (
            SimpleNamespace(skipped_reason="disabled"),
            "ℹ️ Auto-Enable disabled (job_params enabled=0).",
        ),
        (
            SimpleNamespace(skipped_reason="error"),
            "⚠️ Auto-Enable run failed. См. route-отчёт.",
        ),
        (
            SimpleNamespace(skipped_reason=None, phase="plan-only"),
            "ℹ️ Execution blocked by settings (dry_run=1). Plan-only report sent.",
        ),
        (
            SimpleNamespace(skipped_reason=None, phase="executed", sent=False),
            "✅ Auto-Enable execution finished. Telegram report sent=False",
        ),
    ],
)
def test_cmd_auto_enable_run_result_branches(unbound_handlers, result, expected):
    update = _update()
    _bind_allow(unbound_handlers, "auto_enable_run")
    with patch(_RUN_ORCH, return_value=result):
        asyncio.run(unbound_handlers.cmd_auto_enable_run(update, MagicMock()))
    texts = [call.args[0] for call in update.message.reply_text.await_args_list]
    assert texts[0] == _RUN_START
    assert texts[-1] == expected


def test_cmd_auto_enable_plan_orchestrator_exception(unbound_handlers):
    update = _update()
    logger = _bind_allow(unbound_handlers, "auto_enable_plan")
    with patch(_PLAN_ORCH, side_effect=RuntimeError("plan boom")):
        asyncio.run(unbound_handlers.cmd_auto_enable_plan(update, MagicMock()))
    logger.exception.assert_called_once()
    assert logger.exception.call_args.args[0] == "cmd_auto_enable_plan failed"
    texts = [call.args[0] for call in update.message.reply_text.await_args_list]
    assert texts[0] == _PLAN_START
    assert texts[-1] == "❌ /auto_enable_plan failed: RuntimeError: plan boom"


def test_cmd_auto_enable_run_orchestrator_exception(unbound_handlers):
    update = _update()
    logger = _bind_allow(unbound_handlers, "auto_enable_run")
    with patch(_RUN_ORCH, side_effect=RuntimeError("run boom")):
        asyncio.run(unbound_handlers.cmd_auto_enable_run(update, MagicMock()))
    logger.exception.assert_called_once()
    assert logger.exception.call_args.args[0] == "cmd_auto_enable_run failed"
    texts = [call.args[0] for call in update.message.reply_text.await_args_list]
    assert texts[0] == _RUN_START
    assert texts[-1] == "❌ /auto_enable_run failed: RuntimeError: run boom"
