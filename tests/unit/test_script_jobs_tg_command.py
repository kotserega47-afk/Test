from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from integrations.tg_commands import cmd_run_script_hello


def test_run_script_hello_command_is_guarded():
    update = AsyncMock()
    update.effective_chat.id = 1
    update.effective_user.id = 2
    context = AsyncMock()

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock) as guard:
            guard.return_value = False
            await cmd_run_script_hello(update, context)
            guard.assert_awaited_once_with(update, "run_script_hello")

    asyncio.run(run())


def test_run_script_hello_dispatches_script_job_when_allowed():
    update = AsyncMock()
    update.effective_chat.id = 1
    update.effective_user.id = 2
    context = AsyncMock()

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock) as guard:
            with patch("integrations.tg_commands._run_job_async", new_callable=AsyncMock) as run_job:
                guard.return_value = True
                await cmd_run_script_hello(update, context)
                run_job.assert_awaited_once_with(update, "script_job:hello_world")

    asyncio.run(run())
