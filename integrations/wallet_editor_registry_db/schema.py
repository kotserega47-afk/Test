"""Schema metadata and SQL loader for registry mirror."""

from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = 2

EXPECTED_TABLES = frozenset(
    {
        "we_registry_meta",
        "we_registry_runs",
        "we_registry_results",
        "we_registry_reconcile",
        "we_registry_hold",
        "we_registry_otlezka",
        "we_registry_manual_sync_runs",
    }
)


def schema_sql_path() -> Path:
    return Path(__file__).with_name("schema.sql")


def load_schema_sql() -> str:
    return schema_sql_path().read_text(encoding="utf-8")
