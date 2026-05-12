"""Unit + contract tests for ``validation_workbook`` (CONTRACT_V2 §3 / §18).

Covers:

* required sheets validation (RULE_MISSING_SHEET, error severity);
* optional sheets validation (RULE_MISSING_SHEET, warn severity);
* required columns validation (RULE_MISSING_COLUMN, error);
* deprecated columns (RULE_DEPRECATED_COLUMN, warn);
* unknown columns policy (RULE_UNKNOWN_COLUMN, error in strict, warn in legacy);
* header trim normalization for comparison only;
* deterministic ordering (no row-order coupling);
* contract test on a small generated xlsx (real I/O path via openpyxl).

The validator must never mutate input; this is asserted explicitly.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from core.rules_v2.contract_errors import (
    RULE_DEPRECATED_COLUMN,
    RULE_MISSING_COLUMN,
    RULE_MISSING_SHEET,
    RULE_UNKNOWN_COLUMN,
)
from core.rules_v2.contract_schema import SHEET_SCHEMAS
from core.rules_v2.validation_issues import (
    ValidationIssue,
    ValidationSeverity,
    has_blocking_errors,
)
from core.rules_v2.validation_workbook import (
    read_workbook_headers,
    validate_workbook_schema,
)


# ---------------------------------------------------------------------------
# Fixtures: a synthetic "good" workbook header set covering all 13 sheets.
# ---------------------------------------------------------------------------


def _full_valid_headers() -> dict[str, list[str]]:
    """Return a header mapping where every sheet has its full required set."""

    return {
        sheet: sorted(schema.required_columns | schema.optional_columns)
        for sheet, schema in SHEET_SCHEMAS.items()
    }


def _codes(issues: list[ValidationIssue]) -> list[str]:
    return [i.code for i in issues]


def _by_code(issues: list[ValidationIssue], code: str) -> list[ValidationIssue]:
    return [i for i in issues if i.code == code]


# ---------------------------------------------------------------------------
# Sanity: full valid workbook passes
# ---------------------------------------------------------------------------


def test_full_valid_workbook_has_no_issues():
    issues = validate_workbook_schema(_full_valid_headers())

    assert issues == []
    assert not has_blocking_errors(issues)


def test_full_valid_workbook_strict_also_clean():
    issues = validate_workbook_schema(_full_valid_headers(), strict=True)

    assert issues == []


# ---------------------------------------------------------------------------
# Required sheets
# ---------------------------------------------------------------------------


def test_missing_required_sheet_emits_error():
    headers = _full_valid_headers()
    del headers["access"]

    issues = validate_workbook_schema(headers)

    missing = _by_code(issues, RULE_MISSING_SHEET)
    error_missing = [i for i in missing if i.severity == ValidationSeverity.ERROR]
    assert len(error_missing) == 1
    assert error_missing[0].sheet == "access"
    assert has_blocking_errors(issues)


def test_missing_multiple_required_sheets_each_reported():
    headers = _full_valid_headers()
    del headers["meta"]
    del headers["commands"]

    issues = validate_workbook_schema(headers)

    error_missing = {
        i.sheet
        for i in issues
        if i.code == RULE_MISSING_SHEET and i.severity == ValidationSeverity.ERROR
    }
    assert error_missing == {"meta", "commands"}


def test_missing_optional_sheet_emits_warn_not_error():
    headers = _full_valid_headers()
    del headers["thresholds_partner"]

    issues = validate_workbook_schema(headers)

    relevant = [
        i
        for i in issues
        if i.code == RULE_MISSING_SHEET and i.sheet == "thresholds_partner"
    ]
    assert len(relevant) == 1
    assert relevant[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(issues)


# ---------------------------------------------------------------------------
# Required / optional / unknown columns
# ---------------------------------------------------------------------------


def test_missing_required_column_emits_error():
    headers = _full_valid_headers()
    headers["wallet_limits"] = [
        c for c in headers["wallet_limits"] if c != "limit_value"
    ]

    issues = validate_workbook_schema(headers)

    missing_cols = _by_code(issues, RULE_MISSING_COLUMN)
    assert len(missing_cols) == 1
    issue = missing_cols[0]
    assert issue.sheet == "wallet_limits"
    assert issue.field == "limit_value"
    assert issue.severity == ValidationSeverity.ERROR


def test_optional_column_absence_is_silent():
    headers = _full_valid_headers()
    # Drop the optional `method` column from wallet_limits.
    headers["wallet_limits"] = [
        c for c in headers["wallet_limits"] if c != "method"
    ]

    issues = validate_workbook_schema(headers)

    # No missing-column / unknown-column issues for wallet_limits.
    for issue in issues:
        if issue.sheet == "wallet_limits":
            assert issue.code not in {RULE_MISSING_COLUMN, RULE_UNKNOWN_COLUMN}


def test_unknown_column_strict_is_error():
    headers = _full_valid_headers()
    headers["wallet_limits"].append("extra_diagnostic_field")

    issues = validate_workbook_schema(headers, strict=True)

    unknowns = _by_code(issues, RULE_UNKNOWN_COLUMN)
    assert len(unknowns) == 1
    issue = unknowns[0]
    assert issue.sheet == "wallet_limits"
    assert issue.field == "extra_diagnostic_field"
    assert issue.severity == ValidationSeverity.ERROR


def test_unknown_column_legacy_is_warn():
    headers = _full_valid_headers()
    headers["wallet_limits"].append("extra_diagnostic_field")

    issues = validate_workbook_schema(headers, strict=False)

    unknowns = _by_code(issues, RULE_UNKNOWN_COLUMN)
    assert len(unknowns) == 1
    assert unknowns[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(issues)


# ---------------------------------------------------------------------------
# Deprecated columns
# ---------------------------------------------------------------------------


def test_schedules_unnamed_column_is_deprecated_not_unknown():
    headers = _full_valid_headers()
    headers["schedules"].extend(["Unnamed: 9", "cron.1"])

    issues = validate_workbook_schema(headers, strict=True)

    deprecated = _by_code(issues, RULE_DEPRECATED_COLUMN)
    unknown = _by_code(issues, RULE_UNKNOWN_COLUMN)

    deprecated_fields = {i.field for i in deprecated if i.sheet == "schedules"}
    assert deprecated_fields == {"Unnamed: 9", "cron.1"}
    # And exactly zero unknown-column issues on schedules: pattern hit
    # must classify them as deprecated, not unknown.
    assert [i for i in unknown if i.sheet == "schedules"] == []

    # Severity for deprecated stays WARN even in strict (matches §18).
    for issue in deprecated:
        assert issue.severity == ValidationSeverity.WARN


# ---------------------------------------------------------------------------
# Header trim (CONTRACT_V2 §5.1)
# ---------------------------------------------------------------------------


def test_trim_is_applied_for_comparison_only():
    headers = _full_valid_headers()
    # Replace `job_params` headers with the real-world variant that
    # carries trailing whitespace.
    headers["job_params"] = [
        "id",
        "enabled",
        "job ",
        "scope ",
        "scope_value",
        "key ",
        "value_type ",
        "value ",
        "comment ",
        "updated_at",
        "updated_by",
    ]

    issues = validate_workbook_schema(headers, strict=True)

    for issue in issues:
        assert issue.sheet != "job_params", f"unexpected job_params issue: {issue}"


def test_trim_does_not_mutate_input_headers():
    headers = _full_valid_headers()
    headers["job_params"] = [
        "id",
        "enabled",
        "job ",
        "scope ",
        "scope_value",
        "key ",
        "value_type ",
        "value ",
    ]
    snapshot = copy.deepcopy(headers)

    validate_workbook_schema(headers, strict=True)

    assert headers == snapshot, "validator must not mutate input headers"


def test_unknown_column_preserves_original_name_in_details():
    headers = _full_valid_headers()
    headers["wallet_limits"].append("  extra_with_spaces  ")

    issues = validate_workbook_schema(headers, strict=False)

    unknowns = _by_code(issues, RULE_UNKNOWN_COLUMN)
    assert len(unknowns) == 1
    issue = unknowns[0]
    assert issue.field == "extra_with_spaces"  # trimmed canonical name
    assert issue.details is not None
    assert "  extra_with_spaces  " in issue.details["original_names"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_column_order_independent_of_input_order():
    base = _full_valid_headers()
    base["wallet_limits"].extend(["unknown_a", "unknown_b"])

    a = validate_workbook_schema(base)
    reversed_headers = {k: list(reversed(v)) for k, v in base.items()}
    b = validate_workbook_schema(reversed_headers)

    assert [(i.code, i.sheet, i.field) for i in a] == [
        (i.code, i.sheet, i.field) for i in b
    ]


def test_sheet_insertion_order_does_not_affect_results():
    a_headers = _full_valid_headers()
    b_headers = dict(reversed(list(a_headers.items())))

    a = validate_workbook_schema(a_headers)
    b = validate_workbook_schema(b_headers)

    assert a == b
    assert a == []


# ---------------------------------------------------------------------------
# Unknown / non-contract sheets are skipped silently (no §18 code today)
# ---------------------------------------------------------------------------


def test_extra_unknown_sheet_is_silently_ignored():
    headers = _full_valid_headers()
    headers["future_internal_sheet"] = ["something", "else"]

    issues = validate_workbook_schema(headers, strict=True)

    sheets_with_issues = {i.sheet for i in issues}
    assert "future_internal_sheet" not in sheets_with_issues
    # Adding an unknown sheet must not produce errors anywhere.
    assert issues == []


# ---------------------------------------------------------------------------
# Contract test: real xlsx I/O via openpyxl in a temp directory.
# ---------------------------------------------------------------------------


def _write_xlsx(path: Path, sheets: dict[str, list[str]]) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    # The default sheet gets renamed for the first entry; remove and
    # recreate per the order we want.
    default = wb.active
    wb.remove(default)
    for sheet_name, headers in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        ws.append(headers)
    wb.save(path)


def test_read_workbook_headers_round_trip(tmp_path: Path):
    xlsx = tmp_path / "rules_small.xlsx"
    sheets = {
        "meta": ["key", "value"],
        "access": ["chat_id", "user_id", "level", "enabled"],
        "commands": [
            "command",
            "required_level",
            "allow_private",
            "allow_groups",
            "enabled",
        ],
        "exclude_time": [
            "id",
            "enabled",
            "analyzers",
            "partner",
            "start_dt",
            "end_dt",
            "reason",
        ],
    }
    _write_xlsx(xlsx, sheets)

    headers = read_workbook_headers(xlsx)

    assert set(headers.keys()) == set(sheets.keys())
    for sheet, expected in sheets.items():
        assert headers[sheet] == expected


def test_minimal_required_workbook_validates_clean(tmp_path: Path):
    xlsx = tmp_path / "rules_min.xlsx"
    sheets = {
        "meta": ["key", "value"],
        "access": ["chat_id", "user_id", "level", "enabled"],
        "commands": [
            "command",
            "required_level",
            "allow_private",
            "allow_groups",
            "enabled",
        ],
        "exclude_time": [
            "id",
            "enabled",
            "analyzers",
            "partner",
            "start_dt",
            "end_dt",
            "reason",
        ],
    }
    _write_xlsx(xlsx, sheets)
    headers = read_workbook_headers(xlsx)

    issues = validate_workbook_schema(headers)

    blocking = [i for i in issues if i.severity == ValidationSeverity.ERROR]
    # No blocking issues: required sheets and required columns present.
    assert blocking == [], blocking
    # Optional sheets are missing -> we expect 9 warn-level
    # RULE_MISSING_SHEET issues (KNOWN_SHEETS \ REQUIRED_SHEETS).
    optional_warns = [
        i
        for i in issues
        if i.code == RULE_MISSING_SHEET and i.severity == ValidationSeverity.WARN
    ]
    assert len(optional_warns) == 9


def test_xlsx_with_deprecated_and_unknown_columns(tmp_path: Path):
    xlsx = tmp_path / "rules_dirty.xlsx"
    sheets = {
        "meta": ["key", "value"],
        "access": ["chat_id", "user_id", "level", "enabled", "note"],
        "commands": [
            "command",
            "required_level",
            "allow_private",
            "allow_groups",
            "enabled",
        ],
        "exclude_time": [
            "id",
            "enabled",
            "analyzers",
            "partner",
            "start_dt",
            "end_dt",
            "reason",
        ],
        "schedules": [
            "id",
            "enabled",
            "job_type",
            "schedule_type",
            "every_seconds",
            "cron",
            "jitter_sec",
            "max_runtime_sec",
            "coalesce",
            "Unnamed: 9",
            "cron.1",
        ],
        "wallet_limits": [
            "id",
            "enabled",
            "analyzers",
            "scope",
            "scope_value",
            "limit_type",
            "limit_value",
            "reason",
            "extra_diag",
        ],
    }
    _write_xlsx(xlsx, sheets)
    headers = read_workbook_headers(xlsx)

    legacy = validate_workbook_schema(headers, strict=False)
    strict = validate_workbook_schema(headers, strict=True)

    legacy_codes = sorted(
        (i.code, i.severity.value, i.sheet, i.field) for i in legacy
    )
    strict_codes = sorted(
        (i.code, i.severity.value, i.sheet, i.field) for i in strict
    )

    # Deprecated detection happens in both modes.
    deprecated_fields_legacy = {
        i.field
        for i in legacy
        if i.code == RULE_DEPRECATED_COLUMN and i.sheet == "schedules"
    }
    deprecated_fields_strict = {
        i.field
        for i in strict
        if i.code == RULE_DEPRECATED_COLUMN and i.sheet == "schedules"
    }
    assert deprecated_fields_legacy == {"Unnamed: 9", "cron.1"}
    assert deprecated_fields_strict == {"Unnamed: 9", "cron.1"}

    # `extra_diag` is unknown: strict -> error, legacy -> warn.
    extra_legacy = next(
        i
        for i in legacy
        if i.code == RULE_UNKNOWN_COLUMN
        and i.sheet == "wallet_limits"
        and i.field == "extra_diag"
    )
    extra_strict = next(
        i
        for i in strict
        if i.code == RULE_UNKNOWN_COLUMN
        and i.sheet == "wallet_limits"
        and i.field == "extra_diag"
    )
    assert extra_legacy.severity == ValidationSeverity.WARN
    assert extra_strict.severity == ValidationSeverity.ERROR

    # Sanity: legacy has no blocking errors here; strict does.
    assert not has_blocking_errors(legacy)
    assert has_blocking_errors(strict)

    # Issue tuples should otherwise match modulo severity.
    def _without_severity(t):
        return (t[0], t[2], t[3])

    assert {_without_severity(t) for t in legacy_codes} == {
        _without_severity(t) for t in strict_codes
    }


# ---------------------------------------------------------------------------
# Strict mode does NOT change anything other than RULE_UNKNOWN_COLUMN.
# ---------------------------------------------------------------------------


def test_strict_flag_only_affects_unknown_column_severity():
    headers = _full_valid_headers()
    # Drop a required column, add an unknown column, add a deprecated.
    headers["wallet_limits"] = [
        c for c in headers["wallet_limits"] if c != "limit_value"
    ]
    headers["wallet_limits"].append("unknown_col")
    headers["schedules"].append("Unnamed: 5")

    legacy = validate_workbook_schema(headers, strict=False)
    strict = validate_workbook_schema(headers, strict=True)

    # Severities of missing_column / deprecated_column issues identical.
    def _key(i):
        return (i.code, i.sheet, i.field)

    legacy_by_key = {_key(i): i for i in legacy}
    strict_by_key = {_key(i): i for i in strict}
    assert legacy_by_key.keys() == strict_by_key.keys()
    for k in legacy_by_key:
        code = k[0]
        if code == RULE_UNKNOWN_COLUMN:
            continue
        assert legacy_by_key[k].severity == strict_by_key[k].severity, k


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_empty_workbook_reports_all_required_sheets_missing():
    issues = validate_workbook_schema({})

    error_missing = {
        i.sheet
        for i in issues
        if i.code == RULE_MISSING_SHEET and i.severity == ValidationSeverity.ERROR
    }
    warn_missing = {
        i.sheet
        for i in issues
        if i.code == RULE_MISSING_SHEET and i.severity == ValidationSeverity.WARN
    }
    assert error_missing == {"meta", "exclude_time", "access", "commands"}
    # 13 known sheets - 4 required = 9 warn-level missing sheets.
    assert len(warn_missing) == 9
    assert has_blocking_errors(issues)
