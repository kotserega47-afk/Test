"""Raccoon Wallet PayIn Excel column mapping (code constants, Phase 3B-2)."""

from __future__ import annotations

from typing import Any

# Stable Raccoon PayIn export headers (mirrors legacy config/raccoon_wallet_config.yaml).
RACCOON_PAYIN_COLUMN_MAP: dict[str, str] = {
    "dt": "Дата/Время создания",
    "partner": "Партнер",
    "status": "Статус",
    "info": "Инфо",
    "amount": "Сумма",
}

RACCOON_PAYIN_REQUIRED_COLUMNS: tuple[str, ...] = ("dt", "partner", "status", "amount")
RACCOON_PAYIN_OPTIONAL_COLUMNS: tuple[str, ...] = ("info",)


def get_raccoon_payin_column_map() -> dict[str, str]:
    """Return a copy of the PayIn internal_key → Excel header mapping."""

    return dict(RACCOON_PAYIN_COLUMN_MAP)


def normalize_payin_column_map(mapping: dict[str, Any]) -> dict[str, str]:
    return {str(k): str(v).strip() for k, v in mapping.items() if v is not None and str(v).strip()}


def extract_payin_columns_from_yaml(cfg: dict[str, Any]) -> dict[str, str]:
    cols = ((cfg.get("columns") or {}).get("payin")) or {}
    return normalize_payin_column_map(cols)


def payin_columns_diff(yaml_map: dict[str, str], code_map: dict[str, str]) -> dict[str, Any]:
    """Compare YAML vs code column maps; empty dict if identical."""

    if yaml_map == code_map:
        return {}

    diff_keys: dict[str, dict[str, str]] = {}
    all_keys = sorted(set(yaml_map.keys()) | set(code_map.keys()))
    for key in all_keys:
        yaml_val = yaml_map.get(key)
        code_val = code_map.get(key)
        if yaml_val != code_val:
            diff_keys[key] = {"yaml": yaml_val or "", "code": code_val or ""}

    return {
        "yaml": yaml_map,
        "code": code_map,
        "diff": diff_keys,
    }


def yaml_payin_columns_match_constants(cfg: dict[str, Any]) -> bool:
    yaml_map = extract_payin_columns_from_yaml(cfg)
    code_map = get_raccoon_payin_column_map()
    return yaml_map == code_map
