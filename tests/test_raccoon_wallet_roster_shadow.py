"""Tests for Raccoon Wallet partner roster shadow (CONFIG-MIGRATION-PHASE-3B-1)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from analyzers.raccoon_wallet_config_loader import (
    DEFAULT_YAML_PATH,
    RaccoonWalletScalarParams,
    load_legacy_yaml_config,
    resolve_raccoon_wallet_config,
    run_raccoon_wallet_roster_shadow_compare,
)
from core.rules_v2.raccoon_wallet_rules_accessor import (
    RaccoonWalletRoster,
    build_roster_from_sheets,
    build_roster_from_yaml,
    roster_diff,
)
from utils.normalization import normalize_partner_name

REPO_YAML_PARTNER_COUNT = 11


def _write_repo_yaml_copy(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_YAML_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_raccoon_wallet_roster_from_yaml():
    cfg = load_legacy_yaml_config(DEFAULT_YAML_PATH)
    roster = build_roster_from_yaml(cfg)

    assert len(roster.normalized_keys) == REPO_YAML_PARTNER_COUNT
    assert normalize_partner_name("Cat.Casino (207)") in roster.normalized_keys
    assert roster.norm_to_display[normalize_partner_name("Cat.Casino (207)")] == "Cat.Casino (207)"
    assert normalize_partner_name("Motor (215)") in roster.normalized_keys


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
                {
                    "enabled": 1,
                    "analyzers": "raccoon_wallet",
                    "scope": "group",
                    "scope_value": "GroupA",
                    "limit_type": "daily_max_amount",
                    "limit_value": 5000,
                },
            ]
        ),
    }
    roster = build_roster_from_sheets(sheets)
    assert normalize_partner_name("Motor (215)") in roster.normalized_keys
    assert "groupa" not in roster.normalized_keys


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


def test_raccoon_wallet_roster_shadow_no_diff(tmp_path: Path, caplog):
    yaml_path = _write_repo_yaml_copy(tmp_path / "raccoon.yaml")
    yaml_roster = build_roster_from_yaml(load_legacy_yaml_config(yaml_path))

    caplog.set_level(logging.WARNING)
    with patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=yaml_roster,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_roster_shadow_compare(
            logging.getLogger("test.roster.shadow.ok"),
            yaml_path=yaml_path,
        )

    assert not any("[config_shadow] raccoon_wallet roster mismatch" in r.message for r in caplog.records)


def test_raccoon_wallet_roster_shadow_yaml_only_warning(tmp_path: Path, caplog):
    yaml_path = _write_repo_yaml_copy(tmp_path / "raccoon.yaml")
    yaml_roster = build_roster_from_yaml(load_legacy_yaml_config(yaml_path))
    motor_norm = normalize_partner_name("Motor (215)")
    rules_map = dict(yaml_roster.norm_to_display)
    rules_map.pop(motor_norm, None)
    rules_roster = RaccoonWalletRoster(norm_to_display=rules_map)

    caplog.set_level(logging.WARNING)
    with patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=rules_roster,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_roster_shadow_compare(
            logging.getLogger("test.roster.shadow.yaml_only"),
            yaml_path=yaml_path,
        )

    assert any("[config_shadow] raccoon_wallet roster mismatch" in r.message for r in caplog.records)
    assert any("roster blocker: yaml_only" in r.message for r in caplog.records)
    diff = roster_diff(yaml_roster, rules_roster)
    assert motor_norm in diff["yaml_only"]


def test_raccoon_wallet_roster_shadow_rules_only_warning(tmp_path: Path, caplog):
    yaml_path = _write_repo_yaml_copy(tmp_path / "raccoon.yaml")
    yaml_roster = build_roster_from_yaml(load_legacy_yaml_config(yaml_path))
    rules_roster = RaccoonWalletRoster(
        norm_to_display={
            **yaml_roster.norm_to_display,
            "new_partner": "New Partner (999)",
        }
    )

    caplog.set_level(logging.WARNING)
    with patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=rules_roster,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_roster_shadow_compare(
            logging.getLogger("test.roster.shadow.rules_only"),
            yaml_path=yaml_path,
        )

    assert any("[config_shadow] raccoon_wallet roster mismatch" in r.message for r in caplog.records)
    diff = roster_diff(yaml_roster, rules_roster)
    assert "new_partner" in diff["rules_only"]


def test_raccoon_wallet_rules_mode_falls_back_when_roster_incomplete(tmp_path: Path, monkeypatch):
    yaml_path = _write_repo_yaml_copy(tmp_path / "raccoon.yaml")
    yaml_cfg = load_legacy_yaml_config(yaml_path)
    yaml_partner_keys = set(yaml_cfg.get("partners", {}).keys())

    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    rules_roster = RaccoonWalletRoster(norm_to_display={"only_rules": "Only Rules (1)"})

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=RaccoonWalletScalarParams(
            window_minutes=45,
            offset_minutes=0,
            min_events=5,
            pending_payin_minutes=10,
            payin_days_back=0,
        ),
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=rules_roster,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_cfg_from_rules",
        return_value={},
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        cfg = resolve_raccoon_wallet_config(
            logging.getLogger("test.roster.rules_mode"),
            yaml_path=yaml_path,
        )

    assert cfg["window_minutes"] == 45
    assert set(cfg.get("partners", {}).keys()) == yaml_partner_keys
    assert "Only Rules (1)" not in cfg.get("partners", {})
