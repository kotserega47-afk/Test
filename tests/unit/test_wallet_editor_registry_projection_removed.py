"""Phase 5 — Dropbox registry projection removed from runtime."""

from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry import (
    _AppendOutcome,
    _PatchOutcome,
    EnableRegistryUpdate,
    append_run_to_dropbox_registry,
    build_registry_health_report,
    format_registry_health_report,
    patch_enable_results_in_dropbox_registry,
    replay_pending_outbox_records,
)
from integrations.wallet_editor_registry_async import (
    OUTBOX_STATUS_SYNCED,
    create_outbox_record,
    get_outbox_record,
    persist_durable_result_copy,
)
from integrations.wallet_editor_registry_db.config import ENV_REGISTRY_SOURCE
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    result_row_dates,
)
from integrations.wallet_editor_registry_db.registry_export_builder import RegistryExportArtifact

MSK = ZoneInfo("Europe/Moscow")
RUN_STARTED = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 22, 9, 5, 0, tzinfo=MSK)


def _make_task(*, run_id: str = "proj-removed-run") -> WalletEditorTask:
    return WalletEditorTask(
        file_path="/tmp/wallet_editor/in.xlsx",
        chat_id=-1001,
        telegram_user_id=111,
        operator_profile="DENIS",
        source_file_name="batch.xlsx",
        login="login",
        password="pass",
        auth_state_path="/tmp/auth.json",
        run_id=run_id,
    )


def _write_result(path: Path) -> None:
    processed = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
    operation_date, disable_date = result_row_dates("remove_partner", "OK", processed)
    pd.DataFrame(
        {
            OPERATION_DATE_COLUMN: [operation_date],
            DISABLE_DATE_COLUMN: [disable_date],
            "card": ["4111111111111111"],
            "action": ["remove_partner"],
            "value": ["Ostin"],
            "status": ["OK"],
            "comment": [""],
        }
    ).to_excel(path, index=False)


def _empty_registry_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    return (
        pd.DataFrame(columns=ALL_RESULTS_COLUMNS),
        pd.DataFrame(
            columns=[
                "started_at",
                "finished_at",
                "input_rows",
                "success_rows",
                "failed_rows",
                "skipped_rows",
                "output_file",
            ]
        ),
    )


@pytest.fixture
def fast_registry_settings():
    settings = MagicMock(
        registry_timeout_seconds=1,
        registry_warning_seconds=60,
        registry_retry_interval_seconds=0,
    )
    with patch(
        "integrations.wallet_editor_registry.load_registry_settings",
        return_value=settings,
    ):
        yield settings


class TestProjectionModuleRemoved:
    def test_excel_export_module_not_importable(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("integrations.wallet_editor_registry_db.excel_export")


class TestAppendNoProjection:
    def test_append_postgres_no_schedule_excel_export(
        self, tmp_path, monkeypatch, fast_registry_settings
    ):
        monkeypatch.delenv("DROPBOX_WALLET_EDITOR_PATH", raising=False)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

        result_path = tmp_path / "result.xlsx"
        _write_result(result_path)
        task = _make_task()
        stats = Stats()
        stats.ok = 1
        durable = persist_durable_result_copy(task.run_id, str(result_path))
        create_outbox_record(
            task,
            durable_path=durable,
            stats=stats,
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
            output_file="result.xlsx",
        )

        with (
            patch(
                "integrations.wallet_editor_registry_db.postgres_source.load_registry_frames_from_postgres",
                return_value=_empty_registry_frames(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.postgres_source.load_hold_otlezka_for_runtime",
                return_value=(
                    pd.DataFrame(columns=["Дата добавления", "card", "partner", "comment"]),
                    pd.DataFrame(columns=["partner", "Полные дни", "comment"]),
                    False,
                    False,
                ),
            ),
            patch(
                "integrations.wallet_editor_registry_db.postgres_source.postgres_run_exists",
                return_value=False,
            ),
            patch("integrations.wallet_editor_registry_db.postgres_source._persist_full_registry"),
            patch("integrations.dropbox_watcher.upload_file_if_rev") as upload_mock,
        ):
            append_run_to_dropbox_registry(
                task,
                str(result_path),
                stats,
                run_started_at=RUN_STARTED,
                run_finished_at=RUN_FINISHED,
            )

        record = get_outbox_record(task.run_id)
        assert record is not None
        assert record.status == OUTBOX_STATUS_SYNCED
        upload_mock.assert_not_called()


class TestPatchNoProjection:
    def test_patch_postgres_no_dropbox_upload(self, monkeypatch, fast_registry_settings):
        monkeypatch.delenv("DROPBOX_WALLET_EDITOR_PATH", raising=False)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        updates = [
            EnableRegistryUpdate(
                card="4111111111111111",
                partner="Ostin",
                disable_date="22.06.2026",
                vklyucheno="да",
                comment="auto",
            )
        ]

        with (
            patch(
                "integrations.wallet_editor_registry._patch_attempt",
                return_value=(_PatchOutcome.SUCCESS, 1, None, None),
            ),
            patch("integrations.dropbox_watcher.upload_file_if_rev") as upload_mock,
        ):
            result = patch_enable_results_in_dropbox_registry(
                updates,
                run_id="run-1",
            )

        assert result.success is True
        upload_mock.assert_not_called()


class TestRefreshNoProjection:
    def test_refresh_postgres_no_dropbox_upload(self, monkeypatch):
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", "/dropbox/wallet_editor.xlsx")
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        from integrations.wallet_editor_registry_refresh import (
            _RefreshOutcome,
            refresh_wallet_editor_registry_lifecycle,
        )

        with (
            patch(
                "integrations.wallet_editor_registry_db.manual_sync.run_manual_sync_prerun_gate",
                return_value=MagicMock(ok=True),
            ),
            patch(
                "integrations.wallet_editor_registry_refresh._refresh_attempt",
                return_value=(
                    _RefreshOutcome.SUCCESS,
                    1,
                    MagicMock(),
                    None,
                    None,
                ),
            ),
            patch(
                "integrations.wallet_editor_registry_refresh._send_refresh_report",
                return_value=False,
            ),
            patch("integrations.dropbox_watcher.upload_file_if_rev") as upload_mock,
        ):
            refresh_wallet_editor_registry_lifecycle()

        upload_mock.assert_not_called()


class TestReplayWithoutProjection:
    def test_replay_succeeds_without_dropbox_projection(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
        monkeypatch.delenv("DROPBOX_WALLET_EDITOR_PATH", raising=False)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        with (
            patch(
                "integrations.wallet_editor_registry_db.manual_sync.run_manual_sync_prerun_gate",
                return_value=MagicMock(ok=True),
            ),
            patch("integrations.dropbox_watcher.upload_file_if_rev") as upload_mock,
        ):
            result = replay_pending_outbox_records()

        assert result.skipped >= 0
        upload_mock.assert_not_called()


class TestCliUsesRegistryExportBuilder:
    def test_cli_builds_local_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        artifact = RegistryExportArtifact(
            path=tmp_path / "export.xlsx",
            filename="export.xlsx",
            summary=MagicMock(
                all_results_rows=1,
                runs_rows=1,
                hold_rows=0,
                otlezka_rows=0,
                last_manual_sync_at=None,
                snapshot_hash_short=None,
                manual_sync_degraded=False,
                generated_at="2026-07-01T12:00:00+03:00",
                filename="export.xlsx",
            ),
        )

        with patch(
            "tools.export_wallet_editor_registry.RegistryExportBuilder"
        ) as builder_cls:
            builder_cls.return_value.build.return_value = artifact
            from tools.export_wallet_editor_registry import main

            code = main(["--output", str(tmp_path / "out.xlsx")])

        assert code == 0
        builder_cls.return_value.build.assert_called_once()


class TestRegistryHealthProjectionDisabled:
    def test_health_shows_projection_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        with patch(
            "integrations.wallet_editor_registry_db.frames.load_registry_frames_from_postgres",
            return_value=_empty_registry_frames(),
        ):
            report = build_registry_health_report()

        text = format_registry_health_report(report)
        assert "Registry projection: DISABLED (architecture)" in text
        assert "Registry export: Telegram only" in text
        assert "excel export" not in text.lower()
        assert report.degraded is False


class TestExportIndependentFromDropbox:
    def test_registry_export_builder_does_not_touch_dropbox(self, monkeypatch):
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        with (
            patch(
                "integrations.wallet_editor_registry_db.registry_export_builder.load_registry_frames_from_postgres",
                return_value=_empty_registry_frames(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.registry_export_builder.load_hold_otlezka_frames_for_export",
                return_value=(
                    pd.DataFrame(),
                    pd.DataFrame(),
                    False,
                ),
            ),
            patch(
                "integrations.wallet_editor_registry_db.registry_export_builder.load_manual_sync_meta",
                return_value=MagicMock(
                    last_sync_at=None,
                    last_snapshot_hash=None,
                    last_sync_status=None,
                    last_source_rev=None,
                    active_hold_rows=0,
                    active_otlezka_rows=0,
                    last_sync_run_id=None,
                ),
            ),
            patch("integrations.dropbox_watcher.download_file_with_rev") as download_mock,
            patch("integrations.dropbox_watcher.upload_file_if_rev") as upload_mock,
        ):
            from integrations.wallet_editor_registry_db.registry_export_builder import (
                RegistryExportBuilder,
            )

            RegistryExportBuilder(store=MagicMock()).build()

        download_mock.assert_not_called()
        upload_mock.assert_not_called()
