"""Tests for Raccoon Wallet PayIn column constants + shadow (CONFIG-MIGRATION-PHASE-3B-2)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from analyzers.raccoon_wallet_analyzer import _apply_mapping, _require_mapping
from analyzers.raccoon_wallet_columns import (
    RACCOON_PAYIN_REQUIRED_COLUMNS,
    extract_payin_columns_from_yaml,
    get_raccoon_payin_column_map,
    payin_columns_diff,
    yaml_payin_columns_match_constants,
)
from analyzers.raccoon_wallet_config_loader import (
    DEFAULT_YAML_PATH,
    apply_payin_columns_to_cfg,
    load_legacy_yaml_config,
    resolve_raccoon_wallet_config,
    run_raccoon_payin_columns_shadow_compare,
)


def test_raccoon_columns_constants_match_current_yaml():
    cfg = load_legacy_yaml_config(DEFAULT_YAML_PATH)
    yaml_map = extract_payin_columns_from_yaml(cfg)
    code_map = get_raccoon_payin_column_map()

    assert yaml_map == code_map
    assert yaml_payin_columns_match_constants(cfg)


def test_raccoon_columns_shadow_no_diff(caplog):
    caplog.set_level(logging.WARNING)

    run_raccoon_payin_columns_shadow_compare(logging.getLogger("test.columns.shadow"))

    assert not any("[config_shadow] raccoon_wallet columns mismatch" in r.message for r in caplog.records)


def test_raccoon_columns_shadow_detects_diff(tmp_path: Path, caplog):
    yaml_path = tmp_path / "raccoon_wallet_config.yaml"
    text = DEFAULT_YAML_PATH.read_text(encoding="utf-8").replace(
        'info: "Инфо"',
        'info: "Информация"',
    )
    yaml_path.write_text(text, encoding="utf-8")

    caplog.set_level(logging.WARNING)
    run_raccoon_payin_columns_shadow_compare(logging.getLogger("test.columns.shadow"), yaml_path=yaml_path)

    warnings = [r for r in caplog.records if "[config_shadow] raccoon_wallet columns mismatch" in r.message]
    assert len(warnings) == 1
    assert "Информация" in warnings[0].message
    assert "Инфо" in warnings[0].message


def test_raccoon_columns_shadow_diff_structure():
    yaml_map = {"dt": "Дата/Время создания", "info": "Информация"}
    code_map = {"dt": "Дата/Время создания", "info": "Инфо"}
    diff = payin_columns_diff(yaml_map, code_map)

    assert diff["diff"]["info"] == {"yaml": "Информация", "code": "Инфо"}


def test_raccoon_analyzer_uses_code_column_constants():
    cfg = load_legacy_yaml_config(DEFAULT_YAML_PATH)
    cfg["columns"]["payin"]["info"] = "Информация"

    yaml_map = extract_payin_columns_from_yaml(cfg)
    code_map = get_raccoon_payin_column_map()
    assert yaml_map != code_map

    df = pd.DataFrame(
        {
            code_map["dt"]: ["2026-01-01 10:00:00"],
            code_map["partner"]: ["Cat.Casino (207)"],
            code_map["status"]: ["Успех"],
            code_map["info"]: ["note"],
            code_map["amount"]: [100.0],
        }
    )

    mapped = _apply_mapping(
        df,
        code_map,
        required=list(RACCOON_PAYIN_REQUIRED_COLUMNS),
        kind="payin",
    )
    assert "info" in mapped.columns
    assert mapped.loc[0, "partner"] == "Cat.Casino (207)"


def test_resolve_injects_code_columns_even_when_yaml_differs(tmp_path: Path):
    yaml_path = tmp_path / "raccoon_wallet_config.yaml"
    text = DEFAULT_YAML_PATH.read_text(encoding="utf-8").replace(
        'info: "Инфо"',
        'info: "Информация"',
    )
    yaml_path.write_text(text, encoding="utf-8")

    cfg = resolve_raccoon_wallet_config(
        logging.getLogger("test.columns.resolve"),
        yaml_path=yaml_path,
    )
    assert cfg["columns"]["payin"] == get_raccoon_payin_column_map()


def test_raccoon_mapping_required_columns_unchanged():
    mapping = get_raccoon_payin_column_map()
    _require_mapping(mapping, required=list(RACCOON_PAYIN_REQUIRED_COLUMNS), kind="payin")

    df = pd.DataFrame(
        {
            mapping["dt"]: ["2026-01-01 10:00:00"],
            mapping["partner"]: ["Motor (215)"],
            mapping["status"]: ["Успех"],
            mapping["amount"]: [50.0],
        }
    )
    mapped = _apply_mapping(
        df,
        mapping,
        required=list(RACCOON_PAYIN_REQUIRED_COLUMNS),
        kind="payin",
    )
    for col in RACCOON_PAYIN_REQUIRED_COLUMNS:
        assert col in mapped.columns


def test_raccoon_mapping_info_optional():
    mapping = {k: v for k, v in get_raccoon_payin_column_map().items() if k != "info"}

    df = pd.DataFrame(
        {
            mapping["dt"]: ["2026-01-01 10:00:00"],
            mapping["partner"]: ["Motor (215)"],
            mapping["status"]: ["Успех"],
            mapping["amount"]: [50.0],
        }
    )
    mapped = _apply_mapping(
        df,
        mapping,
        required=list(RACCOON_PAYIN_REQUIRED_COLUMNS),
        kind="payin",
    )
    assert "info" not in mapped.columns
    assert list(mapped.columns) == list(RACCOON_PAYIN_REQUIRED_COLUMNS)


def test_apply_payin_columns_to_cfg_overwrites_yaml_section():
    cfg = load_legacy_yaml_config(DEFAULT_YAML_PATH)
    cfg["columns"]["payin"]["info"] = "Информация"

    out = apply_payin_columns_to_cfg(cfg)
    assert out["columns"]["payin"] == get_raccoon_payin_column_map()
