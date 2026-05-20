"""Tests for observation-enabled /status (Phase 1a)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.tg_commands import cmd_status


def _make_update() -> MagicMock:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture(autouse=True)
def _observation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSERVATION_ENABLED", raising=False)


def test_status_flag_off_legacy_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    update = _make_update()
    context = MagicMock()

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("integrations.tg_commands.get_status", return_value={}):
                await cmd_status(update, context)

    asyncio.run(run())
    update.message.reply_text.assert_awaited_once_with("🟢 Сейчас ничего не выполняется.")


def test_status_flag_on_shows_health_while_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVATION_ENABLED", "1")
    update = _make_update()
    context = MagicMock()

    health = {
        "scheduler_last_tick_ts": 1_700_000_000.0,
        "scheduler_last_tick_age_sec": 2.5,
        "scheduler_last_error": "none",
        "scheduler_active_schedules_count": 3,
    }
    locks = {
        "wallet": {"lock_age_sec": "none", "lock_pid": "none"},
        "hourly": {"lock_age_sec": "none", "lock_pid": "none"},
        "rate": {"lock_age_sec": "none", "lock_pid": "none"},
        "download": {"lock_age_sec": "none", "lock_pid": "none"},
    }

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("integrations.tg_commands.get_scheduler_health_snapshot", return_value=health):
                with patch("integrations.tg_commands.get_lock_status_for_job_types", return_value=locks):
                    with patch("integrations.tg_commands.get_status", return_value={}):
                        await cmd_status(update, context)

    asyncio.run(run())
    text = update.message.reply_text.await_args.args[0]
    assert "📊 Status" in text
    assert "scheduler: tick_age=2.5s" in text
    assert "active_schedules=3" in text
    assert "locks:" in text
    assert "🟢 idle" in text


def test_status_lock_reader_failure_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVATION_ENABLED", "1")
    update = _make_update()
    context = MagicMock()

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("integrations.tg_commands.get_scheduler_health_snapshot", return_value={}):
                with patch(
                    "integrations.tg_commands.get_lock_status_for_job_types",
                    side_effect=RuntimeError("boom"),
                ):
                    with patch("integrations.tg_commands.get_status", return_value={}):
                        await cmd_status(update, context)

    asyncio.run(run())
    text = update.message.reply_text.await_args.args[0]
    assert "locks:" in text
    assert "unknown" in text
    assert "🟢 idle" in text


def test_status_get_status_failure_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVATION_ENABLED", "1")
    update = _make_update()
    context = MagicMock()

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("integrations.tg_commands.get_scheduler_health_snapshot", return_value={}):
                with patch("integrations.tg_commands.get_lock_status_for_job_types", return_value={}):
                    with patch("integrations.tg_commands.get_status", side_effect=RuntimeError("boom")):
                        await cmd_status(update, context)

    asyncio.run(run())
    text = update.message.reply_text.await_args.args[0]
    assert "jobs:" in text
    assert "unknown" in text
