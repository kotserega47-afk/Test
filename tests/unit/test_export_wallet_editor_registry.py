"""Unit tests for manual Postgres → Dropbox registry export CLI."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from integrations.wallet_editor_registry_db.excel_export import export_registry_workbook_to_dropbox
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    HOLD_COLUMNS,
    OPERATION_DATE_COLUMN,
    OTLEZKA_COLUMNS,
    RUNS_COLUMNS,
    SHEET_ALL_RESULTS,
    SHEET_HOLD,
    SHEET_OTLEZKA,
    SHEET_RUNS,
    result_row_dates,
)
from tools.export_wallet_editor_registry import main as export_cli_main

MSK = ZoneInfo("Europe/Moscow")
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"


def _postgres_all_results() -> pd.DataFrame:
    processed = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
    operation_date, disable_date = result_row_dates("remove_partner", "OK", processed)
    return pd.DataFrame(
        [
            {
                OPERATION_DATE_COLUMN: operation_date,
                DISABLE_DATE_COLUMN: disable_date,
                "Дата включения": "27.06.2026",
                "Статус включения": "ОЖИДАЕТ",
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111111111111111",
                "partner": "Ostin",
                "action": "remove_partner",
                "status": "OK",
                "comment": "from postgres",
                "hold": "",
            }
        ],
        columns=ALL_RESULTS_COLUMNS,
    )


def _postgres_runs() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "started_at": "22.06.2026 09:00:00",
                "finished_at": "22.06.2026 09:05:00",
                "input_rows": 1,
                "success_rows": 1,
                "failed_rows": 0,
                "skipped_rows": 0,
                "output_file": "result.xlsx",
            }
        ],
        columns=RUNS_COLUMNS,
    )


def _seed_dropbox_workbook(tmp_path: Path) -> bytes:
    hold_df = pd.DataFrame(
        [{"Дата добавления": "01.01.2026", "card": "9999", "partner": "HoldPartner", "comment": "keep"}],
        columns=HOLD_COLUMNS,
    )
    otlezka_df = pd.DataFrame(
        [{"partner": "KeepMe", "Полные дни": 7, "comment": "preserve"}],
        columns=OTLEZKA_COLUMNS,
    )
    stale_all_results = pd.DataFrame(
        [
            {
                OPERATION_DATE_COLUMN: "01.01.2099",
                DISABLE_DATE_COLUMN: "01.01.2099 00:00:00",
                "Дата включения": "",
                "Статус включения": "",
                "Включено": "",
                "Комментарий включения": "",
                "card": "0000",
                "partner": "StaleExcel",
                "action": "remove_partner",
                "status": "OK",
                "comment": "old excel row",
                "hold": "",
            }
        ],
        columns=ALL_RESULTS_COLUMNS,
    )
    local = tmp_path / "seed.xlsx"
    with pd.ExcelWriter(local, engine="openpyxl") as writer:
        stale_all_results.to_excel(writer, sheet_name=SHEET_ALL_RESULTS, index=False)
        pd.DataFrame(columns=RUNS_COLUMNS).to_excel(writer, sheet_name=SHEET_RUNS, index=False)
        hold_df.to_excel(writer, sheet_name=SHEET_HOLD, index=False)
        otlezka_df.to_excel(writer, sheet_name=SHEET_OTLEZKA, index=False)
    return local.read_bytes()


@pytest.fixture
def export_env(monkeypatch, tmp_path):
    store: dict[str, bytes] = {DROPBOX_PATH: _seed_dropbox_workbook(tmp_path)}
    uploads: list[bytes] = []

    def fake_download_with_rev(dropbox_path: str, local_path: str) -> tuple[str, str | None]:
        content = store.get(dropbox_path)
        if content is None:
            return "not_found", None
        Path(local_path).write_bytes(content)
        return "ok", "rev-export-1"

    def fake_upload_if_rev(local_path: str, dropbox_path: str, expected_rev: str | None) -> str:
        uploads.append(Path(local_path).read_bytes())
        store[dropbox_path] = uploads[-1]
        return "uploaded"

    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

    with (
        patch(
            "integrations.wallet_editor_registry_db.excel_export.download_file_with_rev",
            side_effect=fake_download_with_rev,
        ),
        patch(
            "integrations.wallet_editor_registry_db.excel_export.upload_file_if_rev",
            side_effect=fake_upload_if_rev,
        ),
    ):
        yield {"uploads": uploads}


class TestExportWalletEditorRegistry:
    def test_exports_all_results_and_runs_from_postgres(self, export_env):
        postgres_all = _postgres_all_results()
        postgres_runs = _postgres_runs()

        with (
            patch(
                "integrations.wallet_editor_registry_db.excel_export.load_registry_frames_from_postgres",
                return_value=(postgres_all.copy(), postgres_runs.copy()),
            ),
            patch(
                "integrations.wallet_editor_registry_lifecycle.recalculate_all_results",
                side_effect=AssertionError("recalculate_all_results must not run during export"),
            ),
            patch("integrations.wallet_editor_registry_db.connection.connect") as connect_mock,
        ):
            summary = export_registry_workbook_to_dropbox(DROPBOX_PATH)

        connect_mock.assert_not_called()
        assert summary.all_results_rows == 1
        assert summary.runs_rows == 1
        assert summary.upload_status == "uploaded"
        assert len(export_env["uploads"]) == 1

        with pd.ExcelFile(io.BytesIO(export_env["uploads"][0]), engine="openpyxl") as book:
            exported_all = pd.read_excel(book, SHEET_ALL_RESULTS)
            exported_runs = pd.read_excel(book, SHEET_RUNS)

        assert str(exported_all.iloc[0]["card"]) == "4111111111111111"
        assert exported_all.iloc[0]["comment"] == "from postgres"
        assert str(exported_runs.iloc[0]["output_file"]) == "result.xlsx"

    def test_preserves_hold_and_otlezka_sheets(self, export_env):
        postgres_all = _postgres_all_results()
        postgres_runs = _postgres_runs()

        with patch(
            "integrations.wallet_editor_registry_db.excel_export.load_registry_frames_from_postgres",
            return_value=(postgres_all.copy(), postgres_runs.copy()),
        ):
            summary = export_registry_workbook_to_dropbox(DROPBOX_PATH)

        assert summary.hold_exists is True
        assert summary.otlezka_exists is True

        with pd.ExcelFile(io.BytesIO(export_env["uploads"][0]), engine="openpyxl") as book:
            hold_df = pd.read_excel(book, SHEET_HOLD)
            otlezka_df = pd.read_excel(book, SHEET_OTLEZKA)

        assert hold_df.iloc[0]["partner"] == "HoldPartner"
        assert hold_df.iloc[0]["comment"] == "keep"
        assert otlezka_df.iloc[0]["partner"] == "KeepMe"
        assert otlezka_df.iloc[0]["comment"] == "preserve"

    def test_does_not_write_postgres(self, export_env):
        with (
            patch(
                "integrations.wallet_editor_registry_db.excel_export.load_registry_frames_from_postgres",
                return_value=(_postgres_all_results(), _postgres_runs()),
            ),
            patch("integrations.wallet_editor_registry_db.connection.connect") as connect_mock,
            patch(
                "integrations.wallet_editor_registry_db.store.PostgresRegistryStore.upsert_result",
            ) as upsert_mock,
        ):
            export_registry_workbook_to_dropbox(DROPBOX_PATH)

        connect_mock.assert_not_called()
        upsert_mock.assert_not_called()

    def test_cli_prints_summary(self, export_env, capsys):
        with patch(
            "integrations.wallet_editor_registry_db.excel_export.load_registry_frames_from_postgres",
            return_value=(_postgres_all_results(), _postgres_runs()),
        ):
            code = export_cli_main([])

        output = capsys.readouterr().out
        assert code == 0
        assert "all_results rows: 1" in output
        assert "runs rows: 1" in output
        assert "hold_exists: True" in output
        assert "otlezka_exists: True" in output
        assert "upload status: uploaded" in output
