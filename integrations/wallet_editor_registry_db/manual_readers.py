"""Runtime readers for manual hold / Отлёжка (PostgreSQL or Dropbox fallback)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from integrations.wallet_editor_registry_db.config import (
    manual_readers_source,
    manual_readers_source_is_postgres,
)
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError, connect
from integrations.wallet_editor_registry_db.manual_store import (
    InMemoryManualSyncStore,
    ManualSyncStore,
    PostgresManualSyncStore,
)
from integrations.wallet_editor_registry_db.manual_sync_state import (
    META_LAST_SNAPSHOT_HASH,
    META_LAST_SYNC_AT,
    META_LAST_SYNC_STATUS,
)
from integrations.wallet_editor_registry_lifecycle import HOLD_COLUMNS, OTLEZKA_COLUMNS
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

SUCCESSFUL_MANUAL_SYNC_STATUSES = frozenset({"success", "skipped_hash", "skipped_rev"})

PG_MANUAL_READERS_NOT_READY = (
    "PG manual readers require at least one successful manual sync"
)


class ManualReadersNotReadyError(RuntimeError):
    """Raised when postgres manual readers are used without a prior successful sync."""


def has_successful_manual_sync(*, store: ManualSyncStore | None = None) -> bool:
    if store is not None:
        return _has_successful_manual_sync_store(store)
    try:
        with connect(for_mirror=False) as conn:
            return _has_successful_manual_sync_store(PostgresManualSyncStore(conn))
    except (DatabaseNotConfiguredError, RuntimeError):
        return False


def _has_successful_manual_sync_store(store: ManualSyncStore) -> bool:
    status = store.get_meta(META_LAST_SYNC_STATUS)
    snapshot_hash = store.get_meta(META_LAST_SNAPSHOT_HASH)
    synced_at = store.get_meta(META_LAST_SYNC_AT)
    return bool(
        status in SUCCESSFUL_MANUAL_SYNC_STATUSES
        and snapshot_hash
        and synced_at
    )


def require_pg_manual_readers_ready(*, store: ManualSyncStore | None = None) -> None:
    if not manual_readers_source_is_postgres():
        return
    if not has_successful_manual_sync(store=store):
        raise ManualReadersNotReadyError(PG_MANUAL_READERS_NOT_READY)


def pg_manual_readers_missing_successful_sync() -> bool:
    return manual_readers_source_is_postgres() and not has_successful_manual_sync()


FETCH_ACTIVE_HOLD_SQL = """
SELECT card, partner, added_at, comment
FROM we_registry_hold
WHERE active = true
ORDER BY card_norm, partner_norm
"""

FETCH_ACTIVE_OTLEZKA_SQL = """
SELECT partner, full_days, comment
FROM we_registry_otlezka
WHERE active = true
ORDER BY partner_norm
"""


def _hold_df_from_records(records: list[dict[str, object]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=HOLD_COLUMNS)
    rows = [
        {
            "Дата добавления": str(record.get("added_at") or ""),
            "card": record["card"],
            "partner": record["partner"],
            "comment": str(record.get("comment") or ""),
        }
        for record in records
    ]
    return pd.DataFrame(rows, columns=HOLD_COLUMNS)


def _otlezka_df_from_records(records: list[dict[str, object]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=OTLEZKA_COLUMNS)
    rows = []
    for record in records:
        rows.append(
            {
                "partner": record["partner"],
                "Полные дни": int(record["full_days"]),
                "comment": record.get("comment") or "",
            }
        )
    return pd.DataFrame(rows, columns=OTLEZKA_COLUMNS)


def load_hold_otlezka_frames_from_postgres(
    *,
    store: ManualSyncStore | None = None,
    connection: Any | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    require_pg_manual_readers_ready(store=store)

    if store is not None:
        return _load_hold_otlezka_from_store(store)

    if connection is not None:
        return _load_hold_otlezka_from_store(PostgresManualSyncStore(connection))

    with connect(for_mirror=False) as conn:
        return _load_hold_otlezka_from_store(PostgresManualSyncStore(conn))


def _load_hold_otlezka_from_store(store: ManualSyncStore) -> tuple[pd.DataFrame, pd.DataFrame]:
    if isinstance(store, InMemoryManualSyncStore):
        hold_records = [
            {
                "card": entry["row"].card,
                "partner": entry["row"].partner,
                "added_at": entry["row"].added_at or "",
                "comment": entry["row"].comment or "",
            }
            for entry in store.hold.values()
            if entry.get("active")
        ]
        otlezka_records = [
            {
                "partner": entry["row"].partner,
                "full_days": entry["row"].full_days,
                "comment": entry["row"].comment or "",
            }
            for entry in store.otlezka.values()
            if entry.get("active")
        ]
        return _hold_df_from_records(hold_records), _otlezka_df_from_records(otlezka_records)

    if isinstance(store, PostgresManualSyncStore):
        conn = store._conn
        with conn.cursor() as cur:
            cur.execute(FETCH_ACTIVE_HOLD_SQL)
            hold_rows = cur.fetchall()
            hold_records = [
                {
                    "card": row[0],
                    "partner": row[1],
                    "added_at": row[2] or "",
                    "comment": row[3] or "",
                }
                for row in hold_rows
            ]
            cur.execute(FETCH_ACTIVE_OTLEZKA_SQL)
            otlezka_rows = cur.fetchall()
            otlezka_records = [
                {
                    "partner": row[0],
                    "full_days": row[1],
                    "comment": row[2] or "",
                }
                for row in otlezka_rows
            ]
        return _hold_df_from_records(hold_records), _otlezka_df_from_records(otlezka_records)

    raise TypeError(f"unsupported manual sync store type: {type(store)!r}")


def load_active_hold_pair_norms_from_postgres(
    *,
    store: ManualSyncStore | None = None,
    connection: Any | None = None,
) -> frozenset[tuple[str, str]]:
    from integrations.wallet_editor_registry_lifecycle import _normalize_key

    require_pg_manual_readers_ready(store=store)
    hold_df, _ = load_hold_otlezka_frames_from_postgres(store=store, connection=connection)
    pairs: set[tuple[str, str]] = set()
    for _, row in hold_df.iterrows():
        card = _normalize_key(str(row.get("card", "")))
        partner = _normalize_key(str(row.get("partner", "")))
        if card and partner:
            pairs.add((card, partner))
    return frozenset(pairs)


def load_hold_otlezka_for_runtime(
    dropbox_path: str,
    *,
    store: ManualSyncStore | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, bool, bool]:
    """
    Load hold + Отлёжка for lifecycle/planning.

    Returns (hold_df, otlezka_df, hold_exists, otlezka_exists).
    """
    if manual_readers_source_is_postgres():
        hold_df, otlezka_df = load_hold_otlezka_frames_from_postgres(store=store)
        return hold_df, otlezka_df, True, not otlezka_df.empty

    from integrations.wallet_editor_registry_db.hold_loader import load_hold_otlezka_from_dropbox

    return load_hold_otlezka_from_dropbox(dropbox_path)


def load_hold_otlezka_frames_for_export(
    *,
    store: ManualSyncStore | None = None,
    connection: Any | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    """
    Load hold + Отлёжка for registry export.

    Does not enforce I-MAN-11 (export is reporting). Returns degraded=True when
    no successful manual sync exists.
    """
    degraded = not has_successful_manual_sync(store=store)

    if store is not None:
        hold_df, otlezka_df = _load_hold_otlezka_from_store(store)
        return hold_df, otlezka_df, degraded

    if connection is not None:
        hold_df, otlezka_df = _load_hold_otlezka_from_store(PostgresManualSyncStore(connection))
        return hold_df, otlezka_df, degraded

    with connect(for_mirror=False) as conn:
        hold_df, otlezka_df = _load_hold_otlezka_from_store(PostgresManualSyncStore(conn))
    return hold_df, otlezka_df, degraded
