"""Workbook ``commands`` sheet policy validation (CONTRACT_V2 §3.6 / §7.1 / §18).

Detects enabled rows that collapse to the same ``command_key`` (via
``normalize_key(command)``, matching ``bridge_legacy._build_commands_and_policies``)
but carry different policy semantics (``min_role_key``, ``allow_private``,
``allow_groups``).

Read-only: does not mutate the workbook or call bridge / accessors.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .contract_errors import RULE_DUPLICATE_COMMAND_POLICY, make_issue
from .normalizers import normalize_key
from .validation_issues import ValidationIssue, ValidationSeverity

# Policy tuple aligned with ``CommandPolicy`` fields built in bridge.
PolicyTuple = tuple[str, bool, bool]


@dataclass(frozen=True, slots=True)
class _CommandRowRef:
    excel_row: int
    command_raw: str
    policy: PolicyTuple


def _as_str(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _safe_int(value: Any) -> int | None:
    if pd.isna(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    return int(float(s))


def _is_enabled(value: Any) -> bool:
    """Match ``bridge_legacy._is_enabled`` semantics."""

    if pd.isna(value):
        return False

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        try:
            return float(value) != 0.0
        except (TypeError, ValueError):
            return False

    s = str(value).strip().lower()
    if not s:
        return False

    try:
        return float(s) != 0.0
    except ValueError:
        pass

    return s in {"true", "yes", "y", "да", "on"}


def _policy_tuple_from_row(row: pd.Series) -> PolicyTuple | None:
    """Build policy tuple the same way as ``_build_commands_and_policies``."""

    command_text = _as_str(row.get("command"))
    if not command_text:
        return None

    required_level = _safe_int(row.get("required_level")) or 0
    min_role_key = f"level_{required_level}"
    allow_private = _is_enabled(row.get("allow_private", 1))
    allow_groups = _is_enabled(row.get("allow_groups", 1))
    return (min_role_key, allow_private, allow_groups)


def _excel_row_number(pandas_index: int) -> int:
    """1-based Excel row (row 1 = header)."""

    return int(pandas_index) + 2


def validate_duplicate_command_policies(
    path: str | Path,
    *,
    strict: bool,
) -> list[ValidationIssue]:
    """Emit ``RULE_DUPLICATE_COMMAND_POLICY`` for conflicting enabled command rows.

    Legacy policy (explicit): ``warn`` — publish not blocked (§18).
    Strict policy: ``error`` — blocks publish via C4.
    """

    path = Path(path)
    try:
        df = pd.read_excel(path, sheet_name="commands", engine="openpyxl")
    except ValueError:
        return []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    groups: dict[str, list[_CommandRowRef]] = defaultdict(list)

    has_enabled_col = "enabled" in df.columns

    for idx, row in df.iterrows():
        if has_enabled_col:
            if not _is_enabled(row.get("enabled")):
                continue
        # If ``enabled`` column is absent, bridge defaults to enabled=1.

        policy = _policy_tuple_from_row(row)
        if policy is None:
            continue

        command_raw = _as_str(row.get("command"))
        command_key = normalize_key(command_raw)
        if not command_key:
            continue

        groups[command_key].append(
            _CommandRowRef(
                excel_row=_excel_row_number(int(idx)),
                command_raw=command_raw,
                policy=policy,
            )
        )

    severity = ValidationSeverity.ERROR if strict else ValidationSeverity.WARN
    issues: list[ValidationIssue] = []

    for command_key in sorted(groups.keys()):
        refs = groups[command_key]
        if len(refs) < 2:
            continue

        unique_policies = {ref.policy for ref in refs}
        if len(unique_policies) <= 1:
            continue

        row_bits = sorted(
            f"row={ref.excel_row} command={ref.command_raw!r} min_role={ref.policy[0]}"
            for ref in refs
        )
        message = (
            f"Conflicting command policies for command_key '{command_key}': "
            + "; ".join(row_bits[:4])
        )
        if len(row_bits) > 4:
            message += f"; … +{len(row_bits) - 4} more"

        issues.append(
            make_issue(
                RULE_DUPLICATE_COMMAND_POLICY,
                message,
                severity=severity,
                sheet="commands",
                field="command",
                row_index=refs[0].excel_row,
                details={
                    "command_key": command_key,
                    "row_indices": [ref.excel_row for ref in refs],
                    "commands": [ref.command_raw for ref in refs],
                },
            )
        )

    return issues


__all__ = ["validate_duplicate_command_policies", "PolicyTuple"]
