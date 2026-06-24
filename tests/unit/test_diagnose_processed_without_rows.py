"""Unit tests for processed_run_ids PostgreSQL diagnostic CLI."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry_async import (
    OUTBOX_STATUS_SYNCED,
    create_outbox_record,
    persist_durable_result_copy,
    update_outbox_status,
)
from integrations.wallet_editor_registry_db.diagnose_processed import (
    REASON_B1_REAL_GAP,
    REASON_B2_FINGERPRINT_MISMATCH,
    REASON_B3_MISSING_DURABLE_RESULT,
    REASON_OK,
    diagnose_processed_report_json,
    diagnose_processed_runs,
    format_diagnose_processed_report,
)
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    mark_run_processed,
    result_row_dates,
    rows_from_result_excel,
)
from integrations.wallet_editor_registry_xlsx import card_as_text

MSK = ZoneInfo("Europe/Moscow")
RUN_STARTED = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 22, 9, 5, 0, tzinfo=MSK)


def _make_task(*, run_id: str) -> WalletEditorTask:
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


def _write_result(path: Path, *, card: str = "4111111111111111") -> None:
    processed = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
    operation_date, disable_date = result_row_dates("remove_partner", "OK", processed)
    pd.DataFrame(
        {
            OPERATION_DATE_COLUMN: [operation_date],
            DISABLE_DATE_COLUMN: [disable_date],
            "card": [card],
            "action": ["remove_partner"],
            "value": ["Ostin"],
            "status": ["OK"],
            "comment": [""],
        }
    ).to_excel(path, index=False)


def _setup_synced_processed_run(
    tmp_path: Path,
    *,
    run_id: str,
    card: str = "4111111111111111",
) -> Path:
    result_path = tmp_path / f"{run_id}.xlsx"
    _write_result(result_path, card=card)
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
    return Path(durable)


def _result_fingerprints(result_path: Path) -> list[str]:
    from integrations.wallet_editor_registry_lifecycle import fingerprints_from_result_excel

    return fingerprints_from_result_excel(str(result_path))


def _matching_all_results(result_path: Path) -> pd.DataFrame:
    result_df = pd.read_excel(
        result_path,
        engine="openpyxl",
        converters={"card": card_as_text},
    )
    return rows_from_result_excel(result_df)


@pytest.fixture
def diagnose_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WALLET_EDITOR_REGISTRY_SOURCE", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")


class TestDiagnoseProcessedRuns:
    def test_ok_when_postgres_has_all_fingerprints(self, diagnose_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="diag-ok")
        fps = _result_fingerprints(durable)
        all_results = _matching_all_results(durable)

        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={"diag-ok": 1},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(fps),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(fps),
            ),
        ):
            report = diagnose_processed_runs(limit=20)

        entry = report.entries[0]
        assert entry.probable_reason == REASON_OK
        assert entry.missing_fingerprints_count == 0
        assert entry.health_would_count is False

    def test_b1_real_gap_no_postgres_rows(self, diagnose_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="diag-b1")
        fps = _result_fingerprints(durable)

        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(),
            ),
        ):
            report = diagnose_processed_runs(limit=20)

        entry = report.entries[0]
        assert entry.probable_reason == REASON_B1_REAL_GAP
        assert entry.postgres_rows_by_run_id == 0
        assert entry.missing_fingerprints_count == len(fps)
        assert entry.health_would_count is True

    def test_b2_fingerprint_mismatch_with_run_rows(self, diagnose_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="diag-b2")
        fps = _result_fingerprints(durable)
        wrong_df = pd.DataFrame(
            [
                {
                    OPERATION_DATE_COLUMN: "01.01.2099",
                    DISABLE_DATE_COLUMN: "01.01.2099 00:00:00",
                    "Дата включения": "",
                    "Статус включения": "",
                    "Включено": "",
                    "Комментарий включения": "",
                    "card": "9999",
                    "partner": "Other",
                    "action": "remove_partner",
                    "status": "OK",
                    "comment": "",
                    "hold": "",
                }
            ],
            columns=ALL_RESULTS_COLUMNS,
        )

        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={"diag-b2": 1},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(),
            ),
        ):
            report = diagnose_processed_runs(limit=20)

        entry = report.entries[0]
        assert entry.probable_reason == REASON_B2_FINGERPRINT_MISMATCH
        assert entry.postgres_rows_by_run_id == 1
        assert entry.missing_fingerprints_count == len(fps)
        assert entry.sample_missing_fingerprints
        assert entry.sample_missing_fingerprints[0] == fps[0]

    def test_b3_missing_durable_result(self, diagnose_env, tmp_path):
        _setup_synced_processed_run(tmp_path, run_id="diag-b3")
        durable_path = tmp_path / "state" / "wallet_editor" / "results" / "diag-b3.xlsx"
        if durable_path.exists():
            durable_path.unlink()

        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(),
            ),
        ):
            report = diagnose_processed_runs(limit=20)

        entry = report.entries[0]
        assert entry.probable_reason == REASON_B3_MISSING_DURABLE_RESULT
        assert entry.result_file_exists is False
        assert entry.health_would_count is False

    def test_json_output_shape_stable(self, diagnose_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="diag-json")
        fps = _result_fingerprints(durable)
        all_results = _matching_all_results(durable)

        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={"diag-json": 1},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(fps),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(fps),
            ),
        ):
            report = diagnose_processed_runs(limit=5)

        payload = json.loads(diagnose_processed_report_json(report))
        assert payload["registry_source"] == "postgres"
        assert payload["processed_run_ids_total"] == 1
        assert payload["limit"] == 5
        assert payload["only_health_failures"] is False
        assert payload["health_failures_count"] == 0
        assert len(payload["entries"]) == 1
        entry = payload["entries"][0]
        assert entry["run_id"] == "diag-json"
        assert entry["probable_reason"] == REASON_OK
        assert "sample_missing_fingerprints" in entry
        assert "health_would_count" in entry

    def test_human_readable_report_contains_summary(self, diagnose_env, tmp_path):
        _setup_synced_processed_run(tmp_path, run_id="diag-text")
        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(),
            ),
        ):
            text = format_diagnose_processed_report(diagnose_processed_runs(limit=10))

        assert "WalletEditor processed_run_ids diagnostic" in text
        assert "probable_reason: B1_REAL_GAP" in text
        assert "summary:" in text


class TestDiagnoseProcessedCli:
    def test_cli_json_mode(self, diagnose_env, tmp_path, capsys):
        durable = _setup_synced_processed_run(tmp_path, run_id="diag-cli")
        fps = _result_fingerprints(durable)

        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={"diag-cli": 1},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(fps),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(fps),
            ),
        ):
            from tools.diagnose_processed_without_rows import main

            code = main(["--limit", "5", "--json"])

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["entries"][0]["run_id"] == "diag-cli"


class TestOnlyHealthFailuresFilter:
    def test_no_failures_empty_entries(self, diagnose_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="diag-no-fail")
        fps = _result_fingerprints(durable)

        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={"diag-no-fail": 1},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(fps),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(fps),
            ),
        ):
            report = diagnose_processed_runs(only_health_failures=True)

        assert report.health_failures_count == 0
        assert report.entries == ()
        text = format_diagnose_processed_report(report)
        assert "no health failures (processed_without_rows would be 0)" in text

    def test_b1_included_when_only_health_failures(self, diagnose_env, tmp_path):
        _setup_synced_processed_run(tmp_path, run_id="diag-b1-only")
        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(),
            ),
        ):
            report = diagnose_processed_runs(only_health_failures=True)

        assert report.health_failures_count == 1
        assert len(report.entries) == 1
        assert report.entries[0].run_id == "diag-b1-only"
        assert report.entries[0].probable_reason == REASON_B1_REAL_GAP
        assert report.entries[0].health_would_count is True

    def test_ok_excluded_when_only_health_failures(self, diagnose_env, tmp_path):
        ok_durable = _setup_synced_processed_run(tmp_path, run_id="diag-ok-only")
        _setup_synced_processed_run(tmp_path, run_id="diag-b1-mix", card="4222222222222222")
        fps_ok = _result_fingerprints(ok_durable)

        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={"diag-ok-only": 1, "diag-b1-mix": 0},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                side_effect=lambda fps: set(fps) & set(fps_ok),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(fps_ok),
            ),
        ):
            report = diagnose_processed_runs(only_health_failures=True)

        assert report.health_failures_count == 1
        assert len(report.entries) == 1
        assert report.entries[0].run_id == "diag-b1-mix"

    def test_only_health_failures_json_shape(self, diagnose_env, tmp_path, capsys):
        _setup_synced_processed_run(tmp_path, run_id="diag-json-fail")
        with (
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_run_row_counts",
                return_value={},
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._postgres_fingerprints_present",
                return_value=set(),
            ),
            patch(
                "integrations.wallet_editor_registry_db.diagnose_processed._load_postgres_fingerprint_set",
                return_value=frozenset(),
            ),
        ):
            from tools.diagnose_processed_without_rows import main

            code = main(["--only-health-failures", "--json"])

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["only_health_failures"] is True
        assert payload["health_failures_count"] == 1
        assert payload["registry_source"] == "postgres"
        assert len(payload["entries"]) == 1
        entry = payload["entries"][0]
        assert entry["health_would_count"] is True
        assert "sample_missing_fingerprints" in entry
