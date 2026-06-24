"""Lazy PostgreSQL connection helper for registry mirror."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from integrations.wallet_editor_registry_db.config import get_database_url, mirror_enabled
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)


class MirrorDisabledError(RuntimeError):
    """Raised when a mirror-only DB operation is requested while mirror is disabled."""


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when DATABASE_URL is missing but a connection was required."""


@contextmanager
def connect(
    *,
    for_mirror: bool = False,
) -> Generator[Any, None, None]:
    """
    Open a psycopg connection context.

    Parameters
    ----------
    for_mirror:
        When True, requires ``WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED=1``.
        When False, connects whenever DATABASE_URL is set (import/migrate tools).
    """
    if for_mirror and not mirror_enabled():
        raise MirrorDisabledError(
            "WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED is off; mirror DB access denied"
        )

    url = get_database_url()
    if not url:
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for registry DB mirror; install psycopg[binary]"
        ) from exc

    conn = None
    try:
        conn = psycopg.connect(
            url,
            connect_timeout=30,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
        yield conn
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


def ping(*, for_mirror: bool = False) -> bool:
    """Return True if DB is reachable. Never raises — logs errors."""
    try:
        with connect(for_mirror=for_mirror) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        log.exception("[WalletEditorRegistryDB] ping failed for_mirror=%s", for_mirror)
        return False
