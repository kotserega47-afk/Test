"""Schema metadata and SQL loader for registry mirror."""

from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = 1

EXPECTED_TABLES = frozenset(
    {
        "we_registry_meta",
        "we_registry_runs",
        "we_registry_results",
        "we_registry_reconcile",
    }
)


def schema_sql_path() -> Path:
    return Path(__file__).with_name("schema.sql")


def load_schema_sql() -> str:
    return schema_sql_path().read_text(encoding="utf-8")
