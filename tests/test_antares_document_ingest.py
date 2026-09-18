"""Risks of moving Wallet Editor document ingest to modules.antares."""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from telegram.ext import MessageHandler, filters

from automation.runtime import (
    MSG_OPERATOR_INCOMPLETE,
    MSG_OPERATOR_UNMAPPED,
    operator_auth_state_path,
)
from modules.antares import document_ingest as ingest
from tests.unit.isolated_child_env import isolated_child_env, missing_dependency_hint

ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = ROOT / "tests" / "fixtures" / "behavior_baseline"

DEFAULT_USER_ID = 123456789
DEFAULT_PROFILE = "DENIS"

_FORBIDDEN_ON_INGEST_IMPORT = (
    "automation.worker",
    "automation.engine",
    "automation.edit_wallet_contract",
    "automation.add_wallet_contract",
    "automation.add_wallet_engine",
    "integrations.tg_commands",
    "integrations.telegram_bot",
    "playwright",
    "playwright.sync_api",
    "psycopg2",
    "psycopg",
    "asyncpg",
)


@pytest.fixture(autouse=True)
def _sandbox_ingest_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "TMP_DIR", tmp_path / "wallet_editor")


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
        "WALLET_EDITOR_ALLOWED_CHAT_IDS": "-5102627011",
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
    update.message.from_user = MagicMock()
    update.message.from_user.id = user_id
    return update


def _write_disable_xlsx(path: str | Path) -> None:
    pd.DataFrame(
        [{"card": "4111111111111111", "action": "remove_partner", "value": "Ostin"}]
    ).to_excel(path, index=False)


def _mock_xlsx_download(tg_file: AsyncMock, writer) -> None:
    async def _download(*, custom_path=None, **kwargs):
        writer(custom_path)

    tg_file.download_to_drive = AsyncMock(side_effect=_download)


def test_identity_reexport_is_same_function_objects() -> None:
    from integrations import wallet_editor_tg as compat

    assert ingest.handle_wallet_editor_document is compat.handle_wallet_editor_document
    assert ingest.parse_allowed_chat_ids is compat.parse_allowed_chat_ids
    assert ingest.is_wallet_editor_chat_allowed is compat.is_wallet_editor_chat_allowed
    assert ingest.is_xlsx_file_name is compat.is_xlsx_file_name
    assert (
        ingest.log_wallet_editor_allowlist_startup_warning
        is compat.log_wallet_editor_allowlist_startup_warning
    )
    assert not hasattr(compat, "_ALLOWLIST_STARTUP_LOGGED")


def test_mixed_get_handlers_uses_owner_document_callback() -> None:
    from integrations import tg_commands
    from integrations import wallet_editor_tg as compat

    with patch("automation.worker.add_task") as add_task:
        with patch("automation.worker.add_add_wallet_task") as add_add:
            with patch("automation.worker.add_edit_wallet_task") as add_edit:
                handlers = tg_commands.get_handlers()
                documents = [h for h in handlers if isinstance(h, MessageHandler)]
                assert len(documents) == 1
                handler = documents[-1]
                assert handler.callback is ingest.handle_wallet_editor_document
                assert handler.callback is compat.handle_wallet_editor_document
                assert handler.filters is filters.Document.ALL
                add_task.assert_not_called()
                add_add.assert_not_called()
                add_edit.assert_not_called()


def test_compat_source_is_reexport_without_second_body() -> None:
    tree = ast.parse((ROOT / "integrations" / "wallet_editor_tg.py").read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "handle_wallet_editor_document" not in defined
    assert "log_wallet_editor_allowlist_startup_warning" not in defined
    src = (ROOT / "integrations" / "wallet_editor_tg.py").read_text(encoding="utf-8")
    assert "log_wallet_editor_allowlist_startup_warning()" not in src
    assert "_ALLOWLIST_STARTUP_LOGGED" not in src
    owner = ast.parse((ROOT / "modules" / "antares" / "document_ingest.py").read_text(encoding="utf-8"))
    owner_defined = {
        node.name
        for node in ast.walk(owner)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "handle_wallet_editor_document" in owner_defined


def test_expected_tg_commands_json_unchanged() -> None:
    expected = json.loads((_FIXTURE / "expected_tg_commands.json").read_text(encoding="utf-8"))
    assert expected.get("also_registers_document_handler") is True
    assert "handle_wallet_editor_document" not in json.dumps(expected)


def test_missing_message_or_document_exits_quietly() -> None:
    async def run() -> None:
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        no_message = MagicMock()
        no_message.message = None
        no_document = _make_document_update()
        no_document.message.document = None
        with patch("automation.worker.add_task") as add_task:
            await ingest.handle_wallet_editor_document(no_message, context)
            await ingest.handle_wallet_editor_document(no_document, context)
        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        no_document.message.reply_text.assert_not_awaited()

    asyncio.run(run())


def test_deny_allowlist_does_not_download_or_enqueue() -> None:
    async def run() -> None:
        update = _make_document_update(chat_id=999)
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        with patch.dict(
            "os.environ",
            {**_operator_env(), "WALLET_EDITOR_ALLOWED_CHAT_IDS": "-5102627011"},
            clear=True,
        ):
            with patch("automation.worker.add_task") as add_task:
                with patch("automation.worker.add_add_wallet_task") as add_add:
                    with patch("automation.worker.add_edit_wallet_task") as add_edit:
                        await ingest.handle_wallet_editor_document(update, context)
        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        add_add.assert_not_called()
        add_edit.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(
            "⛔ Чат не разрешён для WalletEditor."
        )

    asyncio.run(run())


def test_from_user_fallback_when_effective_user_missing() -> None:
    async def run() -> None:
        update = _make_document_update()
        update.effective_user = None
        update.message.from_user = MagicMock()
        update.message.from_user.id = DEFAULT_USER_ID
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        _mock_xlsx_download(tg_file, _write_disable_xlsx)
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch("automation.worker.add_task", return_value=1) as add_task:
                await ingest.handle_wallet_editor_document(update, context)
        from automation.runtime import WalletEditorTask

        task = add_task.call_args.args[0]
        assert isinstance(task, WalletEditorTask)
        assert task.telegram_user_id == DEFAULT_USER_ID
        assert task.operator_profile == DEFAULT_PROFILE

    asyncio.run(run())


def test_missing_sender_rejected_before_get_file() -> None:
    async def run() -> None:
        update = _make_document_update()
        update.effective_user = None
        update.message.from_user = None
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch("automation.worker.add_task") as add_task:
                await ingest.handle_wallet_editor_document(update, context)
        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_with(MSG_OPERATOR_UNMAPPED)

    asyncio.run(run())


def test_unknown_operator_profile_rejected_before_get_file() -> None:
    async def run() -> None:
        update = _make_document_update(user_id=333)
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        env = {
            "WALLET_EDITOR_OPERATOR_MAP": "333:bad/profile",
            "WALLET_EDITOR_ALLOWED_CHAT_IDS": str(update.effective_chat.id),
        }
        with patch.dict("os.environ", env, clear=True):
            with patch("automation.worker.add_task") as add_task:
                await ingest.handle_wallet_editor_document(update, context)
        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_with(MSG_OPERATOR_UNMAPPED)

    asyncio.run(run())


def test_incomplete_operator_rejected_before_get_file() -> None:
    async def run() -> None:
        update = _make_document_update(user_id=222)
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        env = {
            "WALLET_EDITOR_OPERATOR_MAP": "222:IVAN",
            "WALLET_EDITOR_ALLOWED_CHAT_IDS": str(update.effective_chat.id),
        }
        with patch.dict("os.environ", env, clear=True):
            with patch("automation.worker.add_task") as add_task:
                await ingest.handle_wallet_editor_document(update, context)
        context.bot.get_file.assert_not_called()
        add_task.assert_not_called()
        update.message.reply_text.assert_awaited_with(MSG_OPERATOR_INCOMPLETE)

    asyncio.run(run())


def test_edit_wallet_routes_only_to_edit_queue() -> None:
    async def run() -> None:
        update = _make_document_update(file_name="edit_wallet_batch.xlsx")
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)

        def _write(path):
            pd.DataFrame(
                [{"card": "9990110810347534", "status": "Тест"}]
            ).to_excel(path, index=False)

        _mock_xlsx_download(tg_file, _write)
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch("automation.worker.add_task") as add_task:
                with patch("automation.worker.add_add_wallet_task") as add_add:
                    with patch(
                        "automation.worker.add_edit_wallet_task", return_value=4
                    ) as add_edit:
                        await ingest.handle_wallet_editor_document(update, context)
        from automation.runtime import WalletEditorEditWalletTask

        add_task.assert_not_called()
        add_add.assert_not_called()
        add_edit.assert_called_once()
        task = add_edit.call_args.args[0]
        assert isinstance(task, WalletEditorEditWalletTask)
        assert task.original_filename == "edit_wallet_batch.xlsx"
        assert task.operator_profile == DEFAULT_PROFILE
        assert task.chat_id == update.effective_chat.id
        assert task.user_id == DEFAULT_USER_ID
        assert task.login == "denis-login"
        assert task.password == "denis-pass"
        assert task.auth_state_path == operator_auth_state_path(DEFAULT_PROFILE)
        assert Path(task.file_path).exists()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert "📥 Файл получен" in texts
        assert any(
            "📌 Edit Wallet: файл в очереди профиля DENIS. Очередь: 4." in t
            for t in texts
        )

    asyncio.run(run())


def test_add_wallet_task_fields_include_dry_run() -> None:
    async def run() -> None:
        update = _make_document_update(file_name="add_wallet_batch.xlsx")
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)

        def _write(path):
            pd.DataFrame(
                [{"card": "9990110810347534", "phone": "79491103311"}]
            ).to_excel(path, index=False)

        _mock_xlsx_download(tg_file, _write)
        env = {**_operator_env(), "WALLET_EDITOR_ADD_WALLET_DRY_RUN": "1"}
        with patch.dict("os.environ", env, clear=True):
            with patch("automation.worker.add_task") as add_task:
                with patch(
                    "automation.worker.add_add_wallet_task", return_value=2
                ) as add_add:
                    with patch("automation.worker.add_edit_wallet_task") as add_edit:
                        await ingest.handle_wallet_editor_document(update, context)
        from automation.runtime import WalletEditorAddWalletTask

        add_task.assert_not_called()
        add_edit.assert_not_called()
        task = add_add.call_args.args[0]
        assert isinstance(task, WalletEditorAddWalletTask)
        assert task.dry_run is True
        assert task.original_filename == "add_wallet_batch.xlsx"
        assert task.operator_profile == DEFAULT_PROFILE
        assert task.chat_id == update.effective_chat.id
        assert task.user_id == DEFAULT_USER_ID
        assert task.login == "denis-login"
        assert task.password == "denis-pass"
        assert task.auth_state_path == operator_auth_state_path(DEFAULT_PROFILE)
        assert Path(task.file_path).exists()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert any("dry_run=True" in t for t in texts)

    asyncio.run(run())


def test_fallback_disable_queue_fields_and_file_kept() -> None:
    async def run() -> None:
        update = _make_document_update(file_name="disable.xlsx")
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        _mock_xlsx_download(tg_file, _write_disable_xlsx)
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch("automation.worker.add_task", return_value=5) as add_task:
                with patch("automation.worker.add_add_wallet_task") as add_add:
                    with patch("automation.worker.add_edit_wallet_task") as add_edit:
                        await ingest.handle_wallet_editor_document(update, context)
        from automation.runtime import WalletEditorTask

        add_add.assert_not_called()
        add_edit.assert_not_called()
        task = add_task.call_args.args[0]
        assert isinstance(task, WalletEditorTask)
        assert task.source_file_name == "disable.xlsx"
        assert task.telegram_user_id == DEFAULT_USER_ID
        assert Path(task.file_path).exists()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert any("Текущий размер очереди: 5" in t for t in texts)

    asyncio.run(run())


def test_ambiguous_deletes_temp_file_without_enqueue() -> None:
    async def run() -> None:
        update = _make_document_update(file_name="unknown.xlsx")
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        written: dict[str, str] = {}

        async def _download(*, custom_path=None, **kwargs):
            written["path"] = custom_path
            pd.DataFrame([{"card": "4111111111111111"}]).to_excel(custom_path, index=False)

        tg_file.download_to_drive = AsyncMock(side_effect=_download)
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch("automation.worker.add_task") as add_task:
                with patch("automation.worker.add_add_wallet_task") as add_add:
                    with patch("automation.worker.add_edit_wallet_task") as add_edit:
                        await ingest.handle_wallet_editor_document(update, context)
        add_task.assert_not_called()
        add_add.assert_not_called()
        add_edit.assert_not_called()
        assert written["path"]
        assert not Path(written["path"]).exists()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert any("Не удалось определить тип Excel" in t for t in texts)

    asyncio.run(run())


def test_get_file_error_keeps_no_enqueue() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        context.bot.get_file = AsyncMock(side_effect=RuntimeError("get_file boom"))
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch("automation.worker.add_task") as add_task:
                await ingest.handle_wallet_editor_document(update, context)
        add_task.assert_not_called()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert "📥 Файл получен" in texts
        assert any("Ошибка при приёме файла: get_file boom" in t for t in texts)

    asyncio.run(run())


def test_download_error_does_not_enqueue() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        tg_file.download_to_drive = AsyncMock(side_effect=OSError("download boom"))
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch("automation.worker.add_task") as add_task:
                await ingest.handle_wallet_editor_document(update, context)
        add_task.assert_not_called()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert any("Ошибка при приёме файла: download boom" in t for t in texts)

    asyncio.run(run())


def test_routing_exception_uses_ingest_failed_reply() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        _mock_xlsx_download(tg_file, _write_disable_xlsx)
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "automation.edit_wallet_contract.detect_excel_routing",
                side_effect=RuntimeError("routing boom"),
            ):
                with patch("automation.worker.add_task") as add_task:
                    await ingest.handle_wallet_editor_document(update, context)
        add_task.assert_not_called()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert any("Ошибка при приёме файла: routing boom" in t for t in texts)

    asyncio.run(run())


def test_enqueue_error_keeps_downloaded_file() -> None:
    async def run() -> None:
        update = _make_document_update()
        context = MagicMock()
        tg_file = AsyncMock()
        context.bot.get_file = AsyncMock(return_value=tg_file)
        written: dict[str, str] = {}

        async def _download(*, custom_path=None, **kwargs):
            written["path"] = custom_path
            _write_disable_xlsx(custom_path)

        tg_file.download_to_drive = AsyncMock(side_effect=_download)
        with patch.dict("os.environ", _operator_env(), clear=True):
            with patch(
                "automation.worker.add_task",
                side_effect=RuntimeError("enqueue boom"),
            ):
                await ingest.handle_wallet_editor_document(update, context)
        assert Path(written["path"]).exists()
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert any("Ошибка при приёме файла: enqueue boom" in t for t in texts)

    asyncio.run(run())


def test_compat_tmp_dir_assignment_does_not_redirect_owner(tmp_path) -> None:
    from integrations import wallet_editor_tg as compat

    owner_dir = ingest.TMP_DIR
    compat.TMP_DIR = tmp_path / "compat-ignored"
    assert ingest.TMP_DIR == owner_dir
    assert ingest.TMP_DIR != compat.TMP_DIR


def _startup_script(order: str) -> str:
    return f"""
import json
import logging
from io import StringIO

buf = StringIO()
handler = logging.StreamHandler(buf)
handler.setLevel(logging.DEBUG)
root = logging.getLogger()
root.addHandler(handler)
root.setLevel(logging.DEBUG)

if {order!r} == "owner_then_compat":
    import modules.antares.document_ingest as ingest
    import integrations.wallet_editor_tg as compat
else:
    import integrations.wallet_editor_tg as compat
    import modules.antares.document_ingest as ingest

from modules.antares.document_ingest import log_wallet_editor_allowlist_startup_warning
before = buf.getvalue()
log_wallet_editor_allowlist_startup_warning()
after = buf.getvalue()
print(json.dumps({{
    "same_callback": ingest.handle_wallet_editor_document is compat.handle_wallet_editor_document,
    "before": before,
    "after": after,
    "flag": ingest._ALLOWLIST_STARTUP_LOGGED,
}}))
"""


@pytest.mark.parametrize("order", ["owner_then_compat", "compat_then_owner"])
@pytest.mark.parametrize(
    "allowlist,expect_empty",
    [("", True), ("-5102627011", False)],
)
def test_startup_log_once_in_fresh_subprocess(order: str, allowlist: str, expect_empty: bool) -> None:
    extra = {"WALLET_EDITOR_ALLOWED_CHAT_IDS": allowlist}
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated_child_env(Path(tmp), extra=extra)
        proc = subprocess.run(
            [sys.executable, "-c", _startup_script(order)],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        raise AssertionError(missing_dependency_hint(proc.stderr))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["same_callback"] is True
    assert payload["flag"] is True
    text = payload["before"]
    assert text.count("WALLET_EDITOR_ALLOWED_CHAT_IDS") == (1 if expect_empty else 0)
    if expect_empty:
        assert "fail-closed" in text or "отключён" in text
    else:
        assert "allowed chats configured" in text
        assert "-5102627011" in text
    assert payload["after"] == payload["before"]


def test_import_owner_does_not_load_worker_or_routing() -> None:
    script = (
        "import json, sys, threading\n"
        "before = {t.ident for t in threading.enumerate()}\n"
        "import modules.antares.document_ingest as ingest\n"
        "after = {t.ident for t in threading.enumerate()}\n"
        "print(json.dumps({\n"
        "  'file': ingest.__file__,\n"
        "  'loaded': sorted(m for m in sys.modules if m in "
        + repr(list(_FORBIDDEN_ON_INGEST_IMPORT))
        + "),\n"
        "  'new_threads': sorted(str(x) for x in (after - before)),\n"
        "  'logged': ingest._ALLOWLIST_STARTUP_LOGGED,\n"
        "}))\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated_child_env(Path(tmp))
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        raise AssertionError(missing_dependency_hint(proc.stderr))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["file"].replace("\\", "/").endswith("modules/antares/document_ingest.py")
    assert payload["loaded"] == []
    assert payload["new_threads"] == []
    assert payload["logged"] is True
