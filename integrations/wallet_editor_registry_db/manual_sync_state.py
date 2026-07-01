"""Manual sync metadata and health report helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from integrations.wallet_editor_registry_db.manual_store import ManualSyncStore

META_LAST_SOURCE_REV = "last_manual_source_rev"
META_LAST_SNAPSHOT_HASH = "last_manual_snapshot_hash"
META_LAST_SYNC_AT = "last_manual_sync_at"
META_LAST_SYNC_STATUS = "last_manual_sync_status"
META_LAST_SYNC_RUN_ID = "last_manual_sync_run_id"
META_HOLD_ACTIVE_COUNT = "last_manual_hold_active_count"
META_OTLEZKA_ACTIVE_COUNT = "last_manual_otlezka_active_count"

DEFAULT_STALE_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class ManualSyncMeta:
    last_source_rev: str | None
    last_snapshot_hash: str | None
    last_sync_at: str | None
    last_sync_status: str | None
    last_sync_run_id: str | None
    active_hold_rows: int
    active_otlezka_rows: int


@dataclass(frozen=True, slots=True)
class ManualSnapshotHealthReport:
    current_dropbox_rev: str | None
    last_synced_dropbox_rev: str | None
    current_snapshot_hash_short: str | None
    last_successful_sync_at: str | None
    snapshot_age_sec: float | None
    active_hold_rows: int
    active_otlezka_rows: int
    last_sync_status: str | None
    stale: bool
    failed: bool


def stale_threshold_seconds() -> int:
    raw = os.getenv("WALLET_EDITOR_MANUAL_SYNC_MAX_AGE_SEC", str(DEFAULT_STALE_SECONDS))
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_STALE_SECONDS


def load_manual_sync_meta(store: ManualSyncStore) -> ManualSyncMeta:
    def _int_meta(key: str) -> int:
        raw = store.get_meta(key)
        if not raw:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    return ManualSyncMeta(
        last_source_rev=store.get_meta(META_LAST_SOURCE_REV),
        last_snapshot_hash=store.get_meta(META_LAST_SNAPSHOT_HASH),
        last_sync_at=store.get_meta(META_LAST_SYNC_AT),
        last_sync_status=store.get_meta(META_LAST_SYNC_STATUS),
        last_sync_run_id=store.get_meta(META_LAST_SYNC_RUN_ID),
        active_hold_rows=_int_meta(META_HOLD_ACTIVE_COUNT) or store.count_active_hold(),
        active_otlezka_rows=_int_meta(META_OTLEZKA_ACTIVE_COUNT) or store.count_active_otlezka(),
    )


def save_manual_sync_meta(
    store: ManualSyncStore,
    *,
    source_rev: str | None,
    snapshot_hash: str,
    sync_status: str,
    sync_run_id: int,
    active_hold: int,
    active_otlezka: int,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    store.set_meta(META_LAST_SOURCE_REV, source_rev or "")
    store.set_meta(META_LAST_SNAPSHOT_HASH, snapshot_hash)
    store.set_meta(META_LAST_SYNC_AT, now)
    store.set_meta(META_LAST_SYNC_STATUS, sync_status)
    store.set_meta(META_LAST_SYNC_RUN_ID, str(sync_run_id))
    store.set_meta(META_HOLD_ACTIVE_COUNT, str(active_hold))
    store.set_meta(META_OTLEZKA_ACTIVE_COUNT, str(active_otlezka))


def record_manual_sync_failure(store: ManualSyncStore, *, sync_run_id: int) -> None:
    """Record failed attempt without overwriting last good snapshot metadata."""
    store.set_meta(META_LAST_SYNC_STATUS, "failed")
    store.set_meta(META_LAST_SYNC_RUN_ID, str(sync_run_id))


def _parse_iso_age_seconds(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        parsed = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds()
    except ValueError:
        return None


def build_manual_snapshot_health_report(
    store: ManualSyncStore,
    *,
    current_dropbox_rev: str | None,
    current_snapshot_hash: str | None = None,
) -> ManualSnapshotHealthReport:
    meta = load_manual_sync_meta(store)
    age = _parse_iso_age_seconds(meta.last_sync_at)
    threshold = stale_threshold_seconds()
    rev_drift = bool(
        current_dropbox_rev
        and meta.last_source_rev
        and current_dropbox_rev != meta.last_source_rev
    )
    stale = bool(
        age is not None and age > threshold
    ) or rev_drift
    failed = meta.last_sync_status == "failed"
    hash_short = (current_snapshot_hash or meta.last_snapshot_hash or "")[:12] or None

    return ManualSnapshotHealthReport(
        current_dropbox_rev=current_dropbox_rev,
        last_synced_dropbox_rev=meta.last_source_rev,
        current_snapshot_hash_short=hash_short,
        last_successful_sync_at=meta.last_sync_at,
        snapshot_age_sec=age,
        active_hold_rows=store.count_active_hold(),
        active_otlezka_rows=store.count_active_otlezka(),
        last_sync_status=meta.last_sync_status,
        stale=stale,
        failed=failed,
    )


def format_manual_snapshot_health_report(
    report: ManualSnapshotHealthReport,
    *,
    sync_enabled: bool | None = None,
) -> str:
    from integrations.wallet_editor_registry_db.config import manual_sync_enabled

    from integrations.wallet_editor_registry_db.config import manual_readers_source

    enabled = manual_sync_enabled() if sync_enabled is None else sync_enabled
    readers_source = manual_readers_source()
    lines = [
        "Manual snapshot health",
        f"manual sync enabled: {enabled}",
        f"manual readers source: {readers_source}",
        f"current Dropbox rev: {report.current_dropbox_rev or 'n/a'}",
        f"last synced rev: {report.last_synced_dropbox_rev or 'n/a'}",
        f"snapshot hash: {report.current_snapshot_hash_short or 'n/a'}",
        f"last successful sync: {report.last_successful_sync_at or 'never'}",
        f"snapshot age sec: {report.snapshot_age_sec if report.snapshot_age_sec is not None else 'n/a'}",
        f"active hold rows: {report.active_hold_rows}",
        f"active Отлёжка rows: {report.active_otlezka_rows}",
        f"last sync status: {report.last_sync_status or 'n/a'}",
        f"stale: {report.stale}",
        f"failed: {report.failed}",
    ]
    return "\n".join(lines)
