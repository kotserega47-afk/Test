"""Payout config resolution: YAML, Rules V2, shadow compare, runtime switch."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from core.rules_v2.payout_rules_accessor import (
    PayoutRulesAccessor,
    PayoutRulesData,
    normalize_payout_config_for_compare,
    normalize_payout_config_for_runtime,
    payout_rules_data_has_content,
)

DEFAULT_YAML_PATH = Path(__file__).resolve().parent.parent / "config" / "payout_config.yaml"

_ENV_USE_RULES = "PAYOUT_CONFIG_FROM_RULES_V2"


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def load_yaml_payout_config(path: str | Path | None = None) -> PayoutRulesData:
    config_path = Path(path) if path is not None else DEFAULT_YAML_PATH
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    payouts_errors = raw.get("PayoutsErrors", {}) or {}
    ignore_errors = raw.get("IgnoreErrors", []) or []

    payout_rules: dict[str, dict[str, int]] = {}
    for phrase, spec in payouts_errors.items():
        if not str(phrase or "").strip():
            continue
        if isinstance(spec, dict):
            threshold = spec.get("threshold", 1)
        else:
            threshold = spec
        payout_rules[str(phrase)] = {"threshold": int(threshold)}

    ignore_phrases = [str(x) for x in ignore_errors if str(x or "").strip()]
    return PayoutRulesData(payout_rules=payout_rules, ignore_phrases=ignore_phrases)


def load_rules_payout_config(rules_path: str | Path | None = None) -> PayoutRulesData | None:
    return PayoutRulesAccessor.from_rules_path(rules_path)


def _resolve_rules_path() -> Path | None:
    try:
        from core.rules_provider import get_rules_snapshot

        snap = get_rules_snapshot(force_sync=False)
        return snap.local_path
    except Exception:
        env_path = (os.getenv("RULES_XLSX_PATH") or "").strip()
        if env_path:
            p = Path(env_path)
            if p.exists():
                return p
        default = Path("rules.xlsx")
        if default.exists():
            return default
        return None


def payout_config_diff(yaml_data: PayoutRulesData, rules_data: PayoutRulesData) -> dict[str, Any]:
    yaml_view = normalize_payout_config_for_compare(yaml_data)
    rules_view = normalize_payout_config_for_compare(rules_data)
    if yaml_view == rules_view:
        return {}

    diff: dict[str, Any] = {}
    if yaml_view["payout_rules"] != rules_view["payout_rules"]:
        diff["payout_rules"] = {
            "yaml": yaml_view["payout_rules"],
            "rules_v2": rules_view["payout_rules"],
        }
    if yaml_view["ignore_phrases"] != rules_view["ignore_phrases"]:
        diff["ignore_phrases"] = {
            "yaml": yaml_view["ignore_phrases"],
            "rules_v2": rules_view["ignore_phrases"],
        }
    return diff


def run_payout_config_shadow_compare(logger, *, yaml_path: str | Path | None = None) -> None:
    """Compare YAML vs Rules V2; log mismatch without affecting runtime."""

    try:
        yaml_data = load_yaml_payout_config(yaml_path)
    except Exception:
        return

    rules_path = _resolve_rules_path()
    if rules_path is None:
        return

    try:
        rules_data = load_rules_payout_config(rules_path)
    except Exception:
        return

    if not payout_rules_data_has_content(rules_data):
        return

    diff = payout_config_diff(yaml_data, rules_data)
    if diff:
        logger.warning("[config_shadow] payout mismatch: %s", diff)


def resolve_payout_config(
    logger,
    *,
    yaml_path: str | Path | None = None,
    rules_path: str | Path | None = None,
) -> tuple[dict[str, dict[str, int]], list[str], str]:
    """Return runtime-normalized config and source label (``yaml`` | ``rules_v2``)."""

    yaml_data = load_yaml_payout_config(yaml_path)
    use_rules = _truthy_env(_ENV_USE_RULES, "0")

    rules_data: PayoutRulesData | None = None
    if rules_path is not None:
        try:
            rules_data = load_rules_payout_config(rules_path)
        except Exception:
            rules_data = None
    else:
        resolved = _resolve_rules_path()
        if resolved is not None:
            try:
                rules_data = load_rules_payout_config(resolved)
            except Exception:
                rules_data = None

    if not use_rules:
        run_payout_config_shadow_compare(logger, yaml_path=yaml_path)
        norm_rules, norm_ignore = normalize_payout_config_for_runtime(yaml_data)
        return norm_rules, norm_ignore, "yaml"

    if payout_rules_data_has_content(rules_data):
        logger.info("[payout_config] source=rules_v2")
        norm_rules, norm_ignore = normalize_payout_config_for_runtime(rules_data)
        return norm_rules, norm_ignore, "rules_v2"

    logger.info("[payout_config] source=yaml")
    norm_rules, norm_ignore = normalize_payout_config_for_runtime(yaml_data)
    return norm_rules, norm_ignore, "yaml"
