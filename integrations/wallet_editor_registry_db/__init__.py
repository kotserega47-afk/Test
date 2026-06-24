"""Wallet Editor registry PostgreSQL mirror (Phase 1 — Stage A infrastructure)."""

from integrations.wallet_editor_registry_db.config import (
    ENV_DATABASE_URL,
    ENV_MIRROR_ENABLED,
    mirror_enabled,
)
from integrations.wallet_editor_registry_db.connection import (
    MirrorDisabledError,
    connect,
    get_database_url,
)
from integrations.wallet_editor_registry_db.import_workbook import (
    ImportStats,
    ImportWorkbookError,
    import_registry_frames,
    import_registry_workbook,
    load_registry_dataframes,
)
from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore
from integrations.wallet_editor_registry_db.mapping import (
    map_all_results_row,
    map_runs_row,
)
from integrations.wallet_editor_registry_db.models import RegistryResultRow, RegistryRunRow
from integrations.wallet_editor_registry_db.schema import (
    EXPECTED_TABLES,
    SCHEMA_VERSION,
    load_schema_sql,
)

__all__ = [
    "ENV_DATABASE_URL",
    "ENV_MIRROR_ENABLED",
    "EXPECTED_TABLES",
    "MirrorDisabledError",
    "RegistryResultRow",
    "RegistryRunRow",
    "SCHEMA_VERSION",
    "connect",
    "get_database_url",
    "load_schema_sql",
    "InMemoryRegistryStore",
    "ImportStats",
    "ImportWorkbookError",
    "import_registry_frames",
    "import_registry_workbook",
    "load_registry_dataframes",
    "map_all_results_row",
    "map_runs_row",
    "mirror_enabled",
]
