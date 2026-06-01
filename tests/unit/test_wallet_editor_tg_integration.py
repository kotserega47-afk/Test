"""WE-1: WalletEditor integrated into main Telegram polling."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import CommandHandler, MessageHandler

from automation import engine
from automation.worker import ensure_worker_started
from integrations.tg_commands import cmd_status, get_handlers
from integrations.wallet_editor_tg import (
    handle_wallet_editor_document,
    is_wallet_editor_chat_allowed,
    parse_allowed_chat_ids,
)


def _make_document_update(
    *,
    chat_id: int = -5102627011,
    file_name: str = "batch.xlsx",
    file_path: str | None = None,
    file_id: str = "file-123",
) -> MagicMock:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = chat_id
    document = MagicMock()
    document.file_name = file_name
    document.file_path = file_path
    document.file_id = file_id
    update.message.document = document
    return update


def test_parse_allowed_chat_ids_empty() -> None:
    assert parse_allowed_chat_ids("") == frozenset()
    assert parse_allowed_chat_ids("  ,  ") == frozenset()


def test_parse_allowed_chat_ids_values() -> None:
    assert parse_allowed_chat_ids("-1, 42 ,100") == frozenset({-1, 42, 100})


def test_is_wallet_editor_chat_allowed_fail_closed_when_empty() -> None:
    assert is_wallet_editor_chat_allowed(100, allowed=frozenset()) is False


def test_is_wallet_editor_chat_allowed_member() -> None:
    allowed = frozenset({-5102627011})
    assert is_wallet_editor_chat_allowed(-5102627011, allowed=allowed) is True
    assert is_wallet_editor_chat_allowed(999, allowed=allowed) is False


def test_xlsx_document_queues_task() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock()

        allowed = frozenset({update.effective_chat.id})
        with patch(
            "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
            return_value=True,
        ):
            with patch(
                "integrations.wallet_editor_tg.parse_allowed_chat_ids",
                return_value=allowed,
            ):
                with patch("integrations.wallet_editor_tg.add_task") as add_task:
                    with patch("integrations.wallet_editor_tg.task_queue") as queue:
                        queue.qsize.return_value = 1
                        await handle_wallet_editor_document(update, context)

        add_task.assert_called_once()
        file_path, chat_id = add_task.call_args.args
        assert chat_id == update.effective_chat.id
        assert file_path.endswith(".xlsx")
        assert Path(file_path).name.startswith("wallet_editor_")
        tg_file.download_to_drive.assert_awaited_once()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert "📥 Файл получен" in texts
        assert any("очередь" in t for t in texts)

    asyncio.run(run())


def test_non_xlsx_document_rejected() -> None:
    async def run() -> None:
        update = _make_document_update(file_name="notes.pdf")
        context = MagicMock()

        with patch(
            "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
            return_value=True,
        ):
            with patch("integrations.wallet_editor_tg.add_task") as add_task:
                await handle_wallet_editor_document(update, context)

        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(
            "❌ Принимаются только файлы .xlsx"
        )

    asyncio.run(run())


def test_disallowed_chat_rejected() -> None:
    async def run() -> None:
        update = _make_document_update(chat_id=999)
        context = MagicMock()

        with patch(
            "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
            return_value=False,
        ):
            with patch("integrations.wallet_editor_tg.add_task") as add_task:
                await handle_wallet_editor_document(update, context)

        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(
            "⛔ Чат не разрешён для WalletEditor."
        )

    asyncio.run(run())


def test_handler_does_not_call_engine_run_directly() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock()

        with patch(
            "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
            return_value=True,
        ):
            with patch("integrations.wallet_editor_tg.add_task"):
                with patch.object(engine, "run") as engine_run:
                    await handle_wallet_editor_document(update, context)

        engine_run.assert_not_called()

    asyncio.run(run())


def test_ensure_worker_started_only_once() -> None:
    import automation.worker as worker_mod

    worker_mod._worker_started = False
    with patch.object(worker_mod.threading, "Thread") as mock_thread:
        ensure_worker_started()
        ensure_worker_started()
        assert mock_thread.call_count == 1
        assert mock_thread.call_args.kwargs.get("daemon") is True
        assert mock_thread.call_args.kwargs.get("name") == "wallet-editor-worker"
    worker_mod._worker_started = False


def test_scheduler_starts_wallet_editor_worker_once() -> None:
    src = Path("scheduler.py").read_text(encoding="utf-8")
    assert "ensure_worker_started()" in src
    assert src.count("ensure_worker_started()") == 1
    assert "run_receiver()" not in src
    assert "automation.main" not in src


def test_import_safe_without_antares_credentials() -> None:
    with patch.dict("os.environ", {}, clear=True):
        import importlib

        import integrations.wallet_editor_tg as mod

        importlib.reload(mod)
        import automation.engine
        import automation.runtime
        import automation.worker

        cfg = automation.runtime.RunConfig()
        assert cfg.login == ""
        assert cfg.password == ""


def test_existing_telegram_commands_still_registered() -> None:
    handlers = get_handlers()
    command_names = {
        h.commands
        for h in handlers
        if isinstance(h, CommandHandler)
    }
    assert {"start"} in command_names
    assert {"run_wallet"} in command_names
    assert {"run_raccoon"} in command_names
    assert {"rules_validate"} in command_names
    document_handlers = [h for h in handlers if isinstance(h, MessageHandler)]
    assert len(document_handlers) == 1


def test_status_command_still_works() -> None:
    async def run() -> None:
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_chat.id = 1
        update.effective_user.id = 2
        context = MagicMock()

        with patch(
            "integrations.tg_commands._guard_or_deny",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch("integrations.tg_commands._observation_enabled", return_value=False):
                with patch("integrations.tg_commands.get_status", return_value={}):
                    await cmd_status(update, context)

        update.message.reply_text.assert_awaited_once_with(
            "🟢 Сейчас ничего не выполняется."
        )

    asyncio.run(run())


def test_single_polling_loop_only() -> None:
    scheduler_src = Path("scheduler.py").read_text(encoding="utf-8")
    assert "app.run_polling" in scheduler_src
    assert scheduler_src.count("run_polling") == 1
    assert "getUpdates" not in scheduler_src

    tg_commands_src = Path("integrations/tg_commands.py").read_text(encoding="utf-8")
    assert "run_receiver" not in tg_commands_src
    assert "getUpdates" not in tg_commands_src
