"""Raccoon wallet config resolution: YAML legacy + Rules V2 job_params (Phase 3A scalars)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.config_manager import get_job_params
from analyzers.raccoon_wallet_columns import (
    extract_payin_columns_from_yaml,
    get_raccoon_payin_column_map,
    payin_columns_diff,
)
from core.rules_v2.raccoon_wallet_rules_accessor import (
    RaccoonWalletRoster,
    build_groups_from_yaml,
    build_roster_from_yaml,
    load_groups_cfg_from_rules,
    load_roster_from_rules,
    load_groups_from_rules,
    groups_diff,
    partners_cfg_from_roster,
    roster_covers_yaml,
    roster_diff,
)

DEFAULT_YAML_PATH = Path(__file__).resolve().parent.parent / "config" / "raccoon_wallet_config.yaml"

_ENV_USE_RULES = "RACCOON_WALLET_CONFIG_FROM_RULES_V2"
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


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def try_load_legacy_yaml_config(path: str | Path | None = None) -> dict[str, Any] | None:
    """Load legacy YAML; return None if file missing or unreadable."""

    config_path = Path(path) if path is not None else DEFAULT_YAML_PATH
    if not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return None

    cfg.setdefault("window_minutes", 8)
    cfg.setdefault("offset_minutes", 8)
    cfg.setdefault("min_events", 10)
    cfg.setdefault("partners", {})
    cfg.setdefault("groups", {})

    return cfg


def load_legacy_yaml_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load full legacy YAML (partners, groups, columns, scalars)."""

    cfg = try_load_legacy_yaml_config(path)
    if cfg is None:
        raise RuntimeError("raccoon_wallet_config.yaml не найден")
    return cfg


def extract_scalars_from_yaml(cfg: dict[str, Any]) -> RaccoonWalletScalarParams:
    """Extract Phase 3A scalars using the same defaults as legacy runtime."""

    pending = cfg.get("pending_thresholds") or {}
    download = cfg.get("download_periods") or {}

    return RaccoonWalletScalarParams(
        window_minutes=int(cfg.get("window_minutes", 8)),
        offset_minutes=int(cfg.get("offset_minutes", 8)),
        min_events=int(cfg.get("min_events", 10)),
        pending_payin_minutes=int(pending.get("payin_minutes", 10)),
        payin_days_back=int(download.get("payin_days_back", 2)),
    )


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


def scalar_params_diff(
    yaml_params: RaccoonWalletScalarParams,
    rules_params: RaccoonWalletScalarParams,
) -> dict[str, dict[str, int]]:
    yaml_view = yaml_params.as_compare_dict()
    rules_view = rules_params.as_compare_dict()
    if yaml_view == rules_view:
        return {}

    diff: dict[str, dict[str, int]] = {}
    for key in SCALAR_PARAM_KEYS:
        if yaml_view[key] != rules_view[key]:
            diff[key] = {"yaml": yaml_view[key], "rules_v2": rules_view[key]}
    return diff


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


def run_raccoon_wallet_config_shadow_compare(
    logger,
    *,
    yaml_path: str | Path | None = None,
    rules_path: str | Path | None = None,
) -> None:
    """Compare YAML vs Rules V2 scalar params (Phase 3A)."""

    try:
        legacy = load_legacy_yaml_config(yaml_path)
        yaml_scalars = extract_scalars_from_yaml(legacy)
    except Exception:
        return

    resolved_rules = Path(rules_path) if rules_path is not None else _resolve_rules_path()
    if resolved_rules is None:
        return

    try:
        rules_scalars = load_scalars_from_rules(resolved_rules)
    except Exception:
        return

    if not scalars_have_content(rules_scalars):
        return

    diff = scalar_params_diff(yaml_scalars, rules_scalars)
    if diff:
        logger.warning("[config_shadow] raccoon_wallet mismatch: %s", diff)


def run_raccoon_wallet_roster_shadow_compare(
    logger,
    *,
    yaml_path: str | Path | None = None,
    rules_path: str | Path | None = None,
) -> None:
    """Compare YAML vs Rules V2 partner roster (Phase 3B-1). Never affects runtime."""

    try:
        legacy = load_legacy_yaml_config(yaml_path)
        yaml_roster = build_roster_from_yaml(legacy)
    except Exception:
        return

    resolved_rules = Path(rules_path) if rules_path is not None else _resolve_rules_path()
    if resolved_rules is None:
        return

    rules_roster = load_roster_from_rules(resolved_rules)
    if rules_roster is None:
        return

    diff = roster_diff(yaml_roster, rules_roster)
    if not diff:
        return

    logger.warning("[config_shadow] raccoon_wallet roster mismatch: %s", diff)
    if diff.get("yaml_only"):
        logger.warning(
            "[config_shadow] raccoon_wallet roster blocker: yaml_only partners "
            "must be covered in rules before YAML removal: %s",
            diff.get("yaml_only"),
        )


def run_raccoon_wallet_groups_shadow_compare(
    logger,
    *,
    yaml_path: str | Path | None = None,
    rules_path: str | Path | None = None,
) -> None:
    """Compare YAML vs Rules V2 group membership (Phase 3B-3). Never affects runtime."""

    try:
        legacy = load_legacy_yaml_config(yaml_path)
        yaml_groups = build_groups_from_yaml(legacy)
    except Exception:
        return

    resolved_rules = Path(rules_path) if rules_path is not None else _resolve_rules_path()
    if resolved_rules is None:
        return

    rules_groups = load_groups_from_rules(resolved_rules)
    if rules_groups is None:
        return

    diff = groups_diff(yaml_groups, rules_groups)
    if not diff:
        return

    logger.warning("[config_shadow] raccoon_wallet groups mismatch: %s", diff)
    if diff.get("yaml_only_groups"):
        logger.warning(
            "[config_shadow] raccoon_wallet groups blocker: yaml_only groups "
            "must be covered in rules before YAML removal: %s",
            diff.get("yaml_only_groups"),
        )


def apply_payin_columns_to_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Inject code-constant PayIn column mapping (runtime source of truth)."""

    out = dict(cfg)
    columns = dict(out.get("columns") or {})
    columns["payin"] = get_raccoon_payin_column_map()
    out["columns"] = columns
    return out


def run_raccoon_payin_columns_shadow_compare(
    logger,
    *,
    yaml_path: str | Path | None = None,
) -> None:
    """Compare YAML columns.payin vs code constants; log warning on diff."""

    try:
        legacy = load_legacy_yaml_config(yaml_path)
        yaml_map = extract_payin_columns_from_yaml(legacy)
    except Exception:
        return

    code_map = get_raccoon_payin_column_map()
    diff = payin_columns_diff(yaml_map, code_map)
    if diff:
        logger.warning("[config_shadow] raccoon_wallet columns mismatch: %s", diff)


def _resolve_rules_path_explicit(rules_path: str | Path | None) -> Path | None:
    if rules_path is not None:
        return Path(rules_path)
    return _resolve_rules_path()


def _load_rules_bundle(
    rules_path: str | Path | None,
) -> tuple[
    RaccoonWalletScalarParams | None,
    RaccoonWalletRoster | None,
    dict[str, Any] | None,
    Path | None,
]:
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


def _log_config_sources(logger, sources: dict[str, str], *, yaml_reason: str | None = None) -> None:
    rules_fields = sorted(k for k, v in sources.items() if v == "rules_v2")
    yaml_fields = sorted(k for k, v in sources.items() if v == "yaml")

    if rules_fields and not yaml_fields:
        logger.info(
            "[raccoon_wallet_config] source=rules_v2 fields=%s",
            ",".join(rules_fields),
        )
        return

    if yaml_fields and not rules_fields:
        reason = yaml_reason or "all_fields_fallback"
        logger.info("[raccoon_wallet_config] source=yaml reason=%s", reason)
        return

    mixed = ",".join(f"{k}={v}" for k, v in sorted(sources.items()))
    logger.info("[raccoon_wallet_config] source=mixed fields=%s", mixed)


def _resolve_yaml_primary_config(
    logger,
    legacy: dict[str, Any],
    yaml_scalars: RaccoonWalletScalarParams,
    *,
    yaml_path: str | Path | None,
    rules_path: str | Path | None,
) -> dict[str, Any]:
    run_raccoon_wallet_config_shadow_compare(logger, yaml_path=yaml_path, rules_path=rules_path)
    _log_config_sources(logger, {"scalars": "yaml", "roster": "yaml", "groups": "yaml"})
    cfg = apply_scalars_to_cfg(legacy, yaml_scalars)
    return _finalize_config(cfg)


def _resolve_rules_primary_config(
    logger,
    *,
    legacy: dict[str, Any] | None,
    yaml_roster: RaccoonWalletRoster,
    yaml_scalars: RaccoonWalletScalarParams | None,
    rules_scalars: RaccoonWalletScalarParams | None,
    rules_roster: RaccoonWalletRoster | None,
    rules_groups_cfg: dict[str, Any] | None,
) -> dict[str, Any]:
    sources: dict[str, str] = {}
    yaml_reason: str | None = None

    if scalars_have_content(rules_scalars):
        effective_scalars = rules_scalars
        sources["scalars"] = "rules_v2"
    elif legacy is not None and yaml_scalars is not None:
        effective_scalars = yaml_scalars
        sources["scalars"] = "yaml"
        yaml_reason = yaml_reason or "rules_scalars_missing_or_incomplete"
        logger.info(
            "[raccoon_wallet_config] source=yaml reason=rules_scalars_missing_or_incomplete",
        )
    else:
        raise RuntimeError("raccoon_wallet_config: rules scalars incomplete and YAML unavailable")

    if (
        rules_roster is not None
        and roster_covers_yaml(yaml_roster, rules_roster)
    ):
        partners_cfg = partners_cfg_from_roster(rules_roster)
        sources["roster"] = "rules_v2"
    elif legacy is not None:
        partners_cfg = dict(legacy.get("partners") or {})
        sources["roster"] = "yaml"
        yaml_reason = yaml_reason or "rules_roster_missing_or_incomplete"
        logger.info(
            "[raccoon_wallet_config] source=yaml reason=rules_roster_missing_or_incomplete",
        )
    elif rules_roster is not None and rules_roster.norm_to_display:
        partners_cfg = partners_cfg_from_roster(rules_roster)
        sources["roster"] = "rules_v2"
    else:
        raise RuntimeError("raccoon_wallet_config: rules roster empty and YAML unavailable")

    if rules_groups_cfg is not None:
        groups_cfg = rules_groups_cfg
        sources["groups"] = "rules_v2"
    elif legacy is not None:
        groups_cfg = dict(legacy.get("groups") or {})
        sources["groups"] = "yaml"
        if yaml_reason is None:
            yaml_reason = "rules_groups_unavailable"
        logger.info(
            "[raccoon_wallet_config] source=yaml reason=rules_groups_unavailable",
        )
    else:
        groups_cfg = {}
        sources["groups"] = "rules_v2"

    base_cfg = dict(legacy) if legacy is not None else {}
    cfg = apply_scalars_to_cfg(base_cfg, effective_scalars)
    cfg["partners"] = partners_cfg
    cfg["groups"] = groups_cfg

    _log_config_sources(logger, sources, yaml_reason=yaml_reason)
    return _finalize_config(cfg)


def _finalize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return apply_payin_columns_to_cfg(cfg)


def resolve_raccoon_wallet_config(
    logger,
    *,
    yaml_path: str | Path | None = None,
    rules_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return effective config: YAML or Rules V2 primary per env flag, with fallback."""

    use_rules = _truthy_env(_ENV_USE_RULES, "0")

    if use_rules:
        legacy = try_load_legacy_yaml_config(yaml_path)
    else:
        legacy = load_legacy_yaml_config(yaml_path)

    yaml_scalars = extract_scalars_from_yaml(legacy) if legacy is not None else None
    yaml_roster = build_roster_from_yaml(legacy) if legacy is not None else RaccoonWalletRoster({})

    # Shadow compares (always when YAML available for baseline).
    if legacy is not None:
        run_raccoon_wallet_roster_shadow_compare(logger, yaml_path=yaml_path, rules_path=rules_path)
        run_raccoon_payin_columns_shadow_compare(logger, yaml_path=yaml_path)
        run_raccoon_wallet_groups_shadow_compare(logger, yaml_path=yaml_path, rules_path=rules_path)

    rules_scalars, rules_roster, rules_groups_cfg, _resolved = _load_rules_bundle(rules_path)

    if not use_rules:
        assert legacy is not None
        assert yaml_scalars is not None
        return _resolve_yaml_primary_config(
            logger,
            legacy,
            yaml_scalars,
            yaml_path=yaml_path,
            rules_path=rules_path,
        )

    return _resolve_rules_primary_config(
        logger,
        legacy=legacy,
        yaml_roster=yaml_roster,
        yaml_scalars=yaml_scalars,
        rules_scalars=rules_scalars,
        rules_roster=rules_roster,
        rules_groups_cfg=rules_groups_cfg,
    )
