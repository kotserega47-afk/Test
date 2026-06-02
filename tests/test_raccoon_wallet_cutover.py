"""Tests for Raccoon Wallet runtime roster/groups cutover (CONFIG-MIGRATION-PHASE-3B-5)."""

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
    try_load_legacy_yaml_config,
)
from core.rules_v2.raccoon_wallet_rules_accessor import (
    RaccoonWalletRoster,
    build_roster_from_yaml,
    partners_cfg_from_roster,
)

_GOLDEN_SCALARS = RaccoonWalletScalarParams(
    window_minutes=60,
    offset_minutes=0,
    min_events=5,
    pending_payin_minutes=10,
    payin_days_back=0,
)


def _write_repo_yaml_copy(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_YAML_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _yaml_roster_from_path(yaml_path: Path) -> RaccoonWalletRoster:
    return build_roster_from_yaml(load_legacy_yaml_config(yaml_path))


def _complete_rules_roster(yaml_path: Path) -> RaccoonWalletRoster:
    return _yaml_roster_from_path(yaml_path)


@pytest.fixture
def raccoon_yaml(tmp_path: Path) -> Path:
    return _write_repo_yaml_copy(tmp_path / "raccoon.yaml")


def test_raccoon_rules_mode_uses_rules_roster_when_complete(raccoon_yaml: Path, monkeypatch, caplog):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    rules_roster = _complete_rules_roster(raccoon_yaml)

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
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
            logging.getLogger("test.cutover.roster.complete"),
            yaml_path=raccoon_yaml,
        )

    yaml_partners = set(load_legacy_yaml_config(raccoon_yaml).get("partners", {}).keys())
    assert set(cfg["partners"].keys()) == set(rules_roster.sorted_display_names())
    assert set(cfg["partners"].keys()) == yaml_partners
    assert any(
        "[raccoon_wallet_config] source=rules_v2 fields=" in r.message
        and "scalars" in r.message
        and "roster" in r.message
        and "groups" in r.message
        for r in caplog.records
    )


def test_raccoon_rules_mode_falls_back_to_yaml_when_roster_incomplete(raccoon_yaml: Path, monkeypatch, caplog):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    yaml_cfg = load_legacy_yaml_config(raccoon_yaml)
    yaml_partner_keys = set(yaml_cfg.get("partners", {}).keys())
    yaml_roster = _yaml_roster_from_path(raccoon_yaml)
    rules_map = dict(yaml_roster.norm_to_display)
    first_norm = next(iter(yaml_roster.normalized_keys))
    rules_map.pop(first_norm)
    rules_roster = RaccoonWalletRoster(norm_to_display=rules_map)

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
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
            logging.getLogger("test.cutover.roster.incomplete"),
            yaml_path=raccoon_yaml,
        )

    assert set(cfg["partners"].keys()) == yaml_partner_keys
    assert any(
        "reason=rules_roster_missing_or_incomplete" in r.message for r in caplog.records
    )


def test_raccoon_rules_mode_does_not_use_empty_roster(raccoon_yaml: Path, monkeypatch, caplog):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    yaml_partner_keys = set(load_legacy_yaml_config(raccoon_yaml).get("partners", {}).keys())

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=RaccoonWalletRoster(norm_to_display={}),
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_cfg_from_rules",
        return_value={},
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        cfg = resolve_raccoon_wallet_config(
            logging.getLogger("test.cutover.roster.empty"),
            yaml_path=raccoon_yaml,
        )

    assert set(cfg["partners"].keys()) == yaml_partner_keys
    assert any(
        "reason=rules_roster_missing_or_incomplete" in r.message for r in caplog.records
    )


def test_raccoon_rules_mode_uses_rules_groups(raccoon_yaml: Path, monkeypatch):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    rules_roster = _complete_rules_roster(raccoon_yaml)
    rules_groups = {
        "vip": {
            "partners": ["Cat.Casino (207)", "Motor (215)"],
        },
    }

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=rules_roster,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_cfg_from_rules",
        return_value=rules_groups,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        cfg = resolve_raccoon_wallet_config(
            logging.getLogger("test.cutover.groups.rules"),
            yaml_path=raccoon_yaml,
        )

    assert cfg["groups"] == rules_groups


def test_raccoon_rules_mode_falls_back_to_yaml_groups_on_error(raccoon_yaml: Path, monkeypatch, caplog):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    rules_roster = _complete_rules_roster(raccoon_yaml)

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=rules_roster,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_cfg_from_rules",
        return_value=None,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        cfg = resolve_raccoon_wallet_config(
            logging.getLogger("test.cutover.groups.fallback"),
            yaml_path=raccoon_yaml,
        )

    assert cfg["groups"] == {}
    assert any("reason=rules_groups_unavailable" in r.message for r in caplog.records)


def test_raccoon_rules_mode_empty_groups_valid(raccoon_yaml: Path, monkeypatch):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    rules_roster = _complete_rules_roster(raccoon_yaml)

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
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
            logging.getLogger("test.cutover.groups.empty"),
            yaml_path=raccoon_yaml,
        )

    assert cfg["groups"] == {}


def test_raccoon_rules_mode_logs_mixed_sources(raccoon_yaml: Path, monkeypatch, caplog):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    rules_roster = _complete_rules_roster(raccoon_yaml)
    yaml_roster = _yaml_roster_from_path(raccoon_yaml)
    incomplete_map = dict(yaml_roster.norm_to_display)
    incomplete_map.pop(next(iter(yaml_roster.normalized_keys)))
    incomplete_roster = RaccoonWalletRoster(norm_to_display=incomplete_map)
    rules_groups = {"vip": {"partners": ["Cat.Casino (207)"]}}

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=incomplete_roster,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_cfg_from_rules",
        return_value=rules_groups,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        resolve_raccoon_wallet_config(
            logging.getLogger("test.cutover.mixed"),
            yaml_path=raccoon_yaml,
        )

    assert any(
        "[raccoon_wallet_config] source=mixed fields=groups=rules_v2,roster=yaml,scalars=rules_v2"
        in r.message
        for r in caplog.records
    )


def test_raccoon_rules_mode_can_resolve_without_yaml_when_rules_complete(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    missing_yaml = tmp_path / "missing.yaml"
    rules_roster = RaccoonWalletRoster(
        norm_to_display={
            "cat.casino": "Cat.Casino (207)",
            "motor": "Motor (215)",
        }
    )

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
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
            logging.getLogger("test.cutover.no_yaml"),
            yaml_path=missing_yaml,
        )

    assert set(cfg["partners"].keys()) == {"Cat.Casino (207)", "Motor (215)"}
    assert cfg["window_minutes"] == 60
    assert try_load_legacy_yaml_config(missing_yaml) is None


def test_raccoon_yaml_mode_requires_yaml(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", raising=False)
    missing_yaml = tmp_path / "missing.yaml"

    with pytest.raises(RuntimeError, match="raccoon_wallet_config.yaml не найден"):
        resolve_raccoon_wallet_config(
            logging.getLogger("test.cutover.yaml_required"),
            yaml_path=missing_yaml,
        )


def test_partners_cfg_from_roster_preserves_display_names():
    roster = RaccoonWalletRoster(
        norm_to_display={
            "cat.casino": "Cat.Casino (207)",
            "motor": "Motor (215)",
        }
    )
    partners = partners_cfg_from_roster(roster)
    assert partners == {"Cat.Casino (207)": {}, "Motor (215)": {}}
