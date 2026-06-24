"""Feature flags and env for Wallet Editor registry DB mirror."""

from __future__ import annotations

import os

ENV_MIRROR_ENABLED = "WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED"
ENV_REGISTRY_SOURCE = "WALLET_EDITOR_REGISTRY_SOURCE"
ENV_DATABASE_URL = "DATABASE_URL"

REGISTRY_SOURCE_EXCEL = "excel"
REGISTRY_SOURCE_POSTGRES = "postgres"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def mirror_enabled() -> bool:
    """Return True when shadow-write mirror is enabled. Default: off."""
    return os.getenv(ENV_MIRROR_ENABLED, "0").strip().lower() in _TRUTHY


def registry_source() -> str:
    """Return registry source of truth: excel (default) or postgres."""
    value = os.getenv(ENV_REGISTRY_SOURCE, REGISTRY_SOURCE_EXCEL).strip().lower()
    if value == REGISTRY_SOURCE_POSTGRES:
        return REGISTRY_SOURCE_POSTGRES
    return REGISTRY_SOURCE_EXCEL


def registry_source_is_postgres() -> bool:
    return registry_source() == REGISTRY_SOURCE_POSTGRES


def get_database_url() -> str | None:
    """Return DATABASE_URL when set and non-empty."""
    url = os.getenv(ENV_DATABASE_URL, "").strip()
    return url or None
