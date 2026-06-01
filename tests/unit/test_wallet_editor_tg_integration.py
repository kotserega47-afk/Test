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
from automation.runtime import (
    MSG_OPERATOR_INCOMPLETE,
    MSG_OPERATOR_UNMAPPED,
    WalletEditorTask,
    normalize_profile_key,
    operator_auth_state_path,
    parse_operator_map,
    resolve_operator_for_user,
)
from integrations.wallet_editor_tg import (
    handle_wallet_editor_document,
    is_wallet_editor_chat_allowed,
    is_xlsx_file_name,
    parse_allowed_chat_ids,
)

DEFAULT_USER_ID = 123456789
DEFAULT_PROFILE = "DENIS"


def _operator_env(
    *,
    user_id: int = DEFAULT_USER_ID,
    profile: str = DEFAULT_PROFILE,
    login: str = "denis-login",
    password: str = "denis-pass",
) -> dict[str, str]:
    profile_key = profile.upper()
    return {
        "WALLET_EDITOR_OPERATOR_MAP": f"{user_id}:{profile_key}",
        f"WALLET_EDITOR_OPERATOR_{profile_key}_LOGIN": login,
        f"WALLET_EDITOR_OPERATOR_{profile_key}_PASSWORD": password,
    }


def _make_document_update(
    *,
    chat_id: int = -5102627011,
    file_name: str = "batch.xlsx",
    file_id: str = "file-123",
    user_id: int = DEFAULT_USER_ID,
) -> MagicMock:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    document = MagicMock(spec=["file_name", "file_id"])
    document.file_name = file_name
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


def test_wallet_editor_uses_dedicated_allowlist_env() -> None:
    with patch.dict(
        "os.environ",
        {"WALLET_EDITOR_ALLOWED_CHAT_IDS": "-5102627011,42"},
        clear=True,
    ):
        assert parse_allowed_chat_ids() == frozenset({-5102627011, 42})
        assert is_wallet_editor_chat_allowed(-5102627011) is True
        assert is_wallet_editor_chat_allowed(999) is False


def test_telegram_allowed_chat_ids_does_not_affect_wallet_editor() -> None:
    with patch.dict(
        "os.environ",
        {
            "TELEGRAM_ALLOWED_CHAT_IDS": "-999,111",
            "WALLET_EDITOR_ALLOWED_CHAT_IDS": "",
        },
        clear=True,
    ):
        assert parse_allowed_chat_ids() == frozenset()
        assert is_wallet_editor_chat_allowed(-999) is False


def test_empty_wallet_editor_allowlist_is_fail_closed() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert parse_allowed_chat_ids() == frozenset()
        assert is_wallet_editor_chat_allowed(-5102627011) is False


def test_startup_warning_when_allowlist_empty(caplog) -> None:
    import integrations.wallet_editor_tg as mod

    mod._ALLOWLIST_STARTUP_LOGGED = False
    with patch.dict("os.environ", {}, clear=True):
        with caplog.at_level("WARNING"):
            mod.log_wallet_editor_allowlist_startup_warning()

    assert "WALLET_EDITOR_ALLOWED_CHAT_IDS" in caplog.text
    assert "fail-closed" in caplog.text


def test_disallowed_chat_rejected_via_allowlist() -> None:
    async def run() -> None:
        update = _make_document_update(chat_id=999)
        context = MagicMock()

        with patch.dict(
            "os.environ",
            {"WALLET_EDITOR_ALLOWED_CHAT_IDS": "-5102627011"},
            clear=True,
        ):
            with patch("integrations.wallet_editor_tg.add_task") as add_task:
                await handle_wallet_editor_document(update, context)

        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(
            "⛔ Чат не разрешён для WalletEditor."
        )

    asyncio.run(run())


def test_allowed_chat_accepts_xlsx_via_allowlist() -> None:
    async def run() -> None:
        update = _make_document_update(chat_id=-5102627011)
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock()

        with patch.dict(
            "os.environ",
            {**_operator_env(), "WALLET_EDITOR_ALLOWED_CHAT_IDS": "-5102627011"},
            clear=True,
        ):
            with patch("integrations.wallet_editor_tg.add_task", return_value=1) as add_task:
                await handle_wallet_editor_document(update, context)

        add_task.assert_called_once()

    asyncio.run(run())


def test_is_xlsx_file_name() -> None:
    assert is_xlsx_file_name("batch.xlsx") is True
    assert is_xlsx_file_name("batch.XLSX") is True
    assert is_xlsx_file_name("notes.pdf") is False
    assert is_xlsx_file_name("") is False
    assert is_xlsx_file_name(None) is False
    assert is_xlsx_file_name("   ") is False


def test_document_without_file_path_does_not_crash() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock()

        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
                return_value=True,
            ):
                with patch("integrations.wallet_editor_tg.add_task") as add_task:
                    await handle_wallet_editor_document(update, context)

        assert not hasattr(update.message.document, "file_path")
        add_task.assert_called_once()

    asyncio.run(run())


def test_non_xlsx_rejected_before_get_file() -> None:
    async def run() -> None:
        update = _make_document_update(file_name="notes.pdf")
        context = MagicMock()
        context.bot.get_file = AsyncMock()

        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
                return_value=True,
            ):
                with patch("integrations.wallet_editor_tg.add_task") as add_task:
                    await handle_wallet_editor_document(update, context)

        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(
            "❌ Принимаются только файлы .xlsx"
        )

    asyncio.run(run())


def test_empty_file_name_rejected_before_get_file() -> None:
    async def run() -> None:
        update = _make_document_update(file_name="")
        context = MagicMock()
        context.bot.get_file = AsyncMock()

        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
                return_value=True,
            ):
                with patch("integrations.wallet_editor_tg.add_task") as add_task:
                    await handle_wallet_editor_document(update, context)

        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(
            "❌ Принимаются только файлы .xlsx"
        )

    asyncio.run(run())


def test_xlsx_calls_get_file_with_document_file_id() -> None:
    async def run() -> None:
        update = _make_document_update(file_id="doc-file-abc")
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock()

        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
                return_value=True,
            ):
                with patch("integrations.wallet_editor_tg.add_task"):
                    await handle_wallet_editor_document(update, context)

        context.bot.get_file.assert_awaited_once_with("doc-file-abc")

    asyncio.run(run())


def test_xlsx_document_queues_task() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock()

        allowed = frozenset({update.effective_chat.id})
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
                return_value=True,
            ):
                with patch(
                    "integrations.wallet_editor_tg.parse_allowed_chat_ids",
                    return_value=allowed,
                ):
                    with patch("integrations.wallet_editor_tg.add_task", return_value=1) as add_task:
                            await handle_wallet_editor_document(update, context)

        add_task.assert_called_once()
        context.bot.get_file.assert_awaited_once_with("file-123")
        task = add_task.call_args.args[0]
        assert isinstance(task, WalletEditorTask)
        assert task.chat_id == update.effective_chat.id
        assert task.telegram_user_id == DEFAULT_USER_ID
        assert task.operator_profile == DEFAULT_PROFILE
        assert task.login == "denis-login"
        assert task.password == "denis-pass"
        assert task.auth_state_path == operator_auth_state_path(DEFAULT_PROFILE)
        assert task.file_path.endswith(".xlsx")
        assert Path(task.file_path).name.startswith("wallet_editor_")
        tg_file.download_to_drive.assert_awaited_once()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert "📥 Файл получен" in texts
        assert any("очередь профиля DENIS" in t for t in texts)

    asyncio.run(run())


def test_non_xlsx_document_rejected() -> None:
    async def run() -> None:
        update = _make_document_update(file_name="notes.pdf")
        context = MagicMock()
        context.bot.get_file = AsyncMock()

        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
                return_value=True,
            ):
                with patch("integrations.wallet_editor_tg.add_task") as add_task:
                    await handle_wallet_editor_document(update, context)

        context.bot.get_file.assert_not_called()
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

        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
                return_value=True,
            ):
                with patch("integrations.wallet_editor_tg.add_task"):
                    with patch.object(engine, "run") as engine_run:
                        await handle_wallet_editor_document(update, context)

        engine_run.assert_not_called()

    asyncio.run(run())


def test_ensure_worker_started_is_no_op_for_scheduler_compat() -> None:
    import automation.worker as worker_mod

    with patch.object(worker_mod.threading, "Thread") as mock_thread:
        ensure_worker_started()
        ensure_worker_started()
        mock_thread.assert_not_called()


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


def test_mapped_user_queues_task_with_operator_credentials() -> None:
    async def run() -> None:
        update = _make_document_update(user_id=111, chat_id=-1003429793111)
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock()
        env = {
            **_operator_env(user_id=111, profile="DENIS", login="d-login", password="d-pass"),
            "WALLET_EDITOR_ALLOWED_CHAT_IDS": "-1003429793111",
        }

        with patch.dict("os.environ", env, clear=True):
            with patch("integrations.wallet_editor_tg.add_task") as add_task:
                await handle_wallet_editor_document(update, context)

        task = add_task.call_args.args[0]
        assert task.operator_profile == "DENIS"
        assert task.login == "d-login"
        assert task.password == "d-pass"
        assert task.auth_state_path == "/tmp/auth_state_wallet_editor_DENIS.json"

    asyncio.run(run())


def test_unmapped_user_rejected_before_get_file() -> None:
    async def run() -> None:
        update = _make_document_update(user_id=999999)
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        env = {
            **_operator_env(user_id=111),
            "WALLET_EDITOR_ALLOWED_CHAT_IDS": str(update.effective_chat.id),
        }

        with patch.dict("os.environ", env, clear=True):
            with patch("integrations.wallet_editor_tg.add_task") as add_task:
                await handle_wallet_editor_document(update, context)

        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_with(MSG_OPERATOR_UNMAPPED)

    asyncio.run(run())


def test_mapped_user_missing_credentials_rejected() -> None:
    async def run() -> None:
        update = _make_document_update(user_id=222)
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        env = {
            "WALLET_EDITOR_OPERATOR_MAP": "222:IVAN",
            "WALLET_EDITOR_ALLOWED_CHAT_IDS": str(update.effective_chat.id),
        }

        with patch.dict("os.environ", env, clear=True):
            with patch("integrations.wallet_editor_tg.add_task") as add_task:
                await handle_wallet_editor_document(update, context)

        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_with(MSG_OPERATOR_INCOMPLETE)

    asyncio.run(run())


def test_invalid_profile_key_in_map_is_ignored_fail_closed() -> None:
    assert parse_operator_map("333:bad/profile") == {}
    with patch.dict("os.environ", {"WALLET_EDITOR_OPERATOR_MAP": "333:bad/profile"}, clear=True):
        creds, msg = resolve_operator_for_user(333)
    assert creds is None
    assert msg == MSG_OPERATOR_UNMAPPED


def test_normalize_profile_key_rejects_path_traversal() -> None:
    assert normalize_profile_key("../DENIS") is None
    assert normalize_profile_key("denis") == "DENIS"
    assert operator_auth_state_path("DENIS") == "/tmp/auth_state_wallet_editor_DENIS.json"
    assert ".." not in operator_auth_state_path("DENIS")


def test_different_users_get_different_auth_state_paths() -> None:
    assert operator_auth_state_path("DENIS") != operator_auth_state_path("IVAN")


def test_handler_does_not_fallback_to_wallet_editor_antares_login() -> None:
    src = Path("integrations/wallet_editor_tg.py").read_text(encoding="utf-8")
    assert "WALLET_EDITOR_ANTARES_LOGIN" not in src
    assert "wallet_editor_antares_login" not in src


def test_handler_uses_profile_queue_size_from_add_task() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock()

        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "integrations.wallet_editor_tg.is_wallet_editor_chat_allowed",
                return_value=True,
            ):
                with patch("integrations.wallet_editor_tg.add_task", return_value=3) as add_task:
                    await handle_wallet_editor_document(update, context)

        add_task.assert_called_once()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert any(
            "📌 Файл добавлен в очередь профиля DENIS. Текущий размер очереди: 3" in t
            for t in texts
        )

    asyncio.run(run())


def test_import_safe_without_operator_map() -> None:
    with patch.dict("os.environ", {}, clear=True):
        import importlib
        import automation.runtime as runtime_mod

        importlib.reload(runtime_mod)
        assert runtime_mod.parse_operator_map() == {}
        creds, msg = runtime_mod.resolve_operator_for_user(123)
        assert creds is None
        assert msg == MSG_OPERATOR_UNMAPPED
