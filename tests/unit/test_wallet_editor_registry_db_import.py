"""Unit tests for Wallet Editor registry import (Stage B)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from integrations.wallet_editor_registry_db.config import ENV_DATABASE_URL, ENV_MIRROR_ENABLED
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError
from integrations.wallet_editor_registry_db.import_workbook import (
    ImportWorkbookError,
    import_registry_frames,
    import_registry_workbook,
    load_registry_dataframes,
    synthetic_run_id,
    validate_registry_workbook,
)
from integrations.wallet_editor_registry_db.mapping import map_all_results_row
from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore, PostgresRegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    RUNS_COLUMNS,
    result_row_dates,
)
from integrations.wallet_editor_registry_xlsx import save_registry_workbook
from tools.import_wallet_editor_registry import main as cli_main

MSK = ZoneInfo("Europe/Moscow")


def _build_registry_workbook(path: Path) -> None:
    processed = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
    operation_date, disable_date = result_row_dates("remove_partner", "OK", processed)
    all_results = pd.DataFrame(
        [
            {
                OPERATION_DATE_COLUMN: operation_date,
                DISABLE_DATE_COLUMN: disable_date,
                "Дата включения": "25.06.2026",
                "Статус включения": "ОЖИДАЕТ",
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111111111111111",
                "partner": "Ostin",
                "action": "remove_partner",
                "status": "OK",
                "comment": "removed",
                "hold": "",
            },
            {
                OPERATION_DATE_COLUMN: operation_date,
                DISABLE_DATE_COLUMN: disable_date,
                "Дата включения": "",
                "Статус включения": "",
                "Включено": "",
                "Комментарий включения": "",
                "card": "4222222222222222",
                "partner": "PartnerB",
                "action": "remove_partner",
                "status": "OK",
                "comment": "",
                "hold": "",
            },
        ],
        columns=ALL_RESULTS_COLUMNS,
    )
    runs = pd.DataFrame(
        [
            {
                "started_at": "22.06.2026 09:00:00",
                "finished_at": "22.06.2026 09:05:00",
                "input_rows": 2,
                "success_rows": 2,
                "failed_rows": 0,
                "skipped_rows": 0,
                "output_file": "result_batch.xlsx",
            }
        ],
        columns=RUNS_COLUMNS,
    )
    save_registry_workbook(
        path,
        all_results=all_results,
        runs=runs,
        hold_exists=False,
        otlezka_exists=False,
        is_new_file=True,
    )


@pytest.fixture
def registry_workbook(tmp_path: Path) -> Path:
    path = tmp_path / "wallet_editor.xlsx"
    _build_registry_workbook(path)
    return path


class TestImportFrames:
    def test_import_all_results_rows(self, registry_workbook: Path):
        all_df, runs_df = load_registry_dataframes(registry_workbook)
        store = InMemoryRegistryStore()
        stats = import_registry_frames(all_df, runs_df, store)

        assert stats.runs_inserted == 1
        assert stats.results_inserted == 2
        assert len(store.results) == 2
        fingerprints = {map_all_results_row(all_df.loc[i]).row_fingerprint for i in all_df.index}
        assert fingerprints == set(store.results.keys())

    def test_import_runs_rows(self, registry_workbook: Path):
        all_df, runs_df = load_registry_dataframes(registry_workbook)
        store = InMemoryRegistryStore()
        import_registry_frames(all_df, runs_df, store)

        assert len(store.runs) == 1
        run = next(iter(store.runs.values()))
        assert run.output_file == "result_batch.xlsx"
        assert run.input_rows == 2

    def test_idempotent_re_run(self, registry_workbook: Path):
        all_df, runs_df = load_registry_dataframes(registry_workbook)
        store = InMemoryRegistryStore()

        first = import_registry_frames(all_df, runs_df, store)
        second = import_registry_frames(all_df, runs_df, store)

        assert first.runs_inserted == 1
        assert first.results_inserted == 2
        assert second.runs_inserted == 0
        assert second.runs_skipped == 1
        assert second.results_inserted == 0
        assert second.results_skipped == 2
        assert len(store.runs) == 1
        assert len(store.results) == 2

    def test_duplicate_row_fingerprint_not_duplicated(self, registry_workbook: Path):
        all_df, runs_df = load_registry_dataframes(registry_workbook)
        store = InMemoryRegistryStore()
        duplicate = pd.concat([all_df, all_df.iloc[[0]]], ignore_index=True)

        stats = import_registry_frames(duplicate, runs_df, store)

        assert stats.results_inserted == 2
        assert len(store.results) == 2

    def test_synthetic_run_id_is_stable(self, registry_workbook: Path):
        _all_df, runs_df = load_registry_dataframes(registry_workbook)
        row = runs_df.iloc[0]
        assert synthetic_run_id(row, index=0) == synthetic_run_id(row, index=0)


class TestImportWorkbookGuards:
    def test_missing_database_url_raises_clear_error(
        self,
        registry_workbook: Path,
        monkeypatch,
    ):
        monkeypatch.delenv(ENV_DATABASE_URL, raising=False)
        with pytest.raises(DatabaseNotConfiguredError, match="DATABASE_URL"):
            import_registry_workbook(registry_workbook)

    def test_import_allowed_when_mirror_flag_off(
        self,
        registry_workbook: Path,
        monkeypatch,
    ):
        monkeypatch.setenv(ENV_MIRROR_ENABLED, "0")
        store = InMemoryRegistryStore()
        stats = import_registry_workbook(registry_workbook, store=store)
        assert stats.results_inserted == 2

    def test_malformed_workbook_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.xlsx"
        bad.write_text("not an xlsx", encoding="utf-8")
        with pytest.raises(ImportWorkbookError, match="invalid registry workbook"):
            validate_registry_workbook(bad)

    def test_missing_workbook_raises(self, tmp_path: Path):
        with pytest.raises(ImportWorkbookError, match="not found"):
            validate_registry_workbook(tmp_path / "missing.xlsx")

    def test_workbook_without_registry_sheets_raises(self, tmp_path: Path):
        path = tmp_path / "empty.xlsx"
        pd.DataFrame({"a": [1]}).to_excel(path, index=False)
        with pytest.raises(ImportWorkbookError, match="missing required sheets"):
            validate_registry_workbook(path)

    def test_skips_invalid_all_results_row(self, registry_workbook: Path):
        all_df, runs_df = load_registry_dataframes(registry_workbook)
        broken = all_df.copy()
        broken.at[broken.index[0], "card"] = ""
        store = InMemoryRegistryStore()
        stats = import_registry_frames(broken, runs_df, store)
        assert stats.results_errors == 1
        assert stats.results_inserted == 1


class TestPostgresStoreSql:
    def test_postgres_upsert_result_returns_inserted_flag(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [("fp1", True), ("fp1", False)]
        store = PostgresRegistryStore(cursor)
        row = map_all_results_row(
            {
                OPERATION_DATE_COLUMN: "22.06.2026",
                DISABLE_DATE_COLUMN: "22.06.2026 09:00:00",
                "Дата включения": "",
                "Статус включения": "",
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111111111111111",
                "partner": "Ostin",
                "action": "remove_partner",
                "status": "OK",
                "comment": "",
                "hold": "",
            }
        )
        assert store.upsert_result(row) is True
        assert store.upsert_result(row) is False
        assert cursor.execute.call_count == 2


class TestImportCli:
    def test_cli_dry_run_local_workbook(self, registry_workbook: Path, capsys):
        code = cli_main([str(registry_workbook), "--dry-run"])
        assert code == 0
        out = capsys.readouterr().out
        assert "results inserted=2" in out

    def test_cli_missing_database_url(self, registry_workbook: Path, monkeypatch, capsys):
        monkeypatch.delenv(ENV_DATABASE_URL, raising=False)
        code = cli_main([str(registry_workbook)])
        assert code == 2
        assert "DATABASE_URL" in capsys.readouterr().err

    def test_cli_malformed_workbook(self, tmp_path: Path, capsys):
        bad = tmp_path / "bad.xlsx"
        bad.write_bytes(b"broken")
        code = cli_main([str(bad)])
        assert code == 2
        assert "invalid registry workbook" in capsys.readouterr().err


class TestRuntimePathUnchanged:
    def test_registry_module_only_schedules_mirror_not_reads(self):
        import integrations.wallet_editor_registry as registry

        source = Path(registry.__file__).read_text(encoding="utf-8")
        assert "schedule_mirror_batch" in source
        assert "connect(for_mirror" not in source
        assert "load_registry_dataframes" not in source

    def test_worker_has_no_db_import_hook(self):
        source = Path("automation/worker.py").read_text(encoding="utf-8")
        assert "wallet_editor_registry_db" not in source
