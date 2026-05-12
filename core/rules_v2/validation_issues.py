"""Validation issue model for CONTRACT_V2 §18.

Stage 1 / C1 scope: define a structured, immutable record that future
validators (CLI, library, snapshot-level) will emit, plus a few pure helpers
used by the CLI / formatters to count and gate on blocking issues.

This module deliberately does NOT change any current validator behavior:
the existing ``core/rules_v2/validators.py`` keeps its own ``ValidationIssue``
shape until a later commit performs the unification.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ValidationSeverity(str, Enum):
    """Severity levels per CONTRACT_V2 §18.

    Stored as plain strings so issues can be serialized to JSONL / log lines
    without custom encoders. ``error`` is the only blocking level in strict
    mode (§7.1, §18 invariant).
    """

    ERROR = "error"
    WARN = "warn"
    INFO = "info"

    def __str__(self) -> str:  # keep dataclass repr / formatters readable
        return self.value


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One structured validation finding.

    Fields follow CONTRACT_V2 §18 (Structured validation and error model):

    - ``code`` — ``RULE_*`` identifier from the §18 catalog
      (see ``core.rules_v2.contract_errors``).
    - ``severity`` — final severity after policy (strict / legacy can be
      applied by the caller; ``default_severity`` from
      ``contract_errors`` is the catalog default).
    - ``message`` — human-readable explanation, in the validator's language.
    - ``sheet`` — source sheet in ``rules.xlsx`` if applicable.
    - ``row_index`` — 1-based Excel row index (header row excluded) when
      the issue is bound to a specific row; ``None`` for workbook-level
      issues (unknown sheet, meta.version, etc.).
    - ``rule_id`` — value of the ``id`` column (e.g. ``LIM-00001``) when
      the row has one; used in messages and for ops references (§4.1).
    - ``field`` — name of the offending column when applicable
      (e.g. ``method`` for limits, ``cron`` for schedules).
    - ``details`` — optional structured payload (duplicate key parts,
      expected vs actual, etc.). Implementations should keep this
      JSON-serializable.
    """

    code: str
    severity: ValidationSeverity
    message: str
    sheet: str | None = None
    row_index: int | None = None
    rule_id: str | None = None
    field: str | None = None
    details: Mapping[str, Any] | None = None


def is_blocking(issue: ValidationIssue) -> bool:
    """Return ``True`` if the issue blocks snapshot publication.

    Per CONTRACT_V2 §18 invariant: a single ``error`` in strict mode
    prevents publishing a new active snapshot. Strict / legacy elevation
    is the caller's responsibility — by the time the issue is inspected
    here its ``severity`` is already final.
    """

    return issue.severity == ValidationSeverity.ERROR


def count_by_severity(
    issues: Iterable[ValidationIssue],
) -> dict[ValidationSeverity, int]:
    """Count issues per severity. Always returns all severities (zero-padded)."""

    counter: Counter[ValidationSeverity] = Counter()
    for issue in issues:
        counter[issue.severity] += 1
    return {severity: counter.get(severity, 0) for severity in ValidationSeverity}


def has_blocking_errors(issues: Iterable[ValidationIssue]) -> bool:
    """Return ``True`` if any issue in ``issues`` is blocking."""

    return any(is_blocking(issue) for issue in issues)


__all__ = [
    "ValidationSeverity",
    "ValidationIssue",
    "is_blocking",
    "count_by_severity",
    "has_blocking_errors",
]
