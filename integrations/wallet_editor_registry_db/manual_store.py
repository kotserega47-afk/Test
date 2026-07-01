"""PostgreSQL persistence for manual hold/Отлёжка sync."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from integrations.wallet_editor_registry_db.manual_snapshot import (
    HoldSnapshotRow,
    ManualSnapshot,
    OtlezkaSnapshotRow,
)

META_UPSERT_SQL = """
INSERT INTO we_registry_meta (key, value, updated_at)
VALUES (%(key)s, %(value)s, now())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = EXCLUDED.updated_at
"""

INSERT_SYNC_RUN_SQL = """
INSERT INTO we_registry_manual_sync_runs (
    sync_id,
    status,
    triggered_by,
    triggered_by_actor,
    source_path,
    source_rev,
    content_hash,
    hold_rows_seen,
    hold_rows_activated,
    hold_rows_deactivated,
    otlezka_rows_seen,
    otlezka_rows_activated,
    otlezka_rows_deactivated,
    validation_warnings,
    error_code,
    error_message,
    started_at,
    finished_at
) VALUES (
    %(sync_id)s,
    %(status)s,
    %(triggered_by)s,
    %(triggered_by_actor)s,
    %(source_path)s,
    %(source_rev)s,
    %(content_hash)s,
    %(hold_rows_seen)s,
    %(hold_rows_activated)s,
    %(hold_rows_deactivated)s,
    %(otlezka_rows_seen)s,
    %(otlezka_rows_activated)s,
    %(otlezka_rows_deactivated)s,
    %(validation_warnings)s,
    %(error_code)s,
    %(error_message)s,
    %(started_at)s,
    %(finished_at)s
)
RETURNING id
"""

UPSERT_HOLD_SQL = """
INSERT INTO we_registry_hold (
    card, partner, card_norm, partner_norm,
    added_at, comment, active,
    source_sync_run_id, source_row_index, synced_at, raw_payload
) VALUES (
    %(card)s, %(partner)s, %(card_norm)s, %(partner_norm)s,
    %(added_at)s, %(comment)s, true,
    %(source_sync_run_id)s, %(source_row_index)s, now(), %(raw_payload)s::jsonb
)
ON CONFLICT (card_norm, partner_norm) WHERE active = true
DO UPDATE SET
    card = EXCLUDED.card,
    partner = EXCLUDED.partner,
    added_at = EXCLUDED.added_at,
    comment = EXCLUDED.comment,
    source_sync_run_id = EXCLUDED.source_sync_run_id,
    source_row_index = EXCLUDED.source_row_index,
    synced_at = now(),
    raw_payload = EXCLUDED.raw_payload,
    deactivated_at = NULL
"""

UPSERT_OTLEZKA_SQL = """
INSERT INTO we_registry_otlezka (
    partner, partner_norm, full_days, comment, active,
    source_sync_run_id, source_row_index, synced_at, raw_payload
) VALUES (
    %(partner)s, %(partner_norm)s, %(full_days)s, %(comment)s, true,
    %(source_sync_run_id)s, %(source_row_index)s, now(), %(raw_payload)s::jsonb
)
ON CONFLICT (partner_norm) WHERE active = true
DO UPDATE SET
    partner = EXCLUDED.partner,
    full_days = EXCLUDED.full_days,
    comment = EXCLUDED.comment,
    source_sync_run_id = EXCLUDED.source_sync_run_id,
    source_row_index = EXCLUDED.source_row_index,
    synced_at = now(),
    raw_payload = EXCLUDED.raw_payload,
    deactivated_at = NULL
"""

DEACTIVATE_OTLEZKA_SQL = """
UPDATE we_registry_otlezka
SET active = false, deactivated_at = now()
WHERE active = true
  AND partner_norm <> ALL(%(partners)s::text[])
"""

UPDATE_SYNC_RUN_SQL = """
UPDATE we_registry_manual_sync_runs
SET status = %(status)s,
    finished_at = %(finished_at)s,
    hold_rows_activated = %(hold_rows_activated)s,
    hold_rows_deactivated = %(hold_rows_deactivated)s,
    otlezka_rows_activated = %(otlezka_rows_activated)s,
    otlezka_rows_deactivated = %(otlezka_rows_deactivated)s,
    error_code = %(error_code)s,
    error_message = %(error_message)s
WHERE id = %(id)s
"""

COUNT_ACTIVE_HOLD_SQL = "SELECT COUNT(*) FROM we_registry_hold WHERE active = true"
COUNT_ACTIVE_OTLEZKA_SQL = "SELECT COUNT(*) FROM we_registry_otlezka WHERE active = true"


@dataclass(frozen=True, slots=True)
class ManualSyncPersistResult:
    sync_run_id: int
    sync_id: str
    hold_activated: int
    hold_deactivated: int
    otlezka_activated: int
    otlezka_deactivated: int


class ManualSyncStore(Protocol):
    def get_meta(self, key: str) -> str | None: ...

    def set_meta(self, key: str, value: str) -> None: ...

    def persist_sync_run(self, **fields: Any) -> int: ...

    def finish_sync_run(self, run_id: int, **fields: Any) -> None: ...

    def apply_snapshot(
        self,
        snapshot: ManualSnapshot,
        *,
        sync_run_id: int,
    ) -> ManualSyncPersistResult: ...

    def count_active_hold(self) -> int: ...

    def count_active_otlezka(self) -> int: ...


def _hold_payload(row: HoldSnapshotRow) -> dict:
    return {
        "card": row.card,
        "partner": row.partner,
        "added_at": row.added_at,
        "comment": row.comment,
    }


def _otlezka_payload(row: OtlezkaSnapshotRow) -> dict:
    return {
        "partner": row.partner,
        "full_days": row.full_days,
        "comment": row.comment,
    }


class PostgresManualSyncStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def get_meta(self, key: str) -> str | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM we_registry_meta WHERE key = %s",
                (key,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(META_UPSERT_SQL, {"key": key, "value": value})

    def persist_sync_run(self, **fields: Any) -> int:
        with self._conn.cursor() as cur:
            cur.execute(INSERT_SYNC_RUN_SQL, fields)
            row = cur.fetchone()
            assert row is not None
            return int(row[0])

    def finish_sync_run(self, run_id: int, **fields: Any) -> None:
        payload = {"id": run_id, **fields}
        with self._conn.cursor() as cur:
            cur.execute(UPDATE_SYNC_RUN_SQL, payload)

    def apply_snapshot(
        self,
        snapshot: ManualSnapshot,
        *,
        sync_run_id: int,
    ) -> ManualSyncPersistResult:
        hold_keys = {(r.card_norm, r.partner_norm) for r in snapshot.hold_rows}
        otlezka_keys = {r.partner_norm for r in snapshot.otlezka_rows}

        with self._conn.transaction():
            with self._conn.cursor() as cur:
                for row in snapshot.hold_rows:
                    cur.execute(
                        UPSERT_HOLD_SQL,
                        {
                            "card": row.card,
                            "partner": row.partner,
                            "card_norm": row.card_norm,
                            "partner_norm": row.partner_norm,
                            "added_at": row.added_at,
                            "comment": row.comment,
                            "source_sync_run_id": sync_run_id,
                            "source_row_index": row.source_row_index,
                            "raw_payload": json.dumps(_hold_payload(row), ensure_ascii=False),
                        },
                    )

                hold_deactivated = 0
                if hold_keys:
                    cards = [k[0] for k in sorted(hold_keys)]
                    partners = [k[1] for k in sorted(hold_keys)]
                    cur.execute(
                        """
                        UPDATE we_registry_hold
                        SET active = false, deactivated_at = now()
                        WHERE active = true
                          AND (card_norm, partner_norm) NOT IN (
                            SELECT * FROM unnest(%(pairs)s::text[], %(pairs2)s::text[])
                          )
                        """,
                        {"pairs": cards, "pairs2": partners},
                    )
                    hold_deactivated = cur.rowcount
                else:
                    cur.execute(
                        "UPDATE we_registry_hold SET active = false, deactivated_at = now() WHERE active = true"
                    )
                    hold_deactivated = cur.rowcount

                for row in snapshot.otlezka_rows:
                    cur.execute(
                        UPSERT_OTLEZKA_SQL,
                        {
                            "partner": row.partner,
                            "partner_norm": row.partner_norm,
                            "full_days": row.full_days,
                            "comment": row.comment,
                            "source_sync_run_id": sync_run_id,
                            "source_row_index": row.source_row_index,
                            "raw_payload": json.dumps(_otlezka_payload(row), ensure_ascii=False),
                        },
                    )

                otlezka_deactivated = 0
                if otlezka_keys:
                    cur.execute(
                        DEACTIVATE_OTLEZKA_SQL,
                        {"partners": list(otlezka_keys)},
                    )
                    otlezka_deactivated = cur.rowcount
                else:
                    cur.execute(
                        "UPDATE we_registry_otlezka SET active = false, deactivated_at = now() WHERE active = true"
                    )
                    otlezka_deactivated = cur.rowcount

        return ManualSyncPersistResult(
            sync_run_id=sync_run_id,
            sync_id=str(sync_run_id),
            hold_activated=len(snapshot.hold_rows),
            hold_deactivated=hold_deactivated,
            otlezka_activated=len(snapshot.otlezka_rows),
            otlezka_deactivated=otlezka_deactivated,
        )


class InMemoryManualSyncStore:
    """Test double for manual sync persistence."""

    def __init__(self) -> None:
        self.meta: dict[str, str] = {}
        self.hold: dict[tuple[str, str], dict] = {}
        self.otlezka: dict[str, dict] = {}
        self.sync_runs: list[dict] = []
        self._next_id = 1

    def get_meta(self, key: str) -> str | None:
        return self.meta.get(key)

    def set_meta(self, key: str, value: str) -> None:
        self.meta[key] = value

    def persist_sync_run(self, **fields: Any) -> int:
        run_id = self._next_id
        self._next_id += 1
        self.sync_runs.append({"id": run_id, **fields})
        return run_id

    def finish_sync_run(self, run_id: int, **fields: Any) -> None:
        for run in self.sync_runs:
            if run["id"] == run_id:
                run.update(fields)
                return

    def apply_snapshot(
        self,
        snapshot: ManualSnapshot,
        *,
        sync_run_id: int,
    ) -> ManualSyncPersistResult:
        new_hold_keys = {(r.card_norm, r.partner_norm) for r in snapshot.hold_rows}
        new_otlezka_keys = {r.partner_norm for r in snapshot.otlezka_rows}

        hold_deactivated = 0
        for key, row in list(self.hold.items()):
            if key not in new_hold_keys and row.get("active"):
                row["active"] = False
                hold_deactivated += 1
        otlezka_deactivated = 0
        for key, row in list(self.otlezka.items()):
            if key not in new_otlezka_keys and row.get("active"):
                row["active"] = False
                otlezka_deactivated += 1

        for row in snapshot.hold_rows:
            key = (row.card_norm, row.partner_norm)
            self.hold[key] = {
                "row": row,
                "active": True,
                "sync_run_id": sync_run_id,
            }

        for row in snapshot.otlezka_rows:
            self.otlezka[row.partner_norm] = {
                "row": row,
                "active": True,
                "sync_run_id": sync_run_id,
            }

        return ManualSyncPersistResult(
            sync_run_id=sync_run_id,
            sync_id=str(sync_run_id),
            hold_activated=len(snapshot.hold_rows),
            hold_deactivated=hold_deactivated,
            otlezka_activated=len(snapshot.otlezka_rows),
            otlezka_deactivated=otlezka_deactivated,
        )

    def count_active_hold(self) -> int:
        return sum(1 for v in self.hold.values() if v.get("active"))

    def count_active_otlezka(self) -> int:
        return sum(1 for v in self.otlezka.values() if v.get("active"))


def new_sync_id() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
