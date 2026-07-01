"""Unit tests for local Postgres registry export CLI."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from integrations.wallet_editor_registry_db.registry_export_builder import (
    RegistryExportArtifact,
    RegistryExportSummary,
)
from tools.export_wallet_editor_registry import main as export_cli_main

MSK = ZoneInfo("Europe/Moscow")


def _artifact(path: Path) -> RegistryExportArtifact:
    summary = RegistryExportSummary(
        all_results_rows=10,
        runs_rows=2,
        hold_rows=1,
        otlezka_rows=1,
        last_manual_sync_at="2026-07-01T10:00:00+03:00",
        snapshot_hash_short="abc123",
        manual_sync_degraded=False,
        generated_at=datetime(2026, 7, 1, 12, 0, 0, tzinfo=MSK).isoformat(),
        filename=path.name,
    )
    path.write_bytes(b"fake-xlsx")
    return RegistryExportArtifact(path=path, filename=path.name, summary=summary)


class TestExportWalletEditorRegistryCli:
    def test_cli_uses_registry_export_builder(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WALLET_EDITOR_REGISTRY_SOURCE", "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        out_path = tmp_path / "registry_export.xlsx"
        artifact = _artifact(out_path)

        with patch(
            "tools.export_wallet_editor_registry.RegistryExportBuilder"
        ) as builder_cls:
            builder_cls.return_value.build.return_value = artifact
            code = export_cli_main(["--output", str(out_path)])

        assert code == 0
        builder_cls.return_value.build.assert_called_once_with(output_path=out_path)

    def test_cli_missing_database_url(self, monkeypatch, capsys):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("WALLET_EDITOR_REGISTRY_SOURCE", "postgres")

        from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError

        with patch(
            "tools.export_wallet_editor_registry.RegistryExportBuilder"
        ) as builder_cls:
            builder_cls.return_value.build.side_effect = DatabaseNotConfiguredError(
                "DATABASE_URL is not set"
            )
            code = export_cli_main([])

        assert code == 2
        assert "DATABASE_URL" in capsys.readouterr().err

    def test_cli_does_not_call_dropbox(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WALLET_EDITOR_REGISTRY_SOURCE", "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        out_path = tmp_path / "local.xlsx"

        with (
            patch(
                "tools.export_wallet_editor_registry.RegistryExportBuilder"
            ) as builder_cls,
            patch("integrations.dropbox_watcher.upload_file_if_rev") as upload_mock,
        ):
            builder_cls.return_value.build.return_value = _artifact(out_path)
            export_cli_main(["--output", str(out_path)])

        upload_mock.assert_not_called()
