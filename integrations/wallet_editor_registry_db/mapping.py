"""Map Excel/registry DataFrame rows to PostgreSQL mirror models."""

from __future__ import annotations

from typing import Any

import pandas as pd

from integrations.wallet_editor_registry_lifecycle import (
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    RUNS_COLUMNS,
    _cell_str,
    result_row_fingerprint,
)
from integrations.wallet_editor_registry_db.models import RegistryResultRow, RegistryRunRow


def _optional_str(value: object) -> str | None:
    text = _cell_str(value)
    return text or None


def map_all_results_row(
    row: pd.Series | dict[str, Any],
    *,
    run_id: str | None = None,
    source_row_index: int | None = None,
) -> RegistryResultRow:
    """Map one normalized ``all_results`` row to ``RegistryResultRow``."""
    if isinstance(row, dict):
        get = row.get
    else:
        get = row.get

    fingerprint = result_row_fingerprint(row)
    card = _cell_str(get("card", ""))
    partner = _cell_str(get("partner", ""))
    action = _cell_str(get("action", ""))
    status = _cell_str(get("status", ""))

    if not card:
        raise ValueError("all_results row mapping requires non-empty card")
    if not action:
        raise ValueError("all_results row mapping requires action")

    return RegistryResultRow(
        row_fingerprint=fingerprint,
        card=card,
        partner=partner,
        action=action,
        status=status,
        run_id=run_id,
        operation_date=_optional_str(get(OPERATION_DATE_COLUMN, "")),
        disable_at=_optional_str(get(DISABLE_DATE_COLUMN, "")),
        reenable_date=_optional_str(get("Дата включения", "")),
        enable_status=_optional_str(get("Статус включения", "")),
        vklyucheno=_optional_str(get("Включено", "")),
        enable_comment=_optional_str(get("Комментарий включения", "")),
        comment=_optional_str(get("comment", "")),
        hold_mark=_optional_str(get("hold", "")),
        source_row_index=source_row_index,
    )


def map_runs_row(
    row: pd.Series | dict[str, Any],
    *,
    run_id: str,
    operator_profile: str | None = None,
    source: str | None = None,
) -> RegistryRunRow:
    """Map one ``runs`` sheet row to ``RegistryRunRow``."""
    if isinstance(row, dict):
        get = row.get
    else:
        get = row.get

    if not run_id:
        raise ValueError("runs row mapping requires run_id")

    started_at = _cell_str(get("started_at", ""))
    finished_at = _cell_str(get("finished_at", ""))
    if not started_at or not finished_at:
        raise ValueError("runs row mapping requires started_at and finished_at")

    def _int_field(name: str) -> int:
        raw = get(name, 0)
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            return 0
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return 0

    return RegistryRunRow(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        input_rows=_int_field("input_rows"),
        success_rows=_int_field("success_rows"),
        failed_rows=_int_field("failed_rows"),
        skipped_rows=_int_field("skipped_rows"),
        output_file=_optional_str(get("output_file", "")),
        operator_profile=operator_profile,
        source=source,
    )


def runs_row_dict_for_columns(row: RegistryRunRow) -> dict[str, object]:
    """Serialize ``RegistryRunRow`` using ``RUNS_COLUMNS`` keys where applicable."""
    return {
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "input_rows": row.input_rows,
        "success_rows": row.success_rows,
        "failed_rows": row.failed_rows,
        "skipped_rows": row.skipped_rows,
        "output_file": row.output_file or "",
    }


def validate_runs_columns(columns: list[str]) -> bool:
    """Return True when *columns* contains all required RUNS_COLUMNS."""
    return all(col in columns for col in RUNS_COLUMNS)
