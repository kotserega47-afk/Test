from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.access_rules import AccessRules
from modules.antares.assembly import assemble_antares
from modules.antares.handlers import ANTARES_STATUS_JOB_TYPES, cmd_help, cmd_status, cmd_whoami


def _update() -> MagicMock:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat = SimpleNamespace(id=11, type="private")
    update.effective_user = SimpleNamespace(id=22, username="alice")
    return update


def test_antares_help_and_status_and_whoami():
    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-cmds")
    assemble_antares(rules=rules, logger=logger)
    update = _update()

    async def _help() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            await cmd_help(update, MagicMock())

    asyncio.run(_help())
    help_text = update.message.reply_text.await_args.args[0]
    assert "/run_wallet" in help_text
    assert "/operator_wallets_ready" in help_text
    assert "/run_raccoon" not in help_text
    assert "/run_hourly_raccoon" not in help_text
    assert "/run_script_hello" not in help_text

    update.message.reply_text.reset_mock()

    async def _status() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("modules.antares.handlers.get_status", return_value={"raccoon_wallet": {"job_id": "x", "started_ts": 1, "runtime_sec": 1}}):
                await cmd_status(update, MagicMock())

    asyncio.run(_status())
    assert update.message.reply_text.await_args.args[0] == "🟢 Сейчас ничего не выполняется."

    update.message.reply_text.reset_mock()
    snap = SimpleNamespace(access_map={("private", 22): 2})

    async def _whoami() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch.object(rules, "get_snapshot", return_value=snap):
                await cmd_whoami(update, MagicMock())

    asyncio.run(_whoami())
    who = update.message.reply_text.await_args.args[0]
    assert "chat_id: 11" in who
    assert "user_id: 22" in who
    assert "level: 2" in who

    assert "raccoon_wallet" not in ANTARES_STATUS_JOB_TYPES
    assert "script_job:operator_wallets_ready" in ANTARES_STATUS_JOB_TYPES
    assert "wallet_editor_registry_replay" in ANTARES_STATUS_JOB_TYPES
