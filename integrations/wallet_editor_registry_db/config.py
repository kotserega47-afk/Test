"""Feature flags and env for Wallet Editor registry DB mirror."""

from __future__ import annotations

import os

ENV_MIRROR_ENABLED = "WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED"
ENV_REGISTRY_SOURCE = "WALLET_EDITOR_REGISTRY_SOURCE"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_MANUAL_SYNC_ENABLED = "WALLET_EDITOR_MANUAL_SYNC_ENABLED"
ENV_MANUAL_READERS_SOURCE = "WALLET_EDITOR_MANUAL_READERS_SOURCE"

REGISTRY_SOURCE_POSTGRES = "postgres"
REGISTRY_SOURCE_EXCEL = "excel"  # removed — kept for error messages only

MANUAL_READERS_SOURCE_DROPBOX = "dropbox"
MANUAL_READERS_SOURCE_POSTGRES = "postgres"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class LegacyRegistrySourceError(RuntimeError):
    """Raised when WALLET_EDITOR_REGISTRY_SOURCE=excel is requested."""


def mirror_enabled() -> bool:
    """Return True when shadow-write mirror is enabled. Default: off."""
    return os.getenv(ENV_MIRROR_ENABLED, "0").strip().lower() in _TRUTHY


def registry_source() -> str:
    """Return registry source of truth. Only postgres is supported."""
    value = os.getenv(ENV_REGISTRY_SOURCE, REGISTRY_SOURCE_POSTGRES).strip().lower()
    if value == REGISTRY_SOURCE_EXCEL:
        raise LegacyRegistrySourceError(
            "WALLET_EDITOR_REGISTRY_SOURCE=excel is no longer supported. "
            "Registry history is PostgreSQL-only. Rollback via Git/deploy rollback."
        )
    return REGISTRY_SOURCE_POSTGRES


def registry_source_is_postgres() -> bool:
    registry_source()
    return True


def get_database_url() -> str | None:
    """Return DATABASE_URL when set and non-empty."""
    url = os.getenv(ENV_DATABASE_URL, "").strip()
    return url or None


def manual_sync_enabled() -> bool:
    """Return True when manual workbook → PG sync is enabled. Default: off."""
    return os.getenv(ENV_MANUAL_SYNC_ENABLED, "0").strip().lower() in _TRUTHY


def manual_readers_source() -> str:
    """Return manual hold/otlezka readers source: dropbox (default) or postgres."""
    value = os.getenv(ENV_MANUAL_READERS_SOURCE, MANUAL_READERS_SOURCE_DROPBOX).strip().lower()
    if value == MANUAL_READERS_SOURCE_POSTGRES:
        return MANUAL_READERS_SOURCE_POSTGRES
    return MANUAL_READERS_SOURCE_DROPBOX


def manual_readers_source_is_postgres() -> bool:
    return manual_readers_source() == MANUAL_READERS_SOURCE_POSTGRES
