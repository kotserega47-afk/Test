"""PostgreSQL upsert helpers for registry mirror import."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Protocol, Sequence

from integrations.wallet_editor_registry_db.models import RegistryResultRow, RegistryRunRow

INSERT_RUN_SQL = """
INSERT INTO we_registry_runs (
    run_id,
    started_at,
    finished_at,
    input_rows,
    success_rows,
    failed_rows,
    skipped_rows,
    output_file,
    operator_profile,
    source
) VALUES (
    %(run_id)s,
    %(started_at)s,
    %(finished_at)s,
    %(input_rows)s,
    %(success_rows)s,
    %(failed_rows)s,
    %(skipped_rows)s,
    %(output_file)s,
    %(operator_profile)s,
    %(source)s
)
ON CONFLICT (run_id) DO NOTHING
RETURNING run_id
"""

INSERT_RESULT_SQL = """
INSERT INTO we_registry_results (
    run_id,
    operation_date,
    disable_at,
    reenable_date,
    enable_status,
    vklyucheno,
    enable_comment,
    card,
    partner,
    action,
    status,
    comment,
    hold_mark,
    row_fingerprint,
    source_row_index
) VALUES (
    %(run_id)s,
    %(operation_date)s,
    %(disable_at)s,
    %(reenable_date)s,
    %(enable_status)s,
    %(vklyucheno)s,
    %(enable_comment)s,
    %(card)s,
    %(partner)s,
    %(action)s,
    %(status)s,
    %(comment)s,
    %(hold_mark)s,
    %(row_fingerprint)s,
    %(source_row_index)s
)
ON CONFLICT (row_fingerprint) DO UPDATE SET
    run_id = EXCLUDED.run_id,
    operation_date = EXCLUDED.operation_date,
    disable_at = EXCLUDED.disable_at,
    reenable_date = EXCLUDED.reenable_date,
    enable_status = EXCLUDED.enable_status,
    vklyucheno = EXCLUDED.vklyucheno,
    enable_comment = EXCLUDED.enable_comment,
    card = EXCLUDED.card,
    partner = EXCLUDED.partner,
    action = EXCLUDED.action,
    status = EXCLUDED.status,
    comment = EXCLUDED.comment,
    hold_mark = EXCLUDED.hold_mark,
    source_row_index = EXCLUDED.source_row_index,
    updated_at = now()
RETURNING row_fingerprint, (xmax = 0) AS inserted
"""

INSERT_RESULT_BATCH_SQL = """
INSERT INTO we_registry_results (
    run_id,
    operation_date,
    disable_at,
    reenable_date,
    enable_status,
    vklyucheno,
    enable_comment,
    card,
    partner,
    action,
    status,
    comment,
    hold_mark,
    row_fingerprint,
    source_row_index
) VALUES (
    %(run_id)s,
    %(operation_date)s,
    %(disable_at)s,
    %(reenable_date)s,
    %(enable_status)s,
    %(vklyucheno)s,
    %(enable_comment)s,
    %(card)s,
    %(partner)s,
    %(action)s,
    %(status)s,
    %(comment)s,
    %(hold_mark)s,
    %(row_fingerprint)s,
    %(source_row_index)s
)
ON CONFLICT (row_fingerprint) DO UPDATE SET
    run_id = EXCLUDED.run_id,
    operation_date = EXCLUDED.operation_date,
    disable_at = EXCLUDED.disable_at,
    reenable_date = EXCLUDED.reenable_date,
    enable_status = EXCLUDED.enable_status,
    vklyucheno = EXCLUDED.vklyucheno,
    enable_comment = EXCLUDED.enable_comment,
    card = EXCLUDED.card,
    partner = EXCLUDED.partner,
    action = EXCLUDED.action,
    status = EXCLUDED.status,
    comment = EXCLUDED.comment,
    hold_mark = EXCLUDED.hold_mark,
    source_row_index = EXCLUDED.source_row_index,
    updated_at = now()
"""

INSERT_RUN_BATCH_SQL = """
INSERT INTO we_registry_runs (
    run_id,
    started_at,
    finished_at,
    input_rows,
    success_rows,
    failed_rows,
    skipped_rows,
    output_file,
    operator_profile,
    source
) VALUES (
    %(run_id)s,
    %(started_at)s,
    %(finished_at)s,
    %(input_rows)s,
    %(success_rows)s,
    %(failed_rows)s,
    %(skipped_rows)s,
    %(output_file)s,
    %(operator_profile)s,
    %(source)s
)
ON CONFLICT (run_id) DO NOTHING
"""

PATCH_LIFECYCLE_SQL = """
UPDATE we_registry_results SET
    reenable_date = %(reenable_date)s,
    enable_status = %(enable_status)s,
    hold_mark = %(hold_mark)s,
    updated_at = now()
WHERE row_fingerprint = %(row_fingerprint)s
"""


class RegistryStore(Protocol):
    def upsert_run(self, row: RegistryRunRow) -> bool:
        """Return True when a new run row was inserted."""

    def upsert_result(self, row: RegistryResultRow) -> bool:
        """Return True when a new result row was inserted (False if updated existing)."""

    def patch_lifecycle_fields(
        self,
        *,
        row_fingerprint: str,
        reenable_date: str | None,
        enable_status: str | None,
        hold_mark: str | None,
    ) -> bool:
        """Update lifecycle columns only. Return True when a row was updated."""


def _run_params(row: RegistryRunRow) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "input_rows": row.input_rows,
        "success_rows": row.success_rows,
        "failed_rows": row.failed_rows,
        "skipped_rows": row.skipped_rows,
        "output_file": row.output_file,
        "operator_profile": row.operator_profile,
        "source": row.source,
    }


def _result_params(row: RegistryResultRow) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "operation_date": row.operation_date,
        "disable_at": row.disable_at,
        "reenable_date": row.reenable_date,
        "enable_status": row.enable_status,
        "vklyucheno": row.vklyucheno,
        "enable_comment": row.enable_comment,
        "card": row.card,
        "partner": row.partner,
        "action": row.action,
        "status": row.status,
        "comment": row.comment,
        "hold_mark": row.hold_mark,
        "row_fingerprint": row.row_fingerprint,
        "source_row_index": row.source_row_index,
    }


class PostgresRegistryStore:
    """Upsert registry rows into PostgreSQL."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def upsert_run(self, row: RegistryRunRow) -> bool:
        self._cursor.execute(INSERT_RUN_SQL, _run_params(row))
        return self._cursor.fetchone() is not None

    def upsert_result(self, row: RegistryResultRow) -> bool:
        self._cursor.execute(INSERT_RESULT_SQL, _result_params(row))
        fetched = self._cursor.fetchone()
        if not fetched:
            return False
        _fingerprint, inserted = fetched
        return bool(inserted)

    def upsert_runs_batch(self, rows: Sequence[RegistryRunRow]) -> int:
        if not rows:
            return 0
        self._cursor.executemany(INSERT_RUN_BATCH_SQL, [_run_params(row) for row in rows])
        return len(rows)

    def upsert_results_batch(self, rows: Sequence[RegistryResultRow]) -> int:
        if not rows:
            return 0
        self._cursor.executemany(
            INSERT_RESULT_BATCH_SQL,
            [_result_params(row) for row in rows],
        )
        return len(rows)

    def patch_lifecycle_fields(
        self,
        *,
        row_fingerprint: str,
        reenable_date: str | None,
        enable_status: str | None,
        hold_mark: str | None,
    ) -> bool:
        self._cursor.execute(
            PATCH_LIFECYCLE_SQL,
            {
                "row_fingerprint": row_fingerprint,
                "reenable_date": reenable_date,
                "enable_status": enable_status,
                "hold_mark": hold_mark,
            },
        )
        return self._cursor.rowcount > 0


class InMemoryRegistryStore:
    """Test double for import idempotency without a live database."""

    def __init__(self) -> None:
        self.runs: dict[str, RegistryRunRow] = {}
        self.results: dict[str, RegistryResultRow] = {}

    def upsert_run(self, row: RegistryRunRow) -> bool:
        if row.run_id in self.runs:
            return False
        self.runs[row.run_id] = row
        return True

    def upsert_result(self, row: RegistryResultRow) -> bool:
        existed = row.row_fingerprint in self.results
        self.results[row.row_fingerprint] = row
        return not existed

    def patch_lifecycle_fields(
        self,
        *,
        row_fingerprint: str,
        reenable_date: str | None,
        enable_status: str | None,
        hold_mark: str | None,
    ) -> bool:
        existing = self.results.get(row_fingerprint)
        if existing is None:
            return False
        self.results[row_fingerprint] = replace(
            existing,
            reenable_date=reenable_date,
            enable_status=enable_status,
            hold_mark=hold_mark,
        )
        return True
