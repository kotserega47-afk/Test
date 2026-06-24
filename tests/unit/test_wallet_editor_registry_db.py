"""Unit tests for Wallet Editor registry DB mirror — Stage A (schema, config, mapping)."""

from __future__ import annotations

import re
from unittest.mock import patch

import pandas as pd
import pytest

from integrations.wallet_editor_registry_db.config import (
    ENV_DATABASE_URL,
    ENV_MIRROR_ENABLED,
    get_database_url,
    mirror_enabled,
)
from integrations.wallet_editor_registry_db.connection import (
    DatabaseNotConfiguredError,
    MirrorDisabledError,
    connect,
)
from integrations.wallet_editor_registry_db.mapping import (
    map_all_results_row,
    map_runs_row,
    runs_row_dict_for_columns,
    validate_runs_columns,
)
from integrations.wallet_editor_registry_db.models import RegistryResultRow, RegistryRunRow
from integrations.wallet_editor_registry_db.schema import (
    EXPECTED_TABLES,
    SCHEMA_VERSION,
    load_schema_sql,
    schema_sql_path,
)
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    RUNS_COLUMNS,
    result_row_fingerprint,
)


class TestRegistrySourceConfig:
    def test_registry_source_defaults_excel(self, monkeypatch):
        from integrations.wallet_editor_registry_db.config import (
            ENV_REGISTRY_SOURCE,
            REGISTRY_SOURCE_EXCEL,
            registry_source,
        )

        monkeypatch.delenv(ENV_REGISTRY_SOURCE, raising=False)
        assert registry_source() == REGISTRY_SOURCE_EXCEL

    def test_registry_source_postgres(self, monkeypatch):
        from integrations.wallet_editor_registry_db.config import (
            ENV_REGISTRY_SOURCE,
            REGISTRY_SOURCE_POSTGRES,
            registry_source,
        )

        monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
        assert registry_source() == REGISTRY_SOURCE_POSTGRES


class TestFeatureFlag:
    def test_mirror_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MIRROR_ENABLED, raising=False)
        assert mirror_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_mirror_enabled_truthy(self, monkeypatch, value):
        monkeypatch.setenv(ENV_MIRROR_ENABLED, value)
        assert mirror_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "", "off"])
    def test_mirror_disabled_falsy(self, monkeypatch, value):
        monkeypatch.setenv(ENV_MIRROR_ENABLED, value)
        assert mirror_enabled() is False

    def test_database_url_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv(ENV_DATABASE_URL, raising=False)
        assert get_database_url() is None

    def test_database_url_returns_value(self, monkeypatch):
        monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://user:pass@host/db")
        assert get_database_url() == "postgresql://user:pass@host/db"


class TestConnectionGuard:
    def test_connect_for_mirror_raises_when_flag_off(self, monkeypatch):
        monkeypatch.setenv(ENV_MIRROR_ENABLED, "0")
        monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://localhost/test")
        with pytest.raises(MirrorDisabledError):
            with connect(for_mirror=True):
                pass

    def test_connect_raises_without_database_url(self, monkeypatch):
        monkeypatch.delenv(ENV_DATABASE_URL, raising=False)
        with pytest.raises(DatabaseNotConfiguredError):
            with connect(for_mirror=False):
                pass

    def test_connect_without_mirror_flag_does_not_check_mirror_enabled(self, monkeypatch):
        monkeypatch.setenv(ENV_MIRROR_ENABLED, "0")
        monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://invalid:5432/nodb")
        with patch("psycopg.connect", side_effect=OSError("network down")):
            with pytest.raises(OSError, match="network down"):
                with connect(for_mirror=False):
                    pass


class TestSchemaSql:
    def test_schema_file_exists(self):
        assert schema_sql_path().is_file()

    def test_schema_version_constant(self):
        assert SCHEMA_VERSION == 1

    def test_expected_tables_present_in_sql(self):
        sql = load_schema_sql()
        for table in EXPECTED_TABLES:
            assert re.search(
                rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\b",
                sql,
                flags=re.IGNORECASE,
            )

    def test_schema_declares_fingerprint_unique(self):
        sql = load_schema_sql()
        assert "row_fingerprint" in sql
        assert "UNIQUE" in sql

    def test_schema_excludes_hold_and_otlezka(self):
        sql = load_schema_sql().lower()
        assert "hold" not in sql or "hold_mark" in sql
        assert "отлёжка" not in sql
        assert "otlezka" not in sql

    def test_schema_meta_version_insert(self):
        sql = load_schema_sql()
        assert "schema_version" in sql
        assert "'1'" in sql


class TestMappingAllResults:
    def _sample_row(self) -> dict[str, str]:
        return {
            OPERATION_DATE_COLUMN: "22.06.2026",
            DISABLE_DATE_COLUMN: "22.06.2026 09:00:00",
            "Дата включения": "25.06.2026",
            "Статус включения": "ОЖИДАЕТ",
            "Включено": "",
            "Комментарий включения": "",
            "card": "4111111111111111",
            "partner": "Ostin",
            "action": "remove_partner",
            "status": "OK",
            "comment": "done",
            "hold": "",
        }

    def test_map_all_results_row_fields(self):
        row = self._sample_row()
        mapped = map_all_results_row(row, run_id="run-abc", source_row_index=3)

        assert isinstance(mapped, RegistryResultRow)
        assert mapped.run_id == "run-abc"
        assert mapped.source_row_index == 3
        assert mapped.card == "4111111111111111"
        assert mapped.partner == "Ostin"
        assert mapped.action == "remove_partner"
        assert mapped.status == "OK"
        assert mapped.operation_date == "22.06.2026"
        assert mapped.disable_at == "22.06.2026 09:00:00"
        assert mapped.reenable_date == "25.06.2026"
        assert mapped.enable_status == "ОЖИДАЕТ"
        assert mapped.comment == "done"

    def test_fingerprint_matches_lifecycle_helper(self):
        row = self._sample_row()
        mapped = map_all_results_row(row)
        assert mapped.row_fingerprint == result_row_fingerprint(row)

    def test_map_from_dataframe_series(self):
        df = pd.DataFrame([self._sample_row()], columns=ALL_RESULTS_COLUMNS)
        mapped = map_all_results_row(df.iloc[0])
        assert mapped.card == "4111111111111111"

    def test_requires_card(self):
        row = self._sample_row()
        row["card"] = ""
        with pytest.raises(ValueError, match="card"):
            map_all_results_row(row)

    def test_requires_action(self):
        row = self._sample_row()
        row["action"] = ""
        with pytest.raises(ValueError, match="action"):
            map_all_results_row(row)

    def test_optional_fields_become_none(self):
        row = self._sample_row()
        row["comment"] = ""
        row["hold"] = ""
        mapped = map_all_results_row(row)
        assert mapped.comment is None
        assert mapped.hold_mark is None


class TestMappingRuns:
    def test_map_runs_row(self):
        row = {
            "started_at": "22.06.2026 09:00:00",
            "finished_at": "22.06.2026 09:05:00",
            "input_rows": 10,
            "success_rows": 8,
            "failed_rows": 1,
            "skipped_rows": 1,
            "output_file": "result.xlsx",
        }
        mapped = map_runs_row(
            row,
            run_id="run-xyz",
            operator_profile="DENIS",
            source="telegram_manual",
        )
        assert isinstance(mapped, RegistryRunRow)
        assert mapped.run_id == "run-xyz"
        assert mapped.input_rows == 10
        assert mapped.success_rows == 8
        assert mapped.operator_profile == "DENIS"
        assert mapped.source == "telegram_manual"

    def test_runs_row_dict_roundtrip_keys(self):
        mapped = map_runs_row(
            {
                "started_at": "22.06.2026 09:00:00",
                "finished_at": "22.06.2026 09:05:00",
                "input_rows": 1,
                "success_rows": 1,
                "failed_rows": 0,
                "skipped_rows": 0,
                "output_file": "out.xlsx",
            },
            run_id="r1",
        )
        as_dict = runs_row_dict_for_columns(mapped)
        assert validate_runs_columns(list(as_dict.keys()))
        assert list(as_dict.keys()) == [
            "started_at",
            "finished_at",
            "input_rows",
            "success_rows",
            "failed_rows",
            "skipped_rows",
            "output_file",
        ]

    def test_validate_runs_columns(self):
        assert validate_runs_columns(RUNS_COLUMNS) is True
        assert validate_runs_columns(["started_at"]) is False

    def test_requires_run_id(self):
        with pytest.raises(ValueError, match="run_id"):
            map_runs_row({"started_at": "a", "finished_at": "b"}, run_id="")

    def test_requires_timestamps(self):
        with pytest.raises(ValueError, match="started_at"):
            map_runs_row(
                {
                    "started_at": "",
                    "finished_at": "22.06.2026 09:05:00",
                    "input_rows": 0,
                    "success_rows": 0,
                    "failed_rows": 0,
                    "skipped_rows": 0,
                },
                run_id="r1",
            )
