"""Telegram /registry_export command tests (Phase 4 — TG delivery)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from integrations.wallet_editor_registry_db.registry_export_builder import (
    RegistryExportArtifact,
    RegistryExportSummary,
)
from integrations.tg_commands import cmd_registry_export, get_handlers
from telegram.ext import CommandHandler

MSK = ZoneInfo("Europe/Moscow")


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


def _make_update() -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = -100
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    return update


class TestCmdRegistryExport:
    def test_uses_builder_not_dropbox(self, tmp_path):
        update = _make_update()
        artifact = _artifact(tmp_path)

        with (
            patch(
                "integrations.tg_commands._guard_or_deny",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "integrations.tg_commands.build_registry_export_from_postgres",
                return_value=artifact,
            ) as build_mock,
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        build_mock.assert_called_once()
        update.message.reply_document.assert_awaited_once()
        call_kwargs = update.message.reply_document.await_args.kwargs
        assert call_kwargs["document"].filename == artifact.filename

    def test_success_summary_fields(self, tmp_path):
        update = _make_update()
        artifact = _artifact(tmp_path)

        with (
            patch("integrations.tg_commands._guard_or_deny", new=AsyncMock(return_value=True)),
            patch(
                "integrations.tg_commands.build_registry_export_from_postgres",
                return_value=artifact,
            ),
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        caption = update.message.reply_document.await_args.kwargs["caption"]
        assert "all_results rows: 42" in caption
        assert "runs rows: 7" in caption
        assert "active hold rows: 2" in caption
        assert "snapshot hash: abc123def456" in caption
        assert "Building registry export from PostgreSQL" in (
            update.message.reply_text.await_args_list[0].args[0]
        )

    def test_build_failure_sends_telegram_error(self, tmp_path):
        update = _make_update()

        with (
            patch("integrations.tg_commands._guard_or_deny", new=AsyncMock(return_value=True)),
            patch(
                "integrations.tg_commands.build_registry_export_from_postgres",
                side_effect=RuntimeError("db read failed"),
            ),
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert texts[-1] == "❌ Registry export failed: db read failed"
        update.message.reply_document.assert_not_awaited()

    def test_telegram_send_failure_reported(self, tmp_path):
        update = _make_update()
        artifact = _artifact(tmp_path)
        update.message.reply_document = AsyncMock(side_effect=RuntimeError("send failed"))

        with (
            patch("integrations.tg_commands._guard_or_deny", new=AsyncMock(return_value=True)),
            patch(
                "integrations.tg_commands.build_registry_export_from_postgres",
                return_value=artifact,
            ),
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert texts[-1] == "❌ Registry export failed: send failed"

    def test_acl_deny_blocks_export(self):
        update = _make_update()

        with (
            patch(
                "integrations.tg_commands._guard_or_deny",
                new=AsyncMock(return_value=False),
            ) as guard,
            patch("integrations.tg_commands.build_registry_export_from_postgres") as build_mock,
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        guard.assert_awaited_once_with(update, "registry_export")
        build_mock.assert_not_called()

    def test_handler_registered(self):
        command_names = {
            h.commands
            for h in get_handlers()
            if isinstance(h, CommandHandler)
        }
        assert {"registry_export"} in command_names
