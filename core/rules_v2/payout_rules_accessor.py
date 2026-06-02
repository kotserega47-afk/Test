"""Read payout error / ignore rules from Rules V2 workbook sheets.

Sheets ``payout_info_rules`` and ``payout_ignore_phrases`` mirror
``config/payout_config.yaml`` (``PayoutsErrors`` / ``IgnoreErrors``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.rules_v2.bridge_legacy import _is_enabled_default_true
from core.rules_v2.loader import read_rules_excel

SHEET_INFO_RULES = "payout_info_rules"
SHEET_IGNORE_PHRASES = "payout_ignore_phrases"


@dataclass(frozen=True, slots=True)
class PayoutRulesData:
    """Raw payout config comparable to ``payout_config.yaml`` structure."""

    payout_rules: dict[str, dict[str, int]]
    ignore_phrases: list[str]


def _cell_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _parse_threshold(value: Any, *, default: int = 1) -> int:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalize_payout_config_for_compare(data: PayoutRulesData) -> dict[str, Any]:
    """Return a deterministic view for YAML ↔ rules shadow comparison."""

    rules: dict[str, dict[str, int]] = {}
    for phrase, spec in data.payout_rules.items():
        key = phrase.lower().strip()
        if not key:
            continue
        threshold = spec.get("threshold", 1) if isinstance(spec, dict) else 1
        rules[key] = {"threshold": int(threshold)}

    ignore = sorted(x.lower().strip() for x in data.ignore_phrases if _cell_str(x))

    return {
        "payout_rules": dict(sorted(rules.items())),
        "ignore_phrases": ignore,
    }


def normalize_payout_config_for_runtime(data: PayoutRulesData) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Return lower/strip views used by ``analyzers/payout.py`` matching."""

    payouts_errors_norm = {k.lower().strip(): v for k, v in data.payout_rules.items()}
    ignore_errors_norm = [x.lower().strip() for x in data.ignore_phrases]
    return payouts_errors_norm, ignore_errors_norm


def payout_rules_data_has_content(data: PayoutRulesData | None) -> bool:
    if data is None:
        return False
    return bool(data.payout_rules or data.ignore_phrases)


class PayoutRulesAccessor:
    """Load payout config rows from Rules V2 Excel sheets."""

    @staticmethod
    def from_sheets(sheets: dict[str, pd.DataFrame]) -> PayoutRulesData | None:
        info_df = sheets.get(SHEET_INFO_RULES)
        ignore_df = sheets.get(SHEET_IGNORE_PHRASES)

        if info_df is None and ignore_df is None:
            return None

        payout_rules = PayoutRulesAccessor._read_info_rules(info_df)
        ignore_phrases = PayoutRulesAccessor._read_ignore_phrases(ignore_df)

        if not payout_rules and not ignore_phrases:
            return None

        return PayoutRulesData(payout_rules=payout_rules, ignore_phrases=ignore_phrases)

    @staticmethod
    def from_rules_path(path: str | Path | None = None) -> PayoutRulesData | None:
        try:
            sheets = read_rules_excel(
                path,
                only_sheets=(SHEET_INFO_RULES, SHEET_IGNORE_PHRASES),
            )
        except Exception:
            return None
        return PayoutRulesAccessor.from_sheets(sheets)

    @staticmethod
    def _read_info_rules(df: pd.DataFrame | None) -> dict[str, dict[str, int]]:
        if df is None or df.empty:
            return {}

        out: dict[str, dict[str, int]] = {}
        for _, row in df.iterrows():
            phrase = _cell_str(row.get("info_phrase"))
            if not phrase:
                continue
            if not _is_enabled_default_true(row.get("enabled")):
                continue
            threshold = _parse_threshold(row.get("threshold"), default=1)
            out[phrase] = {"threshold": threshold}
        return out

    @staticmethod
    def _read_ignore_phrases(df: pd.DataFrame | None) -> list[str]:
        if df is None or df.empty:
            return []

        out: list[str] = []
        for _, row in df.iterrows():
            phrase = _cell_str(row.get("info_phrase"))
            if not phrase:
                continue
            if not _is_enabled_default_true(row.get("enabled")):
                continue
            out.append(phrase)
        return out


__all__ = [
    "PayoutRulesAccessor",
    "PayoutRulesData",
    "SHEET_IGNORE_PHRASES",
    "SHEET_INFO_RULES",
    "normalize_payout_config_for_compare",
    "normalize_payout_config_for_runtime",
    "payout_rules_data_has_content",
]
