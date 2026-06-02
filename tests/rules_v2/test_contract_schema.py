"""Tests for the declarative schema catalog (CONTRACT_V2 §3).

The catalog is read-only data, but it must stay synchronized with the
contract — these tests pin a few critical invariants so an accidental
change is caught immediately:

* All 15 sheets named in CONTRACT_V2 §3 are present in ``SHEET_SCHEMAS``.
* ``REQUIRED_SHEETS`` is a subset of ``KNOWN_SHEETS``.
* No column is simultaneously required and deprecated for the same sheet.
* Trim normalization matches §5.1 (strip ASCII / Unicode whitespace).
* Deprecated patterns for ``schedules`` cover the legacy `Unnamed: N` /
  `cron.N` columns mentioned in §3.7.
"""

from __future__ import annotations

from core.rules_v2.contract_schema import (
    KNOWN_SHEETS,
    OPTIONAL_SHEETS,
    REQUIRED_SHEETS,
    SHEET_SCHEMAS,
    normalize_header,
)


_CONTRACT_V2_SHEETS = {
    "meta",
    "exclude_time",
    "thresholds_partner",
    "wallet_limits",
    "access",
    "commands",
    "schedules",
    "job_params",
    "ui_layout",
    "hourly_payins",
    "hourly_payout_methods",
    "hourly_payouts",
    "partner_groups",
    "payout_info_rules",
    "payout_ignore_phrases",
}


def test_all_contract_sheets_have_schemas():
    assert set(SHEET_SCHEMAS.keys()) == _CONTRACT_V2_SHEETS
    assert KNOWN_SHEETS == frozenset(_CONTRACT_V2_SHEETS)


def test_required_sheets_subset_of_known():
    assert REQUIRED_SHEETS.issubset(KNOWN_SHEETS)


def test_optional_sheets_complement_required():
    assert OPTIONAL_SHEETS == KNOWN_SHEETS - REQUIRED_SHEETS
    assert REQUIRED_SHEETS.isdisjoint(OPTIONAL_SHEETS)


def test_required_columns_disjoint_from_deprecated():
    for sheet, schema in SHEET_SCHEMAS.items():
        overlap = schema.required_columns & schema.deprecated_columns
        assert not overlap, f"sheet {sheet!r} has columns both required and deprecated: {overlap}"


def test_required_and_optional_disjoint():
    for sheet, schema in SHEET_SCHEMAS.items():
        overlap = schema.required_columns & schema.optional_columns
        assert not overlap, f"sheet {sheet!r} has overlapping required/optional: {overlap}"


def test_normalize_header_trims_both_sides():
    assert normalize_header("  job  ") == "job"
    assert normalize_header("\tvalue\n") == "value"
    assert normalize_header("scope ") == "scope"
    assert normalize_header(" key") == "key"


def test_normalize_header_handles_empty_and_none_like_inputs():
    assert normalize_header("") == ""
    assert normalize_header("   ") == ""


def test_schedules_deprecated_patterns_match_legacy_columns():
    schema = SHEET_SCHEMAS["schedules"]
    assert schema.is_deprecated_column("Unnamed: 9")
    assert schema.is_deprecated_column("Unnamed: 10")
    assert schema.is_deprecated_column("cron.1")
    assert schema.is_deprecated_column("cron.2")
    # Sanity: a real column is not deprecated.
    assert not schema.is_deprecated_column("cron")
    assert not schema.is_deprecated_column("job_type")


def test_schedules_required_columns_match_contract_3_7():
    schema = SHEET_SCHEMAS["schedules"]
    assert schema.required_columns == frozenset(
        {
            "id",
            "enabled",
            "job_type",
            "schedule_type",
            "every_seconds",
            "cron",
            "jitter_sec",
            "max_runtime_sec",
            "coalesce",
        }
    )


def test_job_params_canonical_columns_are_trimmed():
    schema = SHEET_SCHEMAS["job_params"]
    # Real files carry trailing spaces; the *contract* names are trimmed.
    assert "job" in schema.required_columns
    assert "scope" in schema.required_columns
    assert "key" in schema.required_columns
    assert "value_type" in schema.required_columns
    assert "value" in schema.required_columns
    # And we do NOT keep the un-trimmed variants in the catalog.
    assert "job " not in schema.required_columns
    assert "scope " not in schema.required_columns


def test_required_sheets_match_minimum_runtime():
    # Conservative baseline: same as the legacy CLI plus `meta` for
    # version detection. Pin to catch accidental widening or narrowing.
    assert REQUIRED_SHEETS == frozenset({"meta", "exclude_time", "access", "commands"})
