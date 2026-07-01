"""Emergency tests — Postgres-first registry source of truth."""

from __future__ import annotations

import time
from contextlib import contextmanager
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
    registry_stale_outbox_warning,
    EnableRegistryUpdate,
)
from integrations.wallet_editor_registry_async import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_SYNCED,
    create_outbox_record,
    get_outbox_record,
    persist_durable_result_copy,
    update_outbox_status,
)
from integrations.wallet_editor_registry_db.config import (
    ENV_REGISTRY_SOURCE,
    LegacyRegistrySourceError,
    REGISTRY_SOURCE_POSTGRES,
    registry_source,
)
from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    fingerprints_from_result_excel,
    mark_run_processed,
    missing_result_fingerprints,
    result_row_dates,
    rows_from_result_excel,
)
from integrations.wallet_editor_registry_xlsx import card_as_text, save_registry_workbook

MSK = ZoneInfo("Europe/Moscow")
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"
RUN_STARTED = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 22, 9, 5, 0, tzinfo=MSK)


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
    def test_default_source_is_postgres(self, monkeypatch):
        monkeypatch.delenv(ENV_REGISTRY_SOURCE, raising=False)
        assert registry_source() == REGISTRY_SOURCE_POSTGRES

    def test_postgres_source_flag(self, monkeypatch):
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        assert registry_source() == REGISTRY_SOURCE_POSTGRES


class TestPostgresAppendWithoutDropbox:
    def test_postgres_append_synced_without_dropbox_path(
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
            patch(
                "integrations.wallet_editor_registry_db.postgres_source._persist_full_registry",
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

        with patch(
            "integrations.wallet_editor_registry_db.postgres_source.load_registry_frames_from_postgres",
            side_effect=RuntimeError("db down"),
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


class TestAutoEnableReadsPostgres:
    def test_planning_loads_from_postgres_when_source_postgres(self, monkeypatch):
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "postgres")
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
                "integrations.wallet_editor_registry_db.manual_readers.load_hold_otlezka_for_runtime",
                return_value=(
                    pd.DataFrame(columns=["Дата добавления", "card", "partner", "comment"]),
                    pd.DataFrame(columns=["partner", "Полные дни", "comment"]),
                    True,
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


class TestLegacyExcelSourceRejected:
    def test_legacy_excel_source_blocks_append(self, tmp_path, monkeypatch, fast_registry_settings):
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "excel")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

        result_path = tmp_path / "result.xlsx"
        _write_result(result_path)
        task = _make_task(run_id="excel-blocked-run")
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
        assert "no longer supported" in (record.last_error or "")


def _setup_synced_processed_run(
    tmp_path: Path,
    *,
    run_id: str = "pg-health-run",
) -> Path:
    result_path = tmp_path / "result.xlsx"
    _write_result(result_path)
    task = _make_task(run_id=run_id)
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
    update_outbox_status(task.run_id, status=OUTBOX_STATUS_SYNCED)
    mark_run_processed(task.run_id)
    return result_path


def _all_results_matching_result(result_path: Path) -> pd.DataFrame:
    result_df = pd.read_excel(
        result_path,
        engine="openpyxl",
        converters={"card": card_as_text},
    )
    return rows_from_result_excel(result_df)


def _fingerprints_from_result(result_path: Path) -> frozenset[str]:
    return frozenset(fingerprints_from_result_excel(str(result_path)))


@contextmanager
def _patch_postgres_health_reads(
    *,
    all_results: pd.DataFrame,
    runs: pd.DataFrame,
    present_fingerprints: frozenset[str],
):
    with (
        patch(
            "integrations.wallet_editor_registry_db.frames.load_registry_frames_from_postgres",
            return_value=(all_results, runs),
        ),
        patch(
            "integrations.wallet_editor_registry_db.frames.load_registry_row_fingerprints_from_postgres",
            return_value=present_fingerprints,
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
    ):
        yield


class TestProcessedWithoutRowsPostgresSource:
    def test_postgres_source_stale_excel_no_false_positive(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        result_path = _setup_synced_processed_run(tmp_path)
        matching_all_results = _all_results_matching_result(result_path)
        present_fps = _fingerprints_from_result(result_path)
        empty_runs = pd.DataFrame(
            columns=[
                "started_at",
                "finished_at",
                "input_rows",
                "success_rows",
                "failed_rows",
                "skipped_rows",
                "output_file",
                "run_id",
            ]
        )

        with (
            _patch_postgres_health_reads(
                all_results=matching_all_results,
                runs=empty_runs,
                present_fingerprints=present_fps,
            ),
            patch(
                "integrations.wallet_editor_registry.download_file_with_rev",
                side_effect=AssertionError(
                    "processed_without_rows must not read Dropbox Excel when source=postgres"
                ),
            ),
        ):
            report = build_registry_health_report()

        assert report.processed_without_rows_count == 0

    def test_postgres_source_missing_rows_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        _setup_synced_processed_run(tmp_path, run_id="pg-missing-rows")
        empty_all_results, empty_runs = _empty_registry_frames()

        with _patch_postgres_health_reads(
            all_results=empty_all_results,
            runs=empty_runs,
            present_fingerprints=frozenset(),
        ):
            report = build_registry_health_report()

        assert report.processed_without_rows_count >= 1

    def test_postgres_fingerprints_in_db_without_run_id_rows_health_ok(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        result_path = _setup_synced_processed_run(tmp_path, run_id="pg-fp-no-runid")
        present_fps = _fingerprints_from_result(result_path)
        empty_all_results, empty_runs = _empty_registry_frames()

        with _patch_postgres_health_reads(
            all_results=empty_all_results,
            runs=empty_runs,
            present_fingerprints=present_fps,
        ):
            report = build_registry_health_report()

        assert report.processed_without_rows_count == 0

    def test_postgres_health_not_degraded_when_excel_stale_only(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        result_path = _setup_synced_processed_run(tmp_path, run_id="pg-not-degraded")
        matching_all_results = _all_results_matching_result(result_path)
        present_fps = _fingerprints_from_result(result_path)
        empty_runs = pd.DataFrame(
            columns=[
                "started_at",
                "finished_at",
                "input_rows",
                "success_rows",
                "failed_rows",
                "skipped_rows",
                "output_file",
                "run_id",
            ]
        )

        with _patch_postgres_health_reads(
            all_results=matching_all_results,
            runs=empty_runs,
            present_fingerprints=present_fps,
        ):
            report = build_registry_health_report()
            warning = registry_stale_outbox_warning()

        assert report.processed_without_rows_count == 0
        assert report.outbox_pending_count == 0
        assert report.outbox_failed_count == 0
        assert report.degraded is False
        assert warning is None
