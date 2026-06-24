"""Unit tests for Wallet Editor registry reconcile (Stage D / observation)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from integrations.wallet_editor_registry import build_registry_health_report, format_registry_health_report
from integrations.wallet_editor_registry_db.import_workbook import import_registry_frames, load_registry_dataframes
from integrations.wallet_editor_registry_db.mapping import map_all_results_row
from integrations.wallet_editor_registry_db.mirror_state import (
    get_mirror_health,
    record_mirror_failure,
    record_mirror_success,
    reset_mirror_health_for_tests,
)
from integrations.wallet_editor_registry_db.models import RegistryResultRow
from integrations.wallet_editor_registry_db.reconcile import (
    ReconcileStatus,
    classify_drift,
    format_reconcile_report,
    reconcile_exit_code,
    reconcile_frames_against_store,
    reconcile_result_maps,
)
from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    result_row_dates,
)
from integrations.wallet_editor_registry_xlsx import save_registry_workbook
from tools.reconcile_wallet_editor_registry import main as reconcile_cli_main

MSK = ZoneInfo("Europe/Moscow")


def _result_row(
    *,
    card: str = "4111111111111111",
    partner: str = "Ostin",
    vklyucheno: str = "",
    enable_status: str = "ОЖИДАЕТ",
    comment: str = "removed",
) -> dict[str, str]:
    processed = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
    operation_date, disable_date = result_row_dates("remove_partner", "OK", processed)
    return {
        OPERATION_DATE_COLUMN: operation_date,
        DISABLE_DATE_COLUMN: disable_date,
        "Дата включения": "25.06.2026",
        "Статус включения": enable_status,
        "Включено": vklyucheno,
        "Комментарий включения": "",
        "card": card,
        "partner": partner,
        "action": "remove_partner",
        "status": "OK",
        "comment": comment,
        "hold": "",
    }


def _build_workbook(path: Path, rows: list[dict[str, str]]) -> None:
    all_results = pd.DataFrame(rows, columns=ALL_RESULTS_COLUMNS)
    runs = pd.DataFrame(
        [
            {
                "started_at": "22.06.2026 09:00:00",
                "finished_at": "22.06.2026 09:05:00",
                "input_rows": len(rows),
                "success_rows": len(rows),
                "failed_rows": 0,
                "skipped_rows": 0,
                "output_file": "batch.xlsx",
            }
        ]
    )
    save_registry_workbook(
        path,
        all_results=all_results,
        runs=runs,
        hold_exists=False,
        otlezka_exists=False,
        is_new_file=True,
    )


@pytest.fixture(autouse=True)
def _reset_mirror_health():
    reset_mirror_health_for_tests()
    yield
    reset_mirror_health_for_tests()


class TestReconcileMaps:
    def test_clean_reconcile_ok(self):
        row = _result_row()
        all_results = pd.DataFrame([row], columns=ALL_RESULTS_COLUMNS)
        mapped = map_all_results_row(all_results.iloc[0], source_row_index=0)
        store = InMemoryRegistryStore()
        store.upsert_result(mapped)

        report = reconcile_frames_against_store(all_results, store.results)
        assert report.status == ReconcileStatus.OK
        assert report.total_issue_count == 0
        assert report.excel_row_count == 1
        assert report.db_row_count == 1

    def test_missing_in_db_warning(self):
        row = _result_row()
        all_results = pd.DataFrame([row], columns=ALL_RESULTS_COLUMNS)
        report = reconcile_frames_against_store(all_results, {})
        assert report.status == ReconcileStatus.WARNING
        assert len(report.missing_in_db) == 1

    def test_mass_missing_in_db_error(self):
        rows = [_result_row(card=f"411111111111111{i}") for i in range(12)]
        all_results = pd.DataFrame(rows, columns=ALL_RESULTS_COLUMNS)
        report = reconcile_frames_against_store(all_results, {})
        assert report.status == ReconcileStatus.ERROR
        assert len(report.missing_in_db) == 12

    def test_content_mismatch_detected(self):
        row = _result_row()
        all_results = pd.DataFrame([row], columns=ALL_RESULTS_COLUMNS)
        mapped = map_all_results_row(all_results.iloc[0], source_row_index=0)
        db_row = RegistryResultRow(
            row_fingerprint=mapped.row_fingerprint,
            card=mapped.card,
            partner=mapped.partner,
            action=mapped.action,
            status=mapped.status,
            vklyucheno="OK",
            operation_date=mapped.operation_date,
            disable_at=mapped.disable_at,
            reenable_date=mapped.reenable_date,
            enable_status=mapped.enable_status,
            enable_comment=mapped.enable_comment,
            comment=mapped.comment,
            hold_mark=mapped.hold_mark,
        )
        report = reconcile_result_maps(
            {mapped.row_fingerprint: mapped},
            {db_row.row_fingerprint: db_row},
        )
        assert report.status == ReconcileStatus.WARNING
        assert len(report.content_mismatches) == 1
        assert report.content_mismatches[0].fields[0].field == "vklyucheno"

    def test_missing_in_excel_only_warning_when_isolated(self):
        row = _result_row()
        mapped = map_all_results_row(pd.Series(row), source_row_index=0)
        all_results = pd.DataFrame(columns=ALL_RESULTS_COLUMNS)
        report = reconcile_frames_against_store(all_results, {mapped.row_fingerprint: mapped})
        assert report.status == ReconcileStatus.WARNING
        assert len(report.missing_in_excel) == 1


class TestDriftClassification:
    def test_classify_ok(self):
        report = reconcile_result_maps({}, {})
        assert classify_drift(report) == ReconcileStatus.OK

    def test_reconcile_exit_codes(self):
        ok = reconcile_result_maps({}, {})
        assert reconcile_exit_code(ok) == 0

        warning = reconcile_result_maps(
            {"a": map_all_results_row(pd.Series(_result_row()), source_row_index=0)},
            {},
        )
        assert warning.status == ReconcileStatus.WARNING
        assert reconcile_exit_code(warning) == 1


class TestReconcileReportFormat:
    def test_format_includes_counts(self):
        report = reconcile_result_maps({}, {})
        text = format_reconcile_report(report)
        assert "status: OK" in text
        assert "excel rows: 0" in text
        assert "postgres rows: 0" in text


class TestImportThenReconcile:
    def test_imported_store_matches_workbook(self, tmp_path: Path):
        path = tmp_path / "wallet_editor.xlsx"
        _build_workbook(path, [_result_row(), _result_row(card="4222222222222222", partner="B")])
        all_results, runs = load_registry_dataframes(path)
        store = InMemoryRegistryStore()
        import_registry_frames(all_results, runs, store)

        report = reconcile_frames_against_store(all_results, store.results)
        assert report.status == ReconcileStatus.OK
        assert report.excel_row_count == 2
        assert report.db_row_count == 2


class TestMirrorHealthExtended:
    def test_failure_count_and_reset_recent_on_success(self):
        record_mirror_failure(operation="append", error="db down")
        record_mirror_failure(operation="patch", error="timeout")
        health = get_mirror_health()
        assert health.failure_count == 2
        assert health.recent_failures == 2
        assert health.last_failure_at is not None
        assert health.last_error == "timeout"

        record_mirror_success(operation="append")
        health = get_mirror_health()
        assert health.recent_failures == 0
        assert health.failure_count == 2
        assert health.last_success_at is not None

    def test_health_report_includes_mirror_fields(self, monkeypatch):
        monkeypatch.setenv("WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED", "1")
        record_mirror_failure(operation="refresh", error="conn refused")
        report = build_registry_health_report()
        text = format_registry_health_report(report)
        assert "mirror failure count: 1" in text
        assert "mirror last failure:" in text
        assert "mirror last operation: refresh" in text


class TestReconcileCli:
    def test_cli_missing_database_url(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        path = tmp_path / "wallet_editor.xlsx"
        _build_workbook(path, [_result_row()])
        assert reconcile_cli_main([str(path)]) == 2
