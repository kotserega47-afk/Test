"""Tests for Raccoon Wallet Rules V2 roster accessor."""

from __future__ import annotations

import pandas as pd

from core.rules_v2.raccoon_wallet_rules_accessor import (
    build_groups_cfg_from_sheets,
    build_groups_from_sheets,
    build_roster_from_sheets,
)
from utils.normalization import normalize_partner_name


def test_raccoon_wallet_roster_from_rules_thresholds_partner():
    sheets = {
        "thresholds_partner": pd.DataFrame(
            [
                {
                    "enabled": 1,
                    "analyzer": "raccoon_wallet",
                    "partner": "Cat.Casino (207)",
                    "metric": "conversion_rate",
                },
                {
                    "enabled": 1,
                    "analyzer": "wallet",
                    "partner": "Other (999)",
                    "metric": "conversion_rate",
                },
            ]
        ),
    }
    roster = build_roster_from_sheets(sheets)
    assert normalize_partner_name("Cat.Casino (207)") in roster.normalized_keys
    assert normalize_partner_name("Other (999)") not in roster.normalized_keys


def test_raccoon_wallet_roster_from_rules_wallet_limits():
    sheets = {
        "wallet_limits": pd.DataFrame(
            [
                {
                    "enabled": 1,
                    "analyzers": "raccoon_wallet,hourly",
                    "scope": "partner",
                    "scope_value": "Motor (215)",
                    "limit_type": "daily_max_amount",
                    "limit_value": 1000,
                },
            ]
        ),
    }
    roster = build_roster_from_sheets(sheets)
    assert normalize_partner_name("Motor (215)") in roster.normalized_keys


def test_raccoon_wallet_roster_from_rules_partner_groups():
    sheets = {
        "partner_groups": pd.DataFrame(
            [
                {
                    "id": "PG-1",
                    "enabled": 1,
                    "analyzers": "raccoon_wallet",
                    "group_name": "G1",
                    "partner": "Billion_pay (206)",
                },
            ]
        ),
    }
    roster = build_roster_from_sheets(sheets)
    assert normalize_partner_name("Billion_pay (206)") in roster.normalized_keys


def test_raccoon_wallet_groups_cfg_from_rules():
    sheets = {
        "partner_groups": pd.DataFrame(
            [
                {
                    "id": "PG-1",
                    "enabled": 1,
                    "analyzers": "raccoon_wallet",
                    "group_name": "vip",
                    "partner": "Cat.Casino (207)",
                },
                {
                    "id": "PG-2",
                    "enabled": 1,
                    "analyzers": "raccoon_wallet",
                    "group_name": "vip",
                    "partner": "Motor (215)",
                },
            ]
        ),
    }
    groups_cfg = build_groups_cfg_from_sheets(sheets)
    assert groups_cfg == {"vip": {"partners": ["Cat.Casino (207)", "Motor (215)"]}}

    membership = build_groups_from_sheets(sheets)
    assert "vip" in membership
    assert normalize_partner_name("Cat.Casino (207)") in membership["vip"]
