"""Catalog of ``RULE_*`` contract error codes (CONTRACT_V2 §18).

This module is the **single source** for error code identifiers and their
default severity. Validators / CLI must import codes from here instead of
hard-coding string literals, so adding a new code is one diff (§18 +
this catalog + tests) and renaming is impossible without an audit.

Scope (C1): definitions only. No validation logic is moved or rewritten;
existing validators continue to use their own ad-hoc ``ValidationIssue``
shape until a later commit unifies them.
"""

from __future__ import annotations

from typing import Any, Final
from collections.abc import Mapping

from .validation_issues import ValidationIssue, ValidationSeverity


# Duplicate-index codes (CONTRACT_V2 §7.1, §9, §18)
RULE_DUPLICATE_LIMIT: Final[str] = "RULE_DUPLICATE_LIMIT"
RULE_DUPLICATE_THRESHOLD: Final[str] = "RULE_DUPLICATE_THRESHOLD"
RULE_DUPLICATE_JOB_PARAM: Final[str] = "RULE_DUPLICATE_JOB_PARAM"
RULE_DUPLICATE_ACCESS: Final[str] = "RULE_DUPLICATE_ACCESS"
RULE_DUPLICATE_COMMAND: Final[str] = "RULE_DUPLICATE_COMMAND"
RULE_DUPLICATE_COMMAND_POLICY: Final[str] = "RULE_DUPLICATE_COMMAND_POLICY"
RULE_DUPLICATE_SCHEDULE_ID: Final[str] = "RULE_DUPLICATE_SCHEDULE_ID"

# Schedule schema codes (CONTRACT_V2 §3.7)
RULE_INVALID_SCHEDULE: Final[str] = "RULE_INVALID_SCHEDULE"
RULE_INVALID_SCHEDULE_TYPE: Final[str] = "RULE_INVALID_SCHEDULE_TYPE"

# Referential integrity (CONTRACT_V2 §7.1)
RULE_ORPHAN_PARTNER: Final[str] = "RULE_ORPHAN_PARTNER"
RULE_ORPHAN_GROUP: Final[str] = "RULE_ORPHAN_GROUP"

# Exclusion overlap policy (CONTRACT_V2 §8.4)
RULE_OVERLAPPING_EXCLUSION: Final[str] = "RULE_OVERLAPPING_EXCLUSION"

# Schema / scope (CONTRACT_V2 §3, §5.6)
RULE_UNKNOWN_COLUMN: Final[str] = "RULE_UNKNOWN_COLUMN"
RULE_MISSING_SHEET: Final[str] = "RULE_MISSING_SHEET"
RULE_MISSING_COLUMN: Final[str] = "RULE_MISSING_COLUMN"
RULE_DEPRECATED_COLUMN: Final[str] = "RULE_DEPRECATED_COLUMN"
RULE_INVALID_SCOPE: Final[str] = "RULE_INVALID_SCOPE"

# Versioning / identity (CONTRACT_V2 §2, §4.6, §19)
RULE_INVALID_META_VERSION: Final[str] = "RULE_INVALID_META_VERSION"
RULE_IMMUTABLE_ID_VIOLATION: Final[str] = "RULE_IMMUTABLE_ID_VIOLATION"

# Determinism (CONTRACT_V2 §13)
RULE_NON_DETERMINISTIC_ORDER: Final[str] = "RULE_NON_DETERMINISTIC_ORDER"

# Info-only
RULE_VALIDATION_INFO: Final[str] = "RULE_VALIDATION_INFO"


_E = ValidationSeverity.ERROR
_W = ValidationSeverity.WARN
_I = ValidationSeverity.INFO


DEFAULT_SEVERITY: Final[Mapping[str, ValidationSeverity]] = {
    RULE_DUPLICATE_LIMIT: _E,
    RULE_DUPLICATE_THRESHOLD: _E,
    RULE_DUPLICATE_JOB_PARAM: _E,
    RULE_DUPLICATE_ACCESS: _E,
    RULE_DUPLICATE_COMMAND: _E,
    RULE_DUPLICATE_COMMAND_POLICY: _E,
    RULE_DUPLICATE_SCHEDULE_ID: _E,
    RULE_INVALID_SCHEDULE: _E,
    RULE_INVALID_SCHEDULE_TYPE: _E,
    RULE_ORPHAN_PARTNER: _E,
    RULE_ORPHAN_GROUP: _E,
    RULE_OVERLAPPING_EXCLUSION: _E,
    RULE_UNKNOWN_COLUMN: _E,
    RULE_MISSING_SHEET: _E,
    RULE_MISSING_COLUMN: _E,
    RULE_DEPRECATED_COLUMN: _W,
    RULE_INVALID_SCOPE: _E,
    RULE_INVALID_META_VERSION: _E,
    RULE_IMMUTABLE_ID_VIOLATION: _E,
    RULE_NON_DETERMINISTIC_ORDER: _W,
    RULE_VALIDATION_INFO: _I,
}


ALL_RULE_CODES: Final[tuple[str, ...]] = tuple(DEFAULT_SEVERITY.keys())


def default_severity(code: str) -> ValidationSeverity:
    """Return the catalog default severity for ``code``.

    Raises ``KeyError`` for unknown codes — codes must be declared in this
    module before use (CONTRACT_V2 §18: "extension only with document
    update").
    """

    try:
        return DEFAULT_SEVERITY[code]
    except KeyError as exc:  # pragma: no cover - re-raised with a clearer msg
        raise KeyError(f"Unknown contract error code: {code!r}") from exc


def make_issue(
    code: str,
    message: str,
    *,
    severity: ValidationSeverity | None = None,
    sheet: str | None = None,
    row_index: int | None = None,
    rule_id: str | None = None,
    field: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> ValidationIssue:
    """Build a ``ValidationIssue`` for a known ``RULE_*`` code.

    ``severity`` is taken from the §18 catalog unless an explicit value is
    provided (used by strict / legacy elevation in later stages — Stage 1
    callers should pass the default).
    """

    final_severity = severity if severity is not None else default_severity(code)
    return ValidationIssue(
        code=code,
        severity=final_severity,
        message=message,
        sheet=sheet,
        row_index=row_index,
        rule_id=rule_id,
        field=field,
        details=details,
    )


__all__ = [
    "RULE_DUPLICATE_LIMIT",
    "RULE_DUPLICATE_THRESHOLD",
    "RULE_DUPLICATE_JOB_PARAM",
    "RULE_DUPLICATE_ACCESS",
    "RULE_DUPLICATE_COMMAND",
    "RULE_DUPLICATE_COMMAND_POLICY",
    "RULE_DUPLICATE_SCHEDULE_ID",
    "RULE_INVALID_SCHEDULE",
    "RULE_INVALID_SCHEDULE_TYPE",
    "RULE_ORPHAN_PARTNER",
    "RULE_ORPHAN_GROUP",
    "RULE_OVERLAPPING_EXCLUSION",
    "RULE_UNKNOWN_COLUMN",
    "RULE_MISSING_SHEET",
    "RULE_MISSING_COLUMN",
    "RULE_DEPRECATED_COLUMN",
    "RULE_INVALID_SCOPE",
    "RULE_INVALID_META_VERSION",
    "RULE_IMMUTABLE_ID_VIOLATION",
    "RULE_NON_DETERMINISTIC_ORDER",
    "RULE_VALIDATION_INFO",
    "DEFAULT_SEVERITY",
    "ALL_RULE_CODES",
    "default_severity",
    "make_issue",
]
