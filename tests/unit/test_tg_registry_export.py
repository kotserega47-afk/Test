"""Telegram /registry_export command tests (Phase 4 — TG delivery)."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from telegram.ext import CommandHandler

from core.access_rules import CommandRule
from integrations.wallet_editor_registry_db.registry_export_builder import (
    RegistryExportArtifact,
    RegistryExportSummary,
)
from modules.antares import handlers

MSK = ZoneInfo("Europe/Moscow")
_BUILDER = (
    "integrations.wallet_editor_registry_db.registry_export_builder.build_registry_export_from_postgres"
)
_FORMAT = (
    "integrations.wallet_editor_registry_db.registry_export_builder.format_registry_export_summary"
)


class _MutableRules:
    def __init__(self) -> None:
        self.commands_map: dict = {}
        self.access_map: dict = {}

    def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
        return SimpleNamespace(commands_map=self.commands_map, access_map=self.access_map)


def _artifact(tmp_path: Path) -> RegistryExportArtifact:
    export_path = tmp_path / "wallet_editor_export_20260701_120000_MSK.xlsx"
    export_path.write_bytes(b"fake-xlsx")
    summary = RegistryExportSummary(
        all_results_rows=42,
        runs_rows=7,
        hold_rows=2,
        otlezka_rows=3,
        last_manual_sync_at="2026-07-01T12:00:00+03:00",
        snapshot_hash_short="abc123def456",
        manual_sync_degraded=False,
        generated_at=datetime(2026, 7, 1, 12, 0, 0, tzinfo=MSK).isoformat(),
        filename=export_path.name,
    )
    return RegistryExportArtifact(path=export_path, filename=export_path.name, summary=summary)


def _make_update(*, chat_id: int | None = -100) -> MagicMock:
    update = MagicMock()
    if chat_id is None:
        update.effective_chat = None
    else:
        update.effective_chat.id = chat_id
        update.effective_chat.type = "private"
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    return update


def _allow(rules: _MutableRules, command: str, *, chat_id: int = -100, user_id: int = 123) -> None:
    rules.commands_map[command] = CommandRule(
        required_level=1,
        allow_private=True,
        allow_groups=True,
        enabled=True,
    )
    rules.access_map[("private", user_id)] = 1
    rules.access_map[(chat_id, user_id)] = 1


def _read_input_file_bytes(document: object) -> bytes:
    for attr in ("input_file_content", "obj", "_input_file", "file"):
        value = getattr(document, attr, None)
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if hasattr(value, "read"):
            pos = value.tell() if hasattr(value, "tell") else None
            if hasattr(value, "seek"):
                value.seek(0)
            data = value.read()
            if pos is not None and hasattr(value, "seek"):
                value.seek(pos)
            return data
    raise AssertionError(f"cannot read InputFile payload from {type(document)!r}")


@pytest.fixture
def unbound(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(handlers, "_rules", None)
    monkeypatch.setattr(handlers, "_logger", None)
    return handlers


class TestCmdRegistryExport:
    def test_unbound_and_partial_bind_do_not_export(self, unbound, tmp_path) -> None:
        update = _make_update()
        artifact = _artifact(tmp_path)

        async def _run() -> None:
            with patch(_BUILDER, return_value=artifact) as build_mock:
                with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                    with pytest.raises(unbound.HandlerNotBoundError):
                        await unbound.cmd_registry_export(update, MagicMock())
                    unbound.bind_rules(object())
                    with pytest.raises(unbound.HandlerNotBoundError):
                        await unbound.cmd_registry_export(update, MagicMock())
                    unbound._rules = None
                    unbound.bind_logger(MagicMock())
                    with pytest.raises(unbound.HandlerNotBoundError):
                        await unbound.cmd_registry_export(update, MagicMock())
                    build_mock.assert_not_called()
                    dispatch.assert_not_awaited()

        asyncio.run(_run())
        update.message.reply_text.assert_not_awaited()
        update.message.reply_document.assert_not_awaited()

    def test_acl_deny_blocks_export(self, unbound) -> None:
        update = _make_update()
        rules = _MutableRules()
        logger = MagicMock()
        unbound.bind_rules(rules)
        unbound.bind_logger(logger)
        seen: list[str] = []
        real_guard = unbound.guard_or_deny

        async def _spy(update_obj, command, bound_rules):
            seen.append(command)
            return await real_guard(update_obj, command, bound_rules)

        async def _run() -> None:
            with patch.object(unbound, "guard_or_deny", side_effect=_spy):
                with patch(_BUILDER) as build_mock:
                    with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                        await unbound.cmd_registry_export(update, MagicMock())
                        build_mock.assert_not_called()
                        dispatch.assert_not_awaited()

        asyncio.run(_run())
        assert seen == ["registry_export"]
        update.message.reply_document.assert_not_awaited()
        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert texts
        assert "Building registry export from PostgreSQL" not in texts

    def test_allow_start_then_executor_then_document(self, unbound, tmp_path) -> None:
        update = _make_update()
        artifact = _artifact(tmp_path)
        rules = _MutableRules()
        _allow(rules, "registry_export")
        unbound.bind_rules(rules)
        unbound.bind_logger(MagicMock())
        order: list[str] = []
        threads: list[bool] = []

        def _builder():
            order.append("builder")
            threads.append(threading.current_thread() is not threading.main_thread())
            return artifact

        async def _reply_text(text, *args, **kwargs):
            order.append(f"text:{text}")

        async def _reply_document(**kwargs):
            order.append("document")

        update.message.reply_text = AsyncMock(side_effect=_reply_text)
        update.message.reply_document = AsyncMock(side_effect=_reply_document)

        with (
            patch(_BUILDER, side_effect=_builder) as build_mock,
            patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch,
        ):
            asyncio.run(unbound.cmd_registry_export(update, MagicMock()))

        build_mock.assert_called_once()
        dispatch.assert_not_awaited()
        assert threads == [True]
        assert order[0].startswith("text:📤 Building registry export from PostgreSQL")
        assert order[1] == "builder"
        assert order[2] == "document"

    def test_missing_chat_id_after_allow(self, unbound, tmp_path) -> None:
        class _FalsyChat:
            type = "private"
            id = -100

            def __bool__(self) -> bool:
                return False

        update = _make_update()
        update.effective_chat = _FalsyChat()
        artifact = _artifact(tmp_path)
        rules = _MutableRules()
        _allow(rules, "registry_export")
        unbound.bind_rules(rules)
        unbound.bind_logger(MagicMock())

        with patch(_BUILDER, return_value=artifact) as build_mock:
            asyncio.run(unbound.cmd_registry_export(update, MagicMock()))

        build_mock.assert_not_called()
        update.message.reply_document.assert_not_awaited()
        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert texts == ["❌ Registry export failed: chat_id unavailable"]

    def test_filename_bytes_and_caption(self, unbound, tmp_path) -> None:
        update = _make_update()
        artifact = _artifact(tmp_path)
        rules = _MutableRules()
        _allow(rules, "registry_export")
        unbound.bind_rules(rules)
        unbound.bind_logger(MagicMock())
        captured: dict[str, object] = {}

        async def _capture(**kwargs):
            document = kwargs["document"]
            captured["filename"] = document.filename
            captured["bytes"] = _read_input_file_bytes(document)
            captured["caption"] = kwargs["caption"]

        update.message.reply_document = AsyncMock(side_effect=_capture)

        with patch(_BUILDER, return_value=artifact):
            asyncio.run(unbound.cmd_registry_export(update, MagicMock()))

        assert captured["filename"] == artifact.filename
        assert captured["bytes"] == b"fake-xlsx"
        caption = captured["caption"]
        assert "all_results rows: 42" in caption
        assert "runs rows: 7" in caption
        assert "active hold rows: 2" in caption
        assert "snapshot hash: abc123def456" in caption
        assert "Building registry export from PostgreSQL" in (
            update.message.reply_text.await_args_list[0].args[0]
        )

    @pytest.mark.parametrize("length,extra", [(1024, False), (1025, True)])
    def test_summary_caption_split(self, unbound, tmp_path, length: int, extra: bool) -> None:
        update = _make_update()
        artifact = _artifact(tmp_path)
        rules = _MutableRules()
        _allow(rules, "registry_export")
        unbound.bind_rules(rules)
        unbound.bind_logger(MagicMock())
        summary = "s" * length

        with (
            patch(_BUILDER, return_value=artifact),
            patch(_FORMAT, return_value=summary),
        ):
            asyncio.run(unbound.cmd_registry_export(update, MagicMock()))

        caption = update.message.reply_document.await_args.kwargs["caption"]
        assert caption == summary[:1024]
        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert texts[0] == "📤 Building registry export from PostgreSQL..."
        if extra:
            assert texts[-1] == summary
        else:
            assert texts == ["📤 Building registry export from PostgreSQL..."]

    def test_build_failure_sends_telegram_error(self, unbound) -> None:
        update = _make_update()
        rules = _MutableRules()
        _allow(rules, "registry_export")
        logger = MagicMock()
        unbound.bind_rules(rules)
        unbound.bind_logger(logger)

        with patch(_BUILDER, side_effect=RuntimeError("db read failed")):
            asyncio.run(unbound.cmd_registry_export(update, MagicMock()))

        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert texts[-1] == "❌ Registry export failed: db read failed"
        update.message.reply_document.assert_not_awaited()
        logger.exception.assert_called_once()
        assert logger.exception.call_args.args[0] == "cmd_registry_export failed"

    def test_telegram_send_failure_reported(self, unbound, tmp_path) -> None:
        update = _make_update()
        artifact = _artifact(tmp_path)
        rules = _MutableRules()
        _allow(rules, "registry_export")
        logger = MagicMock()
        unbound.bind_rules(rules)
        unbound.bind_logger(logger)
        update.message.reply_document = AsyncMock(side_effect=RuntimeError("send failed"))

        with patch(_BUILDER, return_value=artifact):
            asyncio.run(unbound.cmd_registry_export(update, MagicMock()))

        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert texts[-1] == "❌ Registry export failed: send failed"
        logger.exception.assert_called_once()
        assert logger.exception.call_args.args[0] == "cmd_registry_export failed"

    def test_file_closed_on_success_and_send_failure(self, unbound, tmp_path) -> None:
        artifact = _artifact(tmp_path)
        rules = _MutableRules()
        _allow(rules, "registry_export")
        opened: list[object] = []
        real_open = Path.open

        def _tracking_open(self, *args, **kwargs):
            handle = real_open(self, *args, **kwargs)
            if self == artifact.path:
                opened.append(handle)
            return handle

        async def _run(*, send_error: bool) -> None:
            opened.clear()
            update = _make_update()
            unbound._rules = None
            unbound._logger = None
            unbound.bind_rules(rules)
            unbound.bind_logger(MagicMock())
            if send_error:
                update.message.reply_document = AsyncMock(side_effect=RuntimeError("send failed"))
            with (
                patch.object(Path, "open", _tracking_open),
                patch(_BUILDER, return_value=artifact),
            ):
                await unbound.cmd_registry_export(update, MagicMock())
            assert opened
            assert all(handle.closed for handle in opened)

        asyncio.run(_run(send_error=False))
        asyncio.run(_run(send_error=True))

    def test_handler_registered_same_callback(self):
        from integrations.tg_commands import cmd_registry_export, get_handlers

        command_names = {h.commands for h in get_handlers() if isinstance(h, CommandHandler)}
        assert {"registry_export"} in command_names
        assembled = {}
        for handler in get_handlers():
            if isinstance(handler, CommandHandler):
                for name in handler.commands:
                    assembled[name] = handler.callback
        assert assembled["registry_export"] is handlers.cmd_registry_export
        assert cmd_registry_export is handlers.cmd_registry_export
