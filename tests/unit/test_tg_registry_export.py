"""Telegram /registry_export command tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from integrations.wallet_editor_registry_db.excel_export import ExportRegistrySummary
from integrations.tg_commands import cmd_registry_export, get_handlers
from telegram.ext import CommandHandler


DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"


def _sample_summary() -> ExportRegistrySummary:
    return ExportRegistrySummary(
        dropbox_path=DROPBOX_PATH,
        all_results_rows=42,
        runs_rows=7,
        hold_exists=True,
        otlezka_exists=True,
        download_status="ok",
        upload_status="uploaded",
    )


def _make_update() -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = -100
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()
    return update


class TestCmdRegistryExport:
    def test_calls_export_registry_workbook_to_dropbox(self):
        update = _make_update()

        with (
            patch(
                "integrations.tg_commands._guard_or_deny",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "integrations.tg_commands.wallet_editor_dropbox_path",
                return_value=DROPBOX_PATH,
            ),
            patch(
                "integrations.tg_commands.export_registry_workbook_to_dropbox",
                return_value=_sample_summary(),
            ) as export_mock,
            patch(
                "integrations.wallet_editor_registry_lifecycle.recalculate_all_results",
                side_effect=AssertionError("recalculate_all_results must not run"),
            ),
            patch(
                "integrations.wallet_editor_registry_refresh.refresh_wallet_editor_registry_lifecycle",
                side_effect=AssertionError("lifecycle refresh must not run"),
            ),
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        export_mock.assert_called_once_with(DROPBOX_PATH)

    def test_success_message_contains_summary_fields(self):
        update = _make_update()

        with (
            patch("integrations.tg_commands._guard_or_deny", new=AsyncMock(return_value=True)),
            patch("integrations.tg_commands.wallet_editor_dropbox_path", return_value=DROPBOX_PATH),
            patch(
                "integrations.tg_commands.export_registry_workbook_to_dropbox",
                return_value=_sample_summary(),
            ),
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert any("Exporting registry from Postgres" in text for text in texts)
        summary_text = texts[-1]
        assert "all_results rows: 42" in summary_text
        assert "runs rows: 7" in summary_text
        assert "hold_exists: True" in summary_text
        assert "otlezka_exists: True" in summary_text
        assert "download status: ok" in summary_text
        assert "upload status: uploaded" in summary_text
        assert DROPBOX_PATH in summary_text

    def test_failure_message_is_clear(self):
        update = _make_update()

        with (
            patch("integrations.tg_commands._guard_or_deny", new=AsyncMock(return_value=True)),
            patch("integrations.tg_commands.wallet_editor_dropbox_path", return_value=DROPBOX_PATH),
            patch(
                "integrations.tg_commands.export_registry_workbook_to_dropbox",
                side_effect=RuntimeError("upload failed"),
            ),
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        assert texts[-1] == "❌ Registry export failed: upload failed"

    def test_acl_deny_blocks_export(self):
        update = _make_update()

        with (
            patch(
                "integrations.tg_commands._guard_or_deny",
                new=AsyncMock(return_value=False),
            ) as guard,
            patch("integrations.tg_commands.export_registry_workbook_to_dropbox") as export_mock,
        ):
            asyncio.run(cmd_registry_export(update, MagicMock()))

        guard.assert_awaited_once_with(update, "registry_export")
        export_mock.assert_not_called()

    def test_handler_registered(self):
        command_names = {
            h.commands
            for h in get_handlers()
            if isinstance(h, CommandHandler)
        }
        assert {"registry_export"} in command_names
