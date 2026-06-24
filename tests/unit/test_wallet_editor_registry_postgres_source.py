"""Emergency tests — Postgres-first registry source of truth."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_auto_enable import load_registry_frames_for_planning
from integrations.wallet_editor_registry import (
    _AppendOutcome,
    append_run_to_dropbox_registry,
    build_registry_health_report,
    format_registry_health_report,
    patch_enable_results_in_dropbox_registry,
    EnableRegistryUpdate,
)
from integrations.wallet_editor_registry_async import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_SYNCED,
    create_outbox_record,
    get_outbox_record,
    persist_durable_result_copy,
)
from integrations.wallet_editor_registry_db.config import (
    ENV_REGISTRY_SOURCE,
    REGISTRY_SOURCE_EXCEL,
    REGISTRY_SOURCE_POSTGRES,
    registry_source,
)
from integrations.wallet_editor_registry_db.excel_export_state import reset_excel_export_health_for_tests
from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    result_row_dates,
)
from integrations.wallet_editor_registry_xlsx import save_registry_workbook

MSK = ZoneInfo("Europe/Moscow")
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"
RUN_STARTED = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 22, 9, 5, 0, tzinfo=MSK)


@pytest.fixture(autouse=True)
def _reset_export_health():
    reset_excel_export_health_for_tests()
    yield
    reset_excel_export_health_for_tests()


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


def _make_task(*, run_id: str = "pg-source-run") -> WalletEditorTask:
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


class TestRegistrySourceConfig:
    def test_default_source_is_excel(self, monkeypatch):
        monkeypatch.delenv(ENV_REGISTRY_SOURCE, raising=False)
        assert registry_source() == REGISTRY_SOURCE_EXCEL

    def test_postgres_source_flag(self, monkeypatch):
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        assert registry_source() == REGISTRY_SOURCE_POSTGRES


class TestPostgresAppendPreservesOnExcelExportFail:
    def test_postgres_success_excel_export_fail_outbox_synced(
        self, tmp_path, monkeypatch, fast_registry_settings
    ):
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
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

        export_called = threading.Event()

        def _export_fail(*, operation: str, dropbox_path: str) -> None:
            export_called.set()
            from integrations.wallet_editor_registry_db.excel_export_state import (
                record_excel_export_failure,
            )

            record_excel_export_failure(operation=operation, error="upload failed")

        with (
            patch(
                "integrations.wallet_editor_registry_db.postgres_source.load_registry_frames_from_postgres",
                return_value=_empty_registry_frames(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.postgres_source.load_hold_otlezka_from_dropbox",
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
            patch(
                "integrations.wallet_editor_registry_db.postgres_source._persist_full_registry",
            ),
            patch(
                "integrations.wallet_editor_registry.schedule_mirror_batch"
            ) as mirror_mock,
            patch(
                "integrations.wallet_editor_registry_db.excel_export.schedule_excel_export",
                side_effect=_export_fail,
            ),
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
        mirror_mock.assert_not_called()
        assert export_called.is_set()


class TestPostgresFailRetryable:
    def test_postgres_commit_fail_outbox_failed(
        self, tmp_path, monkeypatch, fast_registry_settings
    ):
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

        result_path = tmp_path / "result.xlsx"
        _write_result(result_path)
        task = _make_task(run_id="pg-fail-run")
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
                side_effect=RuntimeError("db down"),
            ),
            patch(
                "integrations.wallet_editor_registry_db.excel_export.schedule_excel_export"
            ) as export_mock,
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
        assert record.status == OUTBOX_STATUS_FAILED
        export_mock.assert_not_called()


class TestAutoEnableReadsPostgres:
    def test_planning_loads_from_postgres_when_source_postgres(self, monkeypatch):
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)

        processed = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
        operation_date, disable_date = result_row_dates("remove_partner", "OK", processed)
        all_results = pd.DataFrame(
            [
                {
                    OPERATION_DATE_COLUMN: operation_date,
                    DISABLE_DATE_COLUMN: disable_date,
                    "Дата включения": "25.06.2026",
                    "Статус включения": "К ВКЛЮЧЕНИЮ",
                    "Включено": "",
                    "Комментарий включения": "",
                    "card": "4111111111111111",
                    "partner": "Ostin",
                    "action": "remove_partner",
                    "status": "OK",
                    "comment": "",
                    "hold": "",
                }
            ],
            columns=ALL_RESULTS_COLUMNS,
        )
        runs = pd.DataFrame(columns=["started_at", "finished_at", "input_rows", "success_rows", "failed_rows", "skipped_rows", "output_file"])

        with (
            patch(
                "integrations.wallet_editor_registry_db.frames.load_registry_frames_from_postgres",
                return_value=(all_results, runs),
            ),
            patch(
                "integrations.wallet_editor_registry_db.hold_loader.load_hold_otlezka_from_dropbox",
                return_value=(
                    pd.DataFrame(columns=["Дата добавления", "card", "partner", "comment"]),
                    pd.DataFrame(columns=["partner", "Полные дни", "comment"]),
                    False,
                    False,
                ),
            ),
            patch(
                "integrations.dropbox_watcher.download_file_with_rev"
            ) as download_mock,
        ):
            recalculated, _hold, _otlezka, raw = load_registry_frames_for_planning()

        download_mock.assert_not_called()
        assert len(recalculated) == 1
        assert len(raw) == 1


class TestExcelExportFailureDoesNotRevertOutbox:
    def test_async_export_failure_leaves_outbox_synced(
        self, tmp_path, monkeypatch, fast_registry_settings
    ):
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

        result_path = tmp_path / "result.xlsx"
        _write_result(result_path)
        task = _make_task(run_id="pg-export-async-fail")
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
                "integrations.wallet_editor_registry_db.postgres_source.load_hold_otlezka_from_dropbox",
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
            patch(
                "integrations.wallet_editor_registry_db.excel_export.export_registry_workbook_to_dropbox",
                side_effect=RuntimeError("upload failed"),
            ),
        ):
            append_run_to_dropbox_registry(
                task,
                str(result_path),
                stats,
                run_started_at=RUN_STARTED,
                run_finished_at=RUN_FINISHED,
            )
            time.sleep(0.2)

        record = get_outbox_record(task.run_id)
        assert record is not None
        assert record.status == OUTBOX_STATUS_SYNCED

    def test_excel_source_still_uses_dropbox_append(self, tmp_path, monkeypatch, fast_registry_settings):
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "excel")
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

        result_path = tmp_path / "result.xlsx"
        _write_result(result_path)
        task = _make_task(run_id="excel-rollback-run")
        stats = Stats()
        stats.ok = 1

        with (
            patch(
                "integrations.wallet_editor_registry._append_attempt",
                return_value=(_AppendOutcome.SUCCESS, "rev1", None),
            ) as append_mock,
            patch(
                "integrations.wallet_editor_registry_db.postgres_source.append_attempt_postgres"
            ) as pg_mock,
            patch("integrations.wallet_editor_registry.schedule_mirror_batch"),
            patch("integrations.wallet_editor_registry_db.excel_export.schedule_excel_export") as export_mock,
        ):
            append_run_to_dropbox_registry(
                task,
                str(result_path),
                stats,
                run_started_at=RUN_STARTED,
                run_finished_at=RUN_FINISHED,
            )

        append_mock.assert_called_once()
        pg_mock.assert_not_called()
        export_mock.assert_not_called()
