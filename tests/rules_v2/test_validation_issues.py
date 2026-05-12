"""Unit tests for CONTRACT_V2 §18 validation model (C1 scope).

Covered:

* issue creation (positional + keyword, defaults, immutability);
* default severity mapping for every ``RULE_*`` code in the catalog;
* counting by severity (``count_by_severity``);
* blocking detection (``is_blocking``, ``has_blocking_errors``);
* ``make_issue`` factory (default severity, explicit override, unknown code).
"""

from __future__ import annotations

import dataclasses

import pytest

from core.rules_v2 import contract_errors
from core.rules_v2.contract_errors import (
    ALL_RULE_CODES,
    DEFAULT_SEVERITY,
    RULE_DUPLICATE_LIMIT,
    RULE_NON_DETERMINISTIC_ORDER,
    RULE_VALIDATION_INFO,
    default_severity,
    make_issue,
)
from core.rules_v2.validation_issues import (
    ValidationIssue,
    ValidationSeverity,
    count_by_severity,
    has_blocking_errors,
    is_blocking,
)


# ---------------------------------------------------------------------------
# ValidationSeverity
# ---------------------------------------------------------------------------


def test_validation_severity_values():
    assert ValidationSeverity.ERROR.value == "error"
    assert ValidationSeverity.WARN.value == "warn"
    assert ValidationSeverity.INFO.value == "info"


def test_validation_severity_str_is_value():
    assert str(ValidationSeverity.ERROR) == "error"
    assert f"{ValidationSeverity.WARN}" == "warn"


def test_validation_severity_iterates_three_levels():
    assert list(ValidationSeverity) == [
        ValidationSeverity.ERROR,
        ValidationSeverity.WARN,
        ValidationSeverity.INFO,
    ]


# ---------------------------------------------------------------------------
# ValidationIssue creation
# ---------------------------------------------------------------------------


def test_issue_minimal_fields():
    issue = ValidationIssue(
        code=RULE_DUPLICATE_LIMIT,
        severity=ValidationSeverity.ERROR,
        message="duplicate limit key",
    )

    assert issue.code == RULE_DUPLICATE_LIMIT
    assert issue.severity == ValidationSeverity.ERROR
    assert issue.message == "duplicate limit key"
    assert issue.sheet is None
    assert issue.row_index is None
    assert issue.rule_id is None
    assert issue.field is None
    assert issue.details is None


def test_issue_full_fields():
    issue = ValidationIssue(
        code=RULE_DUPLICATE_LIMIT,
        severity=ValidationSeverity.ERROR,
        message="duplicate limit key",
        sheet="wallet_limits",
        row_index=42,
        rule_id="LIM-00007",
        field="method",
        details={"key": ("wallet", "partner", "p1", "daily_max", "sbp")},
    )

    assert issue.sheet == "wallet_limits"
    assert issue.row_index == 42
    assert issue.rule_id == "LIM-00007"
    assert issue.field == "method"
    assert issue.details == {"key": ("wallet", "partner", "p1", "daily_max", "sbp")}


def test_issue_is_frozen():
    issue = ValidationIssue(
        code=RULE_VALIDATION_INFO,
        severity=ValidationSeverity.INFO,
        message="ok",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        issue.message = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Default severity catalog
# ---------------------------------------------------------------------------


def test_all_rule_codes_have_default_severity():
    assert set(ALL_RULE_CODES) == set(DEFAULT_SEVERITY.keys())
    for code in ALL_RULE_CODES:
        assert isinstance(DEFAULT_SEVERITY[code], ValidationSeverity)


def test_duplicate_codes_default_to_error():
    duplicate_codes = [
        contract_errors.RULE_DUPLICATE_LIMIT,
        contract_errors.RULE_DUPLICATE_THRESHOLD,
        contract_errors.RULE_DUPLICATE_JOB_PARAM,
        contract_errors.RULE_DUPLICATE_ACCESS,
        contract_errors.RULE_DUPLICATE_COMMAND,
        contract_errors.RULE_DUPLICATE_COMMAND_POLICY,
        contract_errors.RULE_DUPLICATE_SCHEDULE_ID,
    ]
    for code in duplicate_codes:
        assert default_severity(code) == ValidationSeverity.ERROR, code


def test_schema_and_referential_codes_default_to_error():
    for code in (
        contract_errors.RULE_INVALID_SCHEDULE,
        contract_errors.RULE_INVALID_SCHEDULE_TYPE,
        contract_errors.RULE_ORPHAN_PARTNER,
        contract_errors.RULE_ORPHAN_GROUP,
        contract_errors.RULE_OVERLAPPING_EXCLUSION,
        contract_errors.RULE_UNKNOWN_COLUMN,
        contract_errors.RULE_MISSING_SHEET,
        contract_errors.RULE_MISSING_COLUMN,
        contract_errors.RULE_INVALID_SCOPE,
        contract_errors.RULE_INVALID_META_VERSION,
        contract_errors.RULE_IMMUTABLE_ID_VIOLATION,
    ):
        assert default_severity(code) == ValidationSeverity.ERROR, code


def test_non_deterministic_order_defaults_to_warn():
    assert default_severity(RULE_NON_DETERMINISTIC_ORDER) == ValidationSeverity.WARN


def test_deprecated_column_defaults_to_warn():
    assert (
        default_severity(contract_errors.RULE_DEPRECATED_COLUMN)
        == ValidationSeverity.WARN
    )


def test_validation_info_defaults_to_info():
    assert default_severity(RULE_VALIDATION_INFO) == ValidationSeverity.INFO


def test_default_severity_unknown_code_raises():
    with pytest.raises(KeyError):
        default_severity("RULE_NOT_IN_CATALOG")


# ---------------------------------------------------------------------------
# make_issue factory
# ---------------------------------------------------------------------------


def test_make_issue_uses_default_severity():
    issue = make_issue(RULE_DUPLICATE_LIMIT, "duplicate limit")

    assert issue.severity == ValidationSeverity.ERROR
    assert issue.code == RULE_DUPLICATE_LIMIT
    assert issue.message == "duplicate limit"


def test_make_issue_explicit_severity_overrides_default():
    # Strict / legacy elevation: e.g. WARN code surfaced as ERROR in strict.
    issue = make_issue(
        RULE_NON_DETERMINISTIC_ORDER,
        "non-deterministic order",
        severity=ValidationSeverity.ERROR,
    )

    assert issue.severity == ValidationSeverity.ERROR


def test_make_issue_passes_through_optional_fields():
    issue = make_issue(
        RULE_DUPLICATE_LIMIT,
        "duplicate limit",
        sheet="wallet_limits",
        row_index=10,
        rule_id="LIM-001",
        field="limit_value",
        details={"existing_row": 7},
    )

    assert issue.sheet == "wallet_limits"
    assert issue.row_index == 10
    assert issue.rule_id == "LIM-001"
    assert issue.field == "limit_value"
    assert issue.details == {"existing_row": 7}


def test_make_issue_unknown_code_raises():
    with pytest.raises(KeyError):
        make_issue("RULE_NOT_IN_CATALOG", "boom")


# ---------------------------------------------------------------------------
# Counting / blocking helpers
# ---------------------------------------------------------------------------


def _make_simple_issue(code: str) -> ValidationIssue:
    return make_issue(code, f"sample message for {code}")


def test_count_by_severity_empty_returns_zero_for_all_levels():
    counts = count_by_severity([])

    assert counts == {
        ValidationSeverity.ERROR: 0,
        ValidationSeverity.WARN: 0,
        ValidationSeverity.INFO: 0,
    }


def test_count_by_severity_mixed():
    issues = [
        _make_simple_issue(RULE_DUPLICATE_LIMIT),
        _make_simple_issue(contract_errors.RULE_DUPLICATE_THRESHOLD),
        _make_simple_issue(RULE_NON_DETERMINISTIC_ORDER),
        _make_simple_issue(RULE_VALIDATION_INFO),
        _make_simple_issue(RULE_VALIDATION_INFO),
    ]

    counts = count_by_severity(issues)

    assert counts[ValidationSeverity.ERROR] == 2
    assert counts[ValidationSeverity.WARN] == 1
    assert counts[ValidationSeverity.INFO] == 2


def test_is_blocking_only_error_blocks():
    err = _make_simple_issue(RULE_DUPLICATE_LIMIT)
    warn = _make_simple_issue(RULE_NON_DETERMINISTIC_ORDER)
    info = _make_simple_issue(RULE_VALIDATION_INFO)

    assert is_blocking(err) is True
    assert is_blocking(warn) is False
    assert is_blocking(info) is False


def test_has_blocking_errors_true_when_any_error():
    issues = [
        _make_simple_issue(RULE_NON_DETERMINISTIC_ORDER),
        _make_simple_issue(RULE_VALIDATION_INFO),
        _make_simple_issue(RULE_DUPLICATE_LIMIT),
    ]

    assert has_blocking_errors(issues) is True


def test_has_blocking_errors_false_without_errors():
    issues = [
        _make_simple_issue(RULE_NON_DETERMINISTIC_ORDER),
        _make_simple_issue(RULE_VALIDATION_INFO),
    ]

    assert has_blocking_errors(issues) is False


def test_has_blocking_errors_false_for_empty():
    assert has_blocking_errors([]) is False
