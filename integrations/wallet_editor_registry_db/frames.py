"""Load registry history frames from PostgreSQL."""

from __future__ import annotations

from typing import Any

import pandas as pd

from integrations.wallet_editor_registry_db.connection import connect
from integrations.wallet_editor_registry_db.models import RegistryResultRow, RegistryRunRow
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    RUNS_COLUMNS,
)

FETCH_RESULTS_SQL = """
SELECT
    row_fingerprint,
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
    source_row_index
FROM we_registry_results
ORDER BY id
"""

FETCH_RUNS_SQL = """
SELECT
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
FROM we_registry_runs
ORDER BY started_at, run_id
"""


def _result_from_record(record: tuple[Any, ...]) -> RegistryResultRow:
    (
        row_fingerprint,
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
        source_row_index,
    ) = record
    return RegistryResultRow(
        row_fingerprint=row_fingerprint,
        run_id=run_id,
        operation_date=operation_date,
        disable_at=disable_at,
        reenable_date=reenable_date,
        enable_status=enable_status,
        vklyucheno=vklyucheno,
        enable_comment=enable_comment,
        card=card,
        partner=partner or "",
        action=action,
        status=status or "",
        comment=comment,
        hold_mark=hold_mark,
        source_row_index=source_row_index,
    )


def result_row_to_dict(row: RegistryResultRow) -> dict[str, str]:
    return {
        OPERATION_DATE_COLUMN: row.operation_date or "",
        DISABLE_DATE_COLUMN: row.disable_at or "",
        "Дата включения": row.reenable_date or "",
        "Статус включения": row.enable_status or "",
        "Включено": row.vklyucheno or "",
        "Комментарий включения": row.enable_comment or "",
        "card": row.card,
        "partner": row.partner,
        "action": row.action,
        "status": row.status,
        "comment": row.comment or "",
        "hold": row.hold_mark or "",
    }


def run_row_to_dict(row: RegistryRunRow) -> dict[str, object]:
    return {
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "input_rows": row.input_rows,
        "success_rows": row.success_rows,
        "failed_rows": row.failed_rows,
        "skipped_rows": row.skipped_rows,
        "output_file": row.output_file or "",
        "run_id": row.run_id,
        "operator_profile": row.operator_profile or "",
        "source": row.source or "",
    }


def load_registry_frames_from_postgres(
    *,
    connection: Any | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load all_results and runs DataFrames from PostgreSQL."""

    def _load(cur: Any) -> tuple[pd.DataFrame, pd.DataFrame]:
        cur.execute(FETCH_RESULTS_SQL)
        result_records = cur.fetchall()
        result_rows = [_result_from_record(record) for record in result_records]
        if result_rows:
            all_results = pd.DataFrame(
                [result_row_to_dict(row) for row in result_rows],
                columns=ALL_RESULTS_COLUMNS,
            )
        else:
            all_results = pd.DataFrame(columns=ALL_RESULTS_COLUMNS)

        cur.execute(FETCH_RUNS_SQL)
        run_records = cur.fetchall()
        runs_data: list[dict[str, object]] = []
        for record in run_records:
            (
                run_id,
                started_at,
                finished_at,
                input_rows,
                success_rows,
                failed_rows,
                skipped_rows,
                output_file,
                operator_profile,
                source,
            ) = record
            runs_data.append(
                {
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "input_rows": input_rows,
                    "success_rows": success_rows,
                    "failed_rows": failed_rows,
                    "skipped_rows": skipped_rows,
                    "output_file": output_file or "",
                    "run_id": run_id,
                    "operator_profile": operator_profile or "",
                    "source": source or "",
                }
            )
        if runs_data:
            runs = pd.DataFrame(runs_data)
        else:
            runs = pd.DataFrame(columns=RUNS_COLUMNS)
        return all_results, runs

    if connection is not None:
        with connection.cursor() as cur:
            return _load(cur)

    with connect(for_mirror=False) as conn:
        with conn.cursor() as cur:
            return _load(cur)


def postgres_run_exists(run_id: str, *, connection: Any | None = None) -> bool:
    sql = "SELECT 1 FROM we_registry_runs WHERE run_id = %s LIMIT 1"

    def _check(cur: Any) -> bool:
        cur.execute(sql, (run_id,))
        return cur.fetchone() is not None

    if connection is not None:
        with connection.cursor() as cur:
            return _check(cur)

    with connect(for_mirror=False) as conn:
        with conn.cursor() as cur:
            return _check(cur)
