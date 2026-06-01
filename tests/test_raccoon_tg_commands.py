"""Phase R3: Raccoon Telegram manual commands."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.job_runner import Actor
from integrations.tg_commands import (
    _help_text,
    cmd_run_hourly_raccoon,
    cmd_run_raccoon,
    cmd_status,
)


def _make_update(*, chat_id: int = 100, user_id: int = 200) -> MagicMock:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = chat_id
    update.effective_user.id = user_id
    return update


def test_help_text_lists_raccoon_commands() -> None:
    text = _help_text()
    assert "/run_raccoon" in text
    assert "/run_hourly_raccoon" in text


@pytest.mark.parametrize(
    ("handler", "guard_command", "job_type"),
    [
        (cmd_run_raccoon, "run_raccoon", "raccoon_wallet"),
        (cmd_run_hourly_raccoon, "run_hourly_raccoon", "raccoon_hourly"),
    ],
)
def test_raccoon_command_uses_dispatch_job_async(handler, guard_command: str, job_type: str) -> None:
    async def run() -> None:
        update = _make_update()
        context = MagicMock()
        dispatch = AsyncMock(return_value="job-abc")

        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True) as guard:
            with patch("integrations.tg_commands.dispatch_job_async", dispatch):
                with patch("core.job_runner.request_job") as request_job:
                    await handler(update, context)

        guard.assert_awaited_once()
        assert guard.await_args.args[1] == guard_command
        dispatch.assert_awaited_once()
        args, _kwargs = dispatch.await_args
        assert args[0] == job_type
        assert isinstance(args[1], Actor)
        assert args[1].kind == "tg"
        assert args[1].chat_id == 100
        assert args[1].user_id == 200
        request_job.assert_not_called()

    asyncio.run(run())


def test_status_still_works_after_raccoon_commands_added() -> None:
    async def run() -> None:
        update = _make_update()
        context = MagicMock()

        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("integrations.tg_commands._observation_enabled", return_value=False):
                with patch("integrations.tg_commands.get_status", return_value={}):
                    await cmd_status(update, context)

        update.message.reply_text.assert_awaited_once_with("🟢 Сейчас ничего не выполняется.")

    asyncio.run(run())
