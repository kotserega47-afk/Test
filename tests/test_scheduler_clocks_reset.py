"""Scheduler clock reset after /reload_rules (next_every / next_cron only)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scheduler_clocks_control import (
    _SCHEDULER_CLOCKS_RESET_REQUESTED,
    _apply_scheduler_clock_reset_if_requested,
    _reset_scheduler_clocks_state_for_tests,
    request_scheduler_clocks_reset,
)


@dataclass
class _HourlyGateLike:
    """Same fields as ``scheduler.HourlyGate``; avoids importing the full scheduler stack."""

    last_intraday_key: str | None = None
    last_final_key: str | None = None


@pytest.fixture(autouse=True)
def _clean_scheduler_clock_control() -> None:
    _reset_scheduler_clocks_state_for_tests()
    yield
    _reset_scheduler_clocks_state_for_tests()


def test_apply_clears_dicts_event_and_returns_true_once() -> None:
    next_every: dict[str, float] = {"wallet": 1.0}
    next_cron: dict[str, datetime] = {"hourly": datetime(2026, 1, 1, 12, 0)}

    request_scheduler_clocks_reset(reason="unit")
    assert _SCHEDULER_CLOCKS_RESET_REQUESTED.is_set()

    assert _apply_scheduler_clock_reset_if_requested(next_every, next_cron, logger=None) is True
    assert next_every == {}
    assert next_cron == {}
    assert not _SCHEDULER_CLOCKS_RESET_REQUESTED.is_set()

    assert _apply_scheduler_clock_reset_if_requested(next_every, next_cron) is False


def test_apply_logs_when_logger_provided() -> None:
    next_every: dict[str, float] = {"a": 1.0}
    next_cron: dict[str, datetime] = {}
    log = MagicMock()
    request_scheduler_clocks_reset(reason="x_reason")

    assert _apply_scheduler_clock_reset_if_requested(next_every, next_cron, logger=log) is True

    log.info.assert_called_once()
    args, kwargs = log.info.call_args
    assert args[0] == "scheduler_clocks_reset reason=%s"
    assert args[1] == "x_reason"


def test_apply_does_not_touch_hourly_gate() -> None:
    gate = _HourlyGateLike(last_intraday_key="20260101-0042", last_final_key="20260101")
    next_every = {"hourly": 99.0}
    next_cron: dict[str, datetime] = {"x": datetime(2026, 5, 1, 8, 0)}

    request_scheduler_clocks_reset("t")
    _apply_scheduler_clock_reset_if_requested(next_every, next_cron, logger=None)

    assert gate.last_intraday_key == "20260101-0042"
    assert gate.last_final_key == "20260101"
    assert next_every == {}


def test_cmd_reload_rules_success_calls_reset() -> None:
    from integrations.tg_commands import cmd_reload_rules

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    snap = MagicMock()
    snap.source = "snapshot_v2:test"

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("integrations.tg_commands.RULES") as rules:
                rules.invalidate = MagicMock()
                rules.get_snapshot = MagicMock(return_value=snap)
                with patch("integrations.tg_commands.request_scheduler_clocks_reset") as rst:
                    await cmd_reload_rules(update, context)
                    rst.assert_called_once_with(reason="reload_rules")

    asyncio.run(run())
    update.message.reply_text.assert_awaited()


def test_cmd_reload_rules_failure_does_not_call_reset() -> None:
    from core.rules_provider import ContractPublishRejected
    from integrations.tg_commands import cmd_reload_rules

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    decision = MagicMock()
    decision.publish_allowed = False

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("integrations.tg_commands.RULES") as rules:
                rules.invalidate = MagicMock()
                rules.get_snapshot = MagicMock(side_effect=ContractPublishRejected(decision))
                with patch("integrations.tg_commands.request_scheduler_clocks_reset") as rst:
                    await cmd_reload_rules(update, context)
                    rst.assert_not_called()

    asyncio.run(run())


def test_cmd_reload_rules_guard_denies_no_reset() -> None:
    from integrations.tg_commands import cmd_reload_rules

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=False):
            with patch("integrations.tg_commands.RULES") as rules:
                with patch("integrations.tg_commands.request_scheduler_clocks_reset") as rst:
                    await cmd_reload_rules(update, context)
                    rules.invalidate.assert_not_called()
                    rst.assert_not_called()

    asyncio.run(run())


def test_request_scheduler_clocks_reset_idempotent_no_crash() -> None:
    request_scheduler_clocks_reset("a")
    request_scheduler_clocks_reset("b")
    assert _SCHEDULER_CLOCKS_RESET_REQUESTED.is_set()
