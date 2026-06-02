"""Raccoon wallet config resolution from Rules V2 only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analyzers.raccoon_wallet_columns import get_raccoon_payin_column_map
from core.config_manager import get_job_params
from core.rules_v2.raccoon_wallet_rules_accessor import (
    load_groups_cfg_from_rules,
    load_roster_from_rules,
    partners_cfg_from_roster,
)

_JOB_KEY = "raccoon_wallet"

SCALAR_PARAM_KEYS: tuple[str, ...] = (
    "window_minutes",
    "offset_minutes",
    "min_events",
    "pending_payin_minutes",
    "payin_days_back",
)


@dataclass(frozen=True, slots=True)
class RaccoonWalletScalarParams:
    window_minutes: int
    offset_minutes: int
    min_events: int
    pending_payin_minutes: int
    payin_days_back: int

    def as_compare_dict(self) -> dict[str, int]:
        return {
            "window_minutes": self.window_minutes,
            "offset_minutes": self.offset_minutes,
            "min_events": self.min_events,
            "pending_payin_minutes": self.pending_payin_minutes,
            "payin_days_back": self.payin_days_back,
        }


def _parse_rules_scalar_params(raw: dict[str, Any]) -> RaccoonWalletScalarParams | None:
    try:
        values = {key: int(raw[key]) for key in SCALAR_PARAM_KEYS}
    except (KeyError, TypeError, ValueError):
        return None
    return RaccoonWalletScalarParams(**values)


def load_scalars_from_rules(
    rules_path: str | Path | None = None,
    *,
    force_sync: bool = False,
) -> RaccoonWalletScalarParams | None:
    try:
        params = get_job_params(
            rules_xlsx_path=str(rules_path) if rules_path is not None else "",
            job=_JOB_KEY,
            force_sync=force_sync,
        )
    except Exception:
        return None

    if not params:
        return None

    return _parse_rules_scalar_params(params)


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


def scalars_have_content(params: RaccoonWalletScalarParams | None) -> bool:
    return params is not None


def apply_scalars_to_cfg(cfg: dict[str, Any], scalars: RaccoonWalletScalarParams) -> dict[str, Any]:
    out = dict(cfg)
    out["window_minutes"] = scalars.window_minutes
    out["offset_minutes"] = scalars.offset_minutes
    out["min_events"] = scalars.min_events

    pending = dict(out.get("pending_thresholds") or {})
    pending["payin_minutes"] = scalars.pending_payin_minutes
    out["pending_thresholds"] = pending

    download = dict(out.get("download_periods") or {})
    download["payin_days_back"] = scalars.payin_days_back
    out["download_periods"] = download

    return out


def apply_payin_columns_to_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Inject code-constant PayIn column mapping (runtime source of truth)."""

    out = dict(cfg)
    columns = dict(out.get("columns") or {})
    columns["payin"] = get_raccoon_payin_column_map()
    out["columns"] = columns
    return out


def _resolve_rules_path_explicit(rules_path: str | Path | None) -> Path | None:
    if rules_path is not None:
        return Path(rules_path)
    return _resolve_rules_path()


def _load_rules_bundle(
    rules_path: str | Path | None,
) -> tuple[Any, Any, dict[str, Any] | None, Path | None]:
    resolved = _resolve_rules_path_explicit(rules_path)
    if resolved is None:
        return None, None, None, None

    try:
        rules_scalars = load_scalars_from_rules(resolved)
    except Exception:
        rules_scalars = None

    rules_roster = load_roster_from_rules(resolved)
    rules_groups_cfg = load_groups_cfg_from_rules(resolved)

    return rules_scalars, rules_roster, rules_groups_cfg, resolved


def _finalize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return apply_payin_columns_to_cfg(cfg)


def resolve_raccoon_wallet_config(
    logger,
    *,
    rules_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return effective config from Rules V2 (scalars, roster, groups, columns)."""

    rules_scalars, rules_roster, rules_groups_cfg, _resolved = _load_rules_bundle(rules_path)

    if not scalars_have_content(rules_scalars):
        raise RuntimeError("raccoon_wallet_config: rules scalars incomplete")

    if rules_roster is None or not rules_roster.norm_to_display:
        raise RuntimeError("raccoon_wallet_config: rules roster empty or unavailable")

    if rules_groups_cfg is None:
        raise RuntimeError("raccoon_wallet_config: rules groups unavailable")

    cfg = apply_scalars_to_cfg({}, rules_scalars)
    cfg["partners"] = partners_cfg_from_roster(rules_roster)
    cfg["groups"] = rules_groups_cfg

    logger.info("[raccoon_wallet_config] source=rules_v2")
    return _finalize_config(cfg)
