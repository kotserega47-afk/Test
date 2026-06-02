"""Tests for Raccoon Wallet groups shadow + dead fields cleanup (CONFIG-MIGRATION-PHASE-3B-3)."""

from __future__ import annotations

import logging
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pandas as pd

from analyzers.raccoon_wallet_analyzer import API_CANCEL_INFO_KEYWORD
from analyzers.raccoon_wallet_config_loader import (
    DEFAULT_YAML_PATH,
    load_legacy_yaml_config,
    resolve_raccoon_wallet_config,
    run_raccoon_wallet_groups_shadow_compare,
)
from core.rules_v2.raccoon_wallet_rules_accessor import (
    RaccoonWalletGroupsMembership,
    build_groups_from_sheets,
    build_groups_from_yaml,
    groups_diff,
)
from utils.normalization import normalize_partner_name


def _write_yaml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return path


def _minimal_yaml_with_groups(groups_yaml: str) -> str:
    return f"""
        window_minutes: 60
        offset_minutes: 0
        min_events: 5
        pending_thresholds:
          payin_minutes: 10
        download_periods:
          payin_days_back: 0
        groups:
        {groups_yaml}
        partners:
          "Cat.Casino (207)": {{}}
        columns:
          payin:
            dt: "Дата/Время создания"
            partner: "Партнер"
            status: "Статус"
            info: "Инфо"
            amount: "Сумма"
    """


def _rules_groups_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_raccoon_groups_shadow_no_diff_empty(tmp_path: Path, caplog):
    yaml_path = _write_yaml(
        tmp_path / "raccoon.yaml",
        _minimal_yaml_with_groups("  {}"),
    )
    empty_rules: RaccoonWalletGroupsMembership = {}

    caplog.set_level(logging.WARNING)
    with patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_from_rules",
        return_value=empty_rules,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_groups_shadow_compare(
            logging.getLogger("test.groups.shadow.empty"),
            yaml_path=yaml_path,
        )

    assert not any("[config_shadow] raccoon_wallet groups mismatch" in r.message for r in caplog.records)


def test_raccoon_groups_shadow_rules_only_warning(tmp_path: Path, caplog):
    yaml_path = _write_yaml(
        tmp_path / "raccoon.yaml",
        _minimal_yaml_with_groups("  {}"),
    )
    rules_groups = build_groups_from_sheets(
        {
            "partner_groups": _rules_groups_df(
                [
                    {
                        "id": "PG-1",
                        "enabled": 1,
                        "analyzers": "raccoon_wallet",
                        "group_name": "test_group",
                        "partner": "Cat.Casino (207)",
                    },
                ]
            ),
        }
    )

    caplog.set_level(logging.WARNING)
    with patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_from_rules",
        return_value=rules_groups,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_groups_shadow_compare(
            logging.getLogger("test.groups.shadow.rules_only"),
            yaml_path=yaml_path,
        )

    warnings = [r for r in caplog.records if "[config_shadow] raccoon_wallet groups mismatch" in r.message]
    assert len(warnings) == 1
    assert "test_group" in warnings[0].message


def test_raccoon_groups_shadow_yaml_only_warning(tmp_path: Path, caplog):
    yaml_path = _write_yaml(
        tmp_path / "raccoon.yaml",
        _minimal_yaml_with_groups(
            """
              vip:
                partners:
                  - "Cat.Casino (207)"
            """
        ),
    )

    caplog.set_level(logging.WARNING)
    with patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_from_rules",
        return_value={},
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_groups_shadow_compare(
            logging.getLogger("test.groups.shadow.yaml_only"),
            yaml_path=yaml_path,
        )

    warnings = [r for r in caplog.records if "[config_shadow] raccoon_wallet groups mismatch" in r.message]
    assert len(warnings) == 1
    assert "vip" in warnings[0].message
    assert any("yaml_only groups" in r.message for r in caplog.records)


def test_raccoon_groups_shadow_membership_diff(tmp_path: Path, caplog):
    yaml_path = _write_yaml(
        tmp_path / "raccoon.yaml",
        _minimal_yaml_with_groups(
            """
              vip:
                partners:
                  - "Cat.Casino (207)"
                  - "Motor (215)"
            """
        ),
    )
    rules_groups = build_groups_from_sheets(
        {
            "partner_groups": _rules_groups_df(
                [
                    {
                        "id": "PG-1",
                        "enabled": 1,
                        "analyzers": "raccoon_wallet",
                        "group_name": "vip",
                        "partner": "Cat.Casino (207)",
                    },
                ]
            ),
        }
    )

    caplog.set_level(logging.WARNING)
    with patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_from_rules",
        return_value=rules_groups,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_groups_shadow_compare(
            logging.getLogger("test.groups.shadow.membership"),
            yaml_path=yaml_path,
        )

    diff = groups_diff(build_groups_from_yaml(load_legacy_yaml_config(yaml_path)), rules_groups)
    motor_norm = normalize_partner_name("Motor (215)")
    assert diff["membership_diff"]["vip"]["yaml_only"] == [motor_norm]

    warnings = [r for r in caplog.records if "[config_shadow] raccoon_wallet groups mismatch" in r.message]
    assert len(warnings) == 1
    assert motor_norm in warnings[0].message


def test_raccoon_dead_fields_not_required(tmp_path: Path):
    yaml_path = _write_yaml(
        tmp_path / "raccoon_clean.yaml",
        """
        window_minutes: 60
        offset_minutes: 0
        min_events: 5
        pending_thresholds:
          payin_minutes: 10
        download_periods:
          payin_days_back: 0
        groups: {}
        partners:
          "Cat.Casino (207)": {}
        columns:
          payin:
            dt: "Дата/Время создания"
            partner: "Партнер"
            status: "Статус"
            info: "Инфо"
            amount: "Сумма"
        """,
    )

    cfg = load_legacy_yaml_config(yaml_path)
    assert cfg["window_minutes"] == 60
    assert cfg["pending_thresholds"] == {"payin_minutes": 10}
    assert cfg["download_periods"] == {"payin_days_back": 0}
    assert "success_window_minutes" not in cfg
    assert cfg["partners"]["Cat.Casino (207)"] == {}


def test_raccoon_api_cancel_behavior_unchanged():
    info = pd.Series(["Отмена по API", "ok", "ОТМЕНА ПО API", ""])
    matches = info.astype(str).str.lower().str.contains(API_CANCEL_INFO_KEYWORD, case=False, na=False)
    assert matches.tolist() == [True, False, True, False]
    assert API_CANCEL_INFO_KEYWORD == "отмена по api"


def test_raccoon_payout_dead_fields_removed_without_behavior_change(tmp_path: Path):
    yaml_path = _write_yaml(
        tmp_path / "raccoon_clean.yaml",
        """
        window_minutes: 60
        offset_minutes: 0
        min_events: 5
        pending_thresholds:
          payin_minutes: 10
        download_periods:
          payin_days_back: 0
        groups: {}
        partners:
          "Cat.Casino (207)": {}
        columns:
          payin:
            dt: "Дата/Время создания"
            partner: "Партнер"
            status: "Статус"
            info: "Инфо"
            amount: "Сумма"
        """,
    )

    cfg = resolve_raccoon_wallet_config(logging.getLogger("test.dead_fields"), yaml_path=yaml_path)
    assert "payout_minutes" not in cfg.get("pending_thresholds", {})
    assert "payout_days_back" not in cfg.get("download_periods", {})

    source = Path(__file__).resolve().parents[1] / "analyzers" / "raccoon_wallet_analyzer.py"
    analyzer_source = source.read_text(encoding="utf-8")
    assert "payout_limit" not in analyzer_source

    downloader_source = (
        Path(__file__).resolve().parents[1] / "integrations" / "raccoon_wallet_downloader.py"
    ).read_text(encoding="utf-8")
    assert "payout_days_back" not in downloader_source


def test_repo_yaml_has_no_dead_fields():
    text = DEFAULT_YAML_PATH.read_text(encoding="utf-8")
    assert "success_window_minutes" not in text
    assert "api_cancel_keyword" not in text
    assert "payout_minutes" not in text
    assert "payout_days_back" not in text
