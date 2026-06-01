"""TG control plane: concurrent handlers respond while a job is hung."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.tg_commands import cmd_run_hourly, cmd_status


def test_application_builder_enables_concurrent_updates() -> None:
    src = open("scheduler.py", encoding="utf-8").read()
    assert "concurrent_updates(True)" in src


def test_status_responds_while_hung_job_runs() -> None:
    async def run() -> None:
        hung_started = asyncio.Event()
        release_hung = asyncio.Event()

        async def fake_dispatch(job_type: str, actor) -> str:
            hung_started.set()
            await release_hung.wait()
            return "jid"

        update_hourly = MagicMock()
        update_hourly.message.reply_text = AsyncMock()
        update_hourly.effective_chat.id = 1
        update_hourly.effective_user.id = 2

        update_status = MagicMock()
        update_status.message.reply_text = AsyncMock()

        context = MagicMock()

        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("integrations.tg_commands.dispatch_job_async", side_effect=fake_dispatch):
                with patch("integrations.tg_commands._observation_enabled", return_value=False):
                    with patch("integrations.tg_commands.get_status", return_value={}):
                        hourly_task = asyncio.create_task(cmd_run_hourly(update_hourly, context))
                        await asyncio.wait_for(hung_started.wait(), timeout=2.0)
                        await cmd_status(update_status, context)
                        release_hung.set()
                        await asyncio.wait_for(hourly_task, timeout=2.0)

        update_status.message.reply_text.assert_awaited()
        assert "ничего не выполняется" in update_status.message.reply_text.await_args.args[0]

    asyncio.run(run())
