"""Declarative workbook schema for ``rules.xlsx`` per CONTRACT_V2 §3.

This module is a **read-only catalog**: it describes which sheets exist,
which columns are required/optional, and which columns are deprecated
(by exact name or by pattern). It does not perform any validation —
that is the job of ``validation_workbook``.

C2 scope rules:

* No mutation of workbook data — schemas are descriptors only.
* No business-value canonicalization — only column header *names*.
* No coupling with bridge / accessors / analyzers — pure data.

Whenever CONTRACT_V2 §3 changes, this file must be updated in the same
commit (see §12.1, Document history).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final


# Header trim normalization used for *comparison only* (CONTRACT_V2 §5.1).
# Validation never writes back trimmed names to the workbook.
def normalize_header(name: str) -> str:
    """Return the column header used for schema comparison.

    Per CONTRACT_V2 §5.1: trim ASCII / Unicode whitespace at both ends.
    The original name (including trailing spaces) is preserved by the
    caller for error reporting in ``details``.
    """

    if name is None:
        return ""
    return str(name).strip()


@dataclass(frozen=True, slots=True)
class SheetSchema:
    """Per-sheet schema descriptor.

    Attributes
    ----------
    sheet:
        Excel sheet name (as it appears in the workbook).
    required_columns:
        Columns that MUST be present (after trim) for the sheet to be
        considered structurally valid.
    optional_columns:
        Columns that are recognized by the contract but absence is OK.
    deprecated_columns:
        Exact column names that are recognized but deprecated.
    deprecated_column_patterns:
        Compiled regex patterns matching deprecated columns (e.g.
        ``Unnamed: 9`` / ``cron.1`` per §3.7). Anchored full-match
        semantics: a column matches if any pattern's ``fullmatch`` hits.
    """

    sheet: str
    required_columns: frozenset[str] = frozenset()
    optional_columns: frozenset[str] = frozenset()
    deprecated_columns: frozenset[str] = frozenset()
    deprecated_column_patterns: tuple[re.Pattern[str], ...] = field(default=())

    def is_deprecated_column(self, trimmed_name: str) -> bool:
        if trimmed_name in self.deprecated_columns:
            return True
        for pattern in self.deprecated_column_patterns:
            if pattern.fullmatch(trimmed_name):
                return True
        return False

    def is_known_column(self, trimmed_name: str) -> bool:
        """Return True if a trimmed column is required, optional, or deprecated."""

        if trimmed_name in self.required_columns:
            return True
        if trimmed_name in self.optional_columns:
            return True
        return self.is_deprecated_column(trimmed_name)


# Compile patterns once.
_UNNAMED_PATTERN: Final[re.Pattern[str]] = re.compile(r"Unnamed:\s*\d+")
_CRON_DUPLICATE_PATTERN: Final[re.Pattern[str]] = re.compile(r"cron\.\d+")


# Per-sheet schemas. Mirror of CONTRACT_V2 §3.1 … §3.13.
SHEET_SCHEMAS: Final[dict[str, SheetSchema]] = {
    # §3.1
    "meta": SheetSchema(
        sheet="meta",
        required_columns=frozenset({"key", "value"}),
    ),
    # §3.2
    "exclude_time": SheetSchema(
        sheet="exclude_time",
        required_columns=frozenset(
            {"id", "enabled", "analyzers", "partner", "start_dt", "end_dt", "reason"}
        ),
        optional_columns=frozenset({"created_by", "created_at"}),
    ),
    # §3.3
    "thresholds_partner": SheetSchema(
        sheet="thresholds_partner",
        required_columns=frozenset(
            {"id", "enabled", "analyzer", "partner", "metric", "reason"}
        ),
        optional_columns=frozenset(
            {"threshold_min", "threshold_max", "min_events", "updated_by", "updated_at"}
        ),
    ),
    # §3.4
    "wallet_limits": SheetSchema(
        sheet="wallet_limits",
        required_columns=frozenset(
            {
                "id",
                "enabled",
                "analyzers",
                "scope",
                "scope_value",
                "limit_type",
                "limit_value",
                "reason",
            }
        ),
        optional_columns=frozenset({"comment", "method", "updated_by", "updated_at"}),
    ),
    # §3.5
    "access": SheetSchema(
        sheet="access",
        required_columns=frozenset({"chat_id", "user_id", "level", "enabled"}),
        optional_columns=frozenset({"note"}),
    ),
    # §3.6
    "commands": SheetSchema(
        sheet="commands",
        required_columns=frozenset(
            {"command", "required_level", "allow_private", "allow_groups", "enabled"}
        ),
        optional_columns=frozenset({"note"}),
    ),
    # §3.7 — deprecated columns include `Unnamed:*` and `cron.\d+` per the
    # spec. Required columns intentionally do not include the duplicates.
    "schedules": SheetSchema(
        sheet="schedules",
        required_columns=frozenset(
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
        ),
        deprecated_column_patterns=(_UNNAMED_PATTERN, _CRON_DUPLICATE_PATTERN),
    ),
    # §3.8 — note that some production files carry trailing whitespace in
    # header names (`'job '`, `'scope '`, …); CONTRACT_V2 §5.1 mandates
    # trim-for-comparison, so the trimmed names below are the canonical
    # contract.
    "job_params": SheetSchema(
        sheet="job_params",
        required_columns=frozenset(
            {"id", "enabled", "job", "scope", "scope_value", "key", "value_type", "value"}
        ),
        optional_columns=frozenset({"comment", "updated_at", "updated_by"}),
    ),
    # §3.9
    "ui_layout": SheetSchema(
        sheet="ui_layout",
        required_columns=frozenset(
            {"id", "enabled", "view", "section", "order", "key"}
        ),
        optional_columns=frozenset({"title", "style", "notes"}),
    ),
    # §3.10 — schema follows the documented loose contract: only
    # `display_name` is treated as required structurally; everything else
    # described in §3.10 is optional (semantic rules live in row-level
    # validators, not in the workbook schema).
    "hourly_payins": SheetSchema(
        sheet="hourly_payins",
        required_columns=frozenset({"display_name"}),
        optional_columns=frozenset(
            {
                "group_code",
                "source_partners",
                "comment",
                "enabled",
                "sort_order",
                "group_break_after",
            }
        ),
    ),
    # §3.11
    "hourly_payout_methods": SheetSchema(
        sheet="hourly_payout_methods",
        required_columns=frozenset({"group_code", "enabled", "sort_order"}),
        optional_columns=frozenset(
            {"method_code", "method_name", "source_partners", "comment"}
        ),
    ),
    # §3.12
    "hourly_payouts": SheetSchema(
        sheet="hourly_payouts",
        required_columns=frozenset({"group_code", "enabled", "sort_order"}),
        optional_columns=frozenset({"display_name", "group_break_after"}),
    ),
    # §3.13 — group_priority / is_primary are CONTRACT V2 forward-looking
    # optional columns (see §3.13 note about Excel extension for
    # deterministic primary group, §8.3); their absence is fine in
    # current legacy files. Runtime membership order without those fields
    # MUST match snapshot insertion order (CONTRACT_V2 §8.3 invariant).
    "partner_groups": SheetSchema(
        sheet="partner_groups",
        required_columns=frozenset(
            {"id", "enabled", "analyzers", "group_name", "partner"}
        ),
        optional_columns=frozenset(
            {
                "default_method",
                "updated_at",
                "comment",
                "group_priority",
                "is_primary",
            }
        ),
    ),
}


# All sheets known to CONTRACT_V2 §3.
KNOWN_SHEETS: Final[frozenset[str]] = frozenset(SHEET_SCHEMAS.keys())


# Sheets that MUST be present in any valid V2 workbook. Conservative set
# chosen to match the legacy CLI baseline (avoids spurious MUST FAIL on
# the current production rules.xlsx). Optional-but-recognized sheets emit
# a WARN-level RULE_MISSING_SHEET when absent (policy in
# ``validation_workbook``).
#
# Rationale per CONTRACT_V2 §3 + §19: these four are the absolute minimum
# for Telegram / time-window operability.
REQUIRED_SHEETS: Final[frozenset[str]] = frozenset(
    {"meta", "exclude_time", "access", "commands"}
)


# Recognized-but-not-required sheets. Absence emits WARN, not ERROR.
OPTIONAL_SHEETS: Final[frozenset[str]] = KNOWN_SHEETS - REQUIRED_SHEETS


__all__ = [
    "SheetSchema",
    "SHEET_SCHEMAS",
    "KNOWN_SHEETS",
    "REQUIRED_SHEETS",
    "OPTIONAL_SHEETS",
    "normalize_header",
]
