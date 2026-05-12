"""Workbook-level schema validation (CONTRACT_V2 §3, §5.1, §7, §18).

Stage 1 / C2 scope:

* This module produces ``ValidationIssue`` records ONLY.
* It never mutates the workbook, never drops columns, never rewrites
  values, never calls into bridge / accessors / analyzers.
* Header trim (§5.1) is applied **for comparison only**; the original
  header is preserved in ``details.original_name`` when it differs from
  the trimmed canonical form.

What it does:

1. Reports missing required sheets (``RULE_MISSING_SHEET``, default
   ``error``) and missing optional but recognized sheets (severity
   overridden to ``warn``).
2. For each recognized sheet present, reports missing required columns
   (``RULE_MISSING_COLUMN``).
3. Reports deprecated columns by exact name or pattern
   (``RULE_DEPRECATED_COLUMN``, default ``warn``).
4. Reports unknown columns (``RULE_UNKNOWN_COLUMN``) with severity
   driven by ``strict``: ``error`` in strict mode, ``warn`` in legacy.

What it does **not** do (out of C2 scope — explicit non-goals):

* No row-level checks (XOR threshold_min/threshold_max, duplicate keys,
  datetime parsing, etc.) — those live in C3 ``validate_snapshot``
  / snapshot-level validators.
* No version range gate (``RULE_INVALID_META_VERSION``) — handled
  separately when `meta.version` is read at snapshot build time.
* No unknown-sheet detection — current §18 has no code for it, so
  unknown sheets are silently skipped (matches legacy CLI behavior).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

from .contract_errors import (
    RULE_DEPRECATED_COLUMN,
    RULE_MISSING_COLUMN,
    RULE_MISSING_SHEET,
    RULE_UNKNOWN_COLUMN,
    make_issue,
)
from .contract_schema import (
    KNOWN_SHEETS,
    OPTIONAL_SHEETS,
    REQUIRED_SHEETS,
    SHEET_SCHEMAS,
    SheetSchema,
    normalize_header,
)
from .validation_issues import ValidationIssue, ValidationSeverity


# Public input alias: caller passes a mapping sheet -> ordered iterable of
# raw header strings (one per column). The validator does not need the
# row data — only headers.
WorkbookHeaders = Mapping[str, Iterable[str]]


def validate_workbook_schema(
    headers: WorkbookHeaders,
    *,
    strict: bool = False,
) -> list[ValidationIssue]:
    """Validate workbook schema against CONTRACT_V2 §3.

    Parameters
    ----------
    headers:
        Mapping of ``sheet_name`` -> iterable of raw header strings.
        Order of sheets and order of columns in the iterable do not
        affect results — issues are emitted in a deterministic order
        (see ``_sorted_columns`` / outer sheet-name sort).
    strict:
        If True, ``RULE_UNKNOWN_COLUMN`` is emitted with the catalog
        default ``error`` severity. Otherwise it is downgraded to
        ``warn`` (CONTRACT_V2 §11.4, §18 "Legacy behavior" column).

    Returns
    -------
    list[ValidationIssue]
        Deterministic list of structured issues. The list is empty when
        every recognized sheet has all required columns and no unknown
        or deprecated columns.
    """

    issues: list[ValidationIssue] = []

    issues.extend(_validate_required_sheets(headers))
    issues.extend(_validate_optional_sheets(headers))

    for sheet in _sorted_sheets(headers):
        schema = SHEET_SCHEMAS.get(sheet)
        if schema is None:
            # Sheet not in CONTRACT_V2 §3 — C2 skips silently; an
            # explicit RULE_UNKNOWN_SHEET code would require a contract
            # update. Out of scope.
            continue
        issues.extend(_validate_sheet_columns(schema, headers[sheet], strict=strict))

    return issues


# ---------------------------------------------------------------------------
# Sheet-level checks
# ---------------------------------------------------------------------------


def _validate_required_sheets(headers: WorkbookHeaders) -> list[ValidationIssue]:
    present = set(headers.keys())
    issues: list[ValidationIssue] = []
    for sheet in sorted(REQUIRED_SHEETS):
        if sheet not in present:
            issues.append(
                make_issue(
                    RULE_MISSING_SHEET,
                    f"Required sheet '{sheet}' is missing",
                    sheet=sheet,
                )
            )
    return issues


def _validate_optional_sheets(headers: WorkbookHeaders) -> list[ValidationIssue]:
    """Emit WARN-severity issues for recognized but absent optional sheets.

    A sheet listed in CONTRACT_V2 §3 but not present in the workbook is
    not a hard error (operators may omit hourly / wallet sheets in a
    minimal config), but the workbook is incomplete relative to the
    contract — surface it as ``warn`` so it is not silent.
    """

    present = set(headers.keys())
    issues: list[ValidationIssue] = []
    for sheet in sorted(OPTIONAL_SHEETS):
        if sheet not in present:
            issues.append(
                make_issue(
                    RULE_MISSING_SHEET,
                    f"Optional sheet '{sheet}' from CONTRACT §3 is missing",
                    severity=ValidationSeverity.WARN,
                    sheet=sheet,
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Column-level checks
# ---------------------------------------------------------------------------


def _validate_sheet_columns(
    schema: SheetSchema,
    raw_columns: Iterable[str],
    *,
    strict: bool,
) -> list[ValidationIssue]:
    """Apply required / optional / deprecated / unknown classification."""

    # Build a trimmed -> originals map so we can report the raw header
    # name in details when trim changed it.
    trimmed_to_originals: dict[str, list[str]] = {}
    for raw in raw_columns:
        trimmed = normalize_header(raw)
        trimmed_to_originals.setdefault(trimmed, []).append(str(raw))

    issues: list[ValidationIssue] = []

    # 1) Missing required columns (deterministic order).
    for required in sorted(schema.required_columns):
        if required not in trimmed_to_originals:
            issues.append(
                make_issue(
                    RULE_MISSING_COLUMN,
                    f"Required column '{required}' missing on sheet '{schema.sheet}'",
                    sheet=schema.sheet,
                    field=required,
                )
            )

    # 2) Classify each present column. Iterate in trimmed-name sort order
    # for stable output.
    for trimmed in sorted(trimmed_to_originals.keys()):
        originals = trimmed_to_originals[trimmed]
        if (
            trimmed in schema.required_columns
            or trimmed in schema.optional_columns
        ):
            continue

        if schema.is_deprecated_column(trimmed):
            issues.append(
                _make_deprecated_issue(schema.sheet, trimmed, originals)
            )
            continue

        # Truly unknown — apply strict/legacy policy.
        severity = (
            ValidationSeverity.ERROR if strict else ValidationSeverity.WARN
        )
        issues.append(
            _make_unknown_issue(schema.sheet, trimmed, originals, severity)
        )

    return issues


def _make_deprecated_issue(
    sheet: str, trimmed: str, originals: list[str]
) -> ValidationIssue:
    details: dict[str, object] = {"original_names": list(originals)}
    return make_issue(
        RULE_DEPRECATED_COLUMN,
        f"Deprecated column '{trimmed}' on sheet '{sheet}'",
        sheet=sheet,
        field=trimmed,
        details=details,
    )


def _make_unknown_issue(
    sheet: str,
    trimmed: str,
    originals: list[str],
    severity: ValidationSeverity,
) -> ValidationIssue:
    details: dict[str, object] = {"original_names": list(originals)}
    return make_issue(
        RULE_UNKNOWN_COLUMN,
        f"Unknown column '{trimmed}' on sheet '{sheet}'",
        severity=severity,
        sheet=sheet,
        field=trimmed,
        details=details,
    )


def _sorted_sheets(headers: WorkbookHeaders) -> list[str]:
    """Return present sheets in a deterministic order.

    Known sheets first (by CONTRACT_V2 §3 declared order in
    ``SHEET_SCHEMAS``), then any other sheet by name. The header iteration
    in ``validate_workbook_schema`` follows this order so callers can
    assert stable issue ordering in tests.
    """

    declared_order: Final[tuple[str, ...]] = tuple(SHEET_SCHEMAS.keys())
    known_present = [s for s in declared_order if s in headers]
    extras = sorted(set(headers.keys()) - set(declared_order))
    return [*known_present, *extras]


# ---------------------------------------------------------------------------
# Workbook header reader (read-only)
# ---------------------------------------------------------------------------


def read_workbook_headers(path: str | Path) -> dict[str, list[str]]:
    """Read header rows from an Excel workbook without loading row data.

    Uses ``pandas.read_excel(..., nrows=0)`` per sheet so that values are
    never materialized in memory. The workbook file is opened read-only;
    nothing is written back. The caller is responsible for the path
    existing and being readable.

    Returns a deterministic mapping (sheet order = workbook order).
    """

    # Lazy import keeps the module importable in environments where
    # pandas/openpyxl are not installed (e.g. CONTRACT validation tests
    # that pass in-memory data only).
    import pandas as pd

    xl = pd.ExcelFile(str(path), engine="openpyxl")
    result: dict[str, list[str]] = {}
    for sheet_name in xl.sheet_names:
        empty = pd.read_excel(xl, sheet_name=sheet_name, nrows=0)
        result[sheet_name] = [str(c) for c in empty.columns]
    return result


__all__ = [
    "WorkbookHeaders",
    "validate_workbook_schema",
    "read_workbook_headers",
]
