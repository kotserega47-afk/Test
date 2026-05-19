"""Per-sheet immutable axis extraction from Excel rows (C3.5 PR-2).

Uses the same normalization rules as ``bridge_legacy`` (via ``normalizers``)
but operates on **one Excel row** without snapshot build or analyzer fan-out.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.rules_v2.normalizers import build_partner_key, normalize_key

# Fixed sheet order for deterministic manifests (CONTRACT_V2 §4.6.2).
IDENTITY_SHEET_ORDER: tuple[str, ...] = (
    "wallet_limits",
    "thresholds_partner",
    "job_params",
    "schedules",
    "exclude_time",
    "partner_groups",
    "ui_layout",
)


def normalize_row_id(value: Any) -> str:
    """Return stripped Excel ``id``; empty string if missing/blank."""

    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _as_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _parse_analyzers_cell(value: Any) -> list[str]:
    s = _as_str(value)
    if not s:
        return []
    return [normalize_key(x) for x in s.split(",") if _as_str(x)]


def _job_keys_from_analyzers(value: Any) -> list[str]:
    keys = _parse_analyzers_cell(value)
    return sorted(keys) if keys else ["wallet"]


def _scope_key(scope: str, scope_value: str) -> str:
    scope_n = normalize_key(scope)
    scope_value_s = _as_str(scope_value)
    if scope_n == "global":
        return "*"
    if scope_n == "group":
        return normalize_key(scope_value_s)
    if scope_n == "partner":
        return build_partner_key(scope_value_s)
    return normalize_key(scope_value_s)


def _section_key(report_key: str, section_name: str) -> str:
    return f"{normalize_key(report_key)}.{normalize_key(section_name)}"


def immutable_axes_wallet_limits(row: pd.Series) -> dict[str, Any]:
    scope_type = normalize_key(row.get("scope"))
    method_raw = _as_str(row.get("method"))
    return {
        "job_keys": _job_keys_from_analyzers(row.get("analyzers")),
        "scope_type": scope_type,
        "scope_key": _scope_key(scope_type, _as_str(row.get("scope_value"))),
        "metric_key": normalize_key(row.get("limit_type")),
        "method_key": normalize_key(method_raw) if method_raw else None,
    }


def immutable_axes_thresholds_partner(row: pd.Series) -> dict[str, Any]:
    return {
        "job_key": normalize_key(row.get("analyzer")),
        "partner_key": build_partner_key(row.get("partner")),
        "metric_key": normalize_key(row.get("metric")),
    }


def immutable_axes_job_params(row: pd.Series) -> dict[str, Any]:
    scope_type = normalize_key(row.get("scope")) or "global"
    raw_scope_value = _as_str(row.get("scope_value"))
    if scope_type == "global":
        scope_key = "*"
    else:
        scope_key = normalize_key(raw_scope_value) if raw_scope_value else "*"
    return {
        "job_key": normalize_key(row.get("job")),
        "scope_type": scope_type,
        "scope_key": scope_key,
        "param_key": normalize_key(row.get("key")),
    }


def immutable_axes_schedules(row: pd.Series) -> dict[str, Any]:
    return {
        "job_key": normalize_key(row.get("job_type")),
        "schedule_type": normalize_key(row.get("schedule_type")),
    }


def immutable_axes_exclude_time(row: pd.Series) -> dict[str, Any]:
    return {
        "job_keys": _job_keys_from_analyzers(row.get("analyzers")),
        "partner_key": build_partner_key(row.get("partner")),
    }


def immutable_axes_partner_groups(row: pd.Series) -> dict[str, Any]:
    return {
        "job_keys": _job_keys_from_analyzers(row.get("analyzers")),
        "group_key": normalize_key(row.get("group_name")),
        "partner_key": build_partner_key(row.get("partner")),
    }


def immutable_axes_ui_layout(row: pd.Series) -> dict[str, Any]:
    report_key = normalize_key(row.get("view"))
    section_name = normalize_key(row.get("section"))
    return {
        "report_key": report_key,
        "section_key": _section_key(report_key, section_name),
        "line_key": _as_str(row.get("key")),
    }


_ROW_EXTRACTORS: dict[str, Any] = {
    "wallet_limits": immutable_axes_wallet_limits,
    "thresholds_partner": immutable_axes_thresholds_partner,
    "job_params": immutable_axes_job_params,
    "schedules": immutable_axes_schedules,
    "exclude_time": immutable_axes_exclude_time,
    "partner_groups": immutable_axes_partner_groups,
    "ui_layout": immutable_axes_ui_layout,
}


def immutable_axes_for_row(sheet: str, row: pd.Series) -> dict[str, Any]:
    """Return canonical immutable axes dict for one Excel row."""

    fn = _ROW_EXTRACTORS.get(sheet)
    if fn is None:
        raise ValueError(f"Unsupported identity sheet: {sheet!r}")
    return fn(row)


__all__ = [
    "IDENTITY_SHEET_ORDER",
    "immutable_axes_for_row",
    "immutable_axes_exclude_time",
    "immutable_axes_job_params",
    "immutable_axes_partner_groups",
    "immutable_axes_schedules",
    "immutable_axes_thresholds_partner",
    "immutable_axes_ui_layout",
    "immutable_axes_wallet_limits",
    "normalize_row_id",
]
