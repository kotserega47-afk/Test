"""Rules V2 accessors for Raccoon Wallet config migration (Phase 3B-1 roster)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.rules_v2.loader import read_rules_excel
from utils.normalization import normalize_partner_name

ANALYZER_KEY = "raccoon_wallet"

SHEET_THRESHOLDS = "thresholds_partner"
SHEET_WALLET_LIMITS = "wallet_limits"
SHEET_PARTNER_GROUPS = "partner_groups"

# group_name (normalized) -> sorted normalized partner keys
RaccoonWalletGroupsMembership = dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class RaccoonWalletRoster:
    """Partner roster: normalized key → display name (Excel/YAML label)."""

    norm_to_display: dict[str, str]

    @property
    def normalized_keys(self) -> frozenset[str]:
        return frozenset(self.norm_to_display.keys())

    def sorted_normalized_keys(self) -> list[str]:
        return sorted(self.norm_to_display.keys())

    def sorted_display_names(self) -> list[str]:
        return [self.norm_to_display[k] for k in self.sorted_normalized_keys()]


def _parse_analyzers_csv(value: Any) -> list[str]:
    return sorted(
        {
            part.strip().lower()
            for part in str(value or "").split(",")
            if part.strip()
        }
    )


def _add_partner(roster: dict[str, str], display_name: str) -> None:
    name = str(display_name or "").strip()
    if not name:
        return
    norm = normalize_partner_name(name)
    if not norm:
        return
    roster.setdefault(norm, name)


def build_roster_from_yaml(cfg: dict[str, Any]) -> RaccoonWalletRoster:
    partners = cfg.get("partners") or {}
    roster: dict[str, str] = {}
    if isinstance(partners, dict):
        for display_name in partners.keys():
            _add_partner(roster, str(display_name))
    return RaccoonWalletRoster(norm_to_display=roster)


def _enabled_series(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df.get("enabled"), errors="coerce").fillna(0).astype(int).eq(1)


def _partners_from_thresholds(df: pd.DataFrame | None, roster: dict[str, str]) -> None:
    if df is None or df.empty:
        return

    frame = df.copy()
    frame["enabled"] = pd.to_numeric(frame.get("enabled"), errors="coerce").fillna(0).astype(int)
    frame["analyzer"] = frame.get("analyzer").astype(str).str.strip().str.lower()
    frame["partner"] = frame.get("partner").astype(str).str.strip()

    active = frame[(frame["enabled"] == 1) & (frame["analyzer"] == ANALYZER_KEY)]
    for partner in active["partner"]:
        _add_partner(roster, partner)


def _partners_from_wallet_limits(df: pd.DataFrame | None, roster: dict[str, str]) -> None:
    if df is None or df.empty:
        return

    if "analyzers" not in df.columns:
        return

    frame = df.copy()
    frame["enabled"] = pd.to_numeric(frame.get("enabled"), errors="coerce").fillna(0).astype(int)
    frame["analyzers"] = frame.get("analyzers").astype(str).str.strip()
    frame["_analyzers_list"] = frame["analyzers"].apply(_parse_analyzers_csv)
    frame["scope"] = frame.get("scope").astype(str).str.strip().str.lower()
    frame["scope_value"] = frame.get("scope_value").astype(str).str.strip()

    active = frame[
        (frame["enabled"] == 1)
        & (frame["_analyzers_list"].map(lambda lst: ANALYZER_KEY in lst))
        & (frame["scope"] == "partner")
    ]
    for partner in active["scope_value"]:
        _add_partner(roster, partner)


def _partners_from_partner_groups(df: pd.DataFrame | None, roster: dict[str, str]) -> None:
    if df is None or df.empty:
        return

    if "analyzers" not in df.columns:
        return

    frame = df.copy()
    frame["enabled"] = pd.to_numeric(frame.get("enabled"), errors="coerce").fillna(0).astype(int)
    frame["analyzers"] = frame.get("analyzers").astype(str).str.strip()
    frame["_analyzers_list"] = frame["analyzers"].apply(_parse_analyzers_csv)
    frame["partner"] = frame.get("partner").astype(str).str.strip()

    active = frame[
        (frame["enabled"] == 1)
        & (frame["_analyzers_list"].map(lambda lst: ANALYZER_KEY in lst))
    ]
    for partner in active["partner"]:
        _add_partner(roster, partner)


def build_roster_from_sheets(sheets: dict[str, pd.DataFrame]) -> RaccoonWalletRoster:
    roster: dict[str, str] = {}
    _partners_from_thresholds(sheets.get(SHEET_THRESHOLDS), roster)
    _partners_from_wallet_limits(sheets.get(SHEET_WALLET_LIMITS), roster)
    _partners_from_partner_groups(sheets.get(SHEET_PARTNER_GROUPS), roster)
    return RaccoonWalletRoster(norm_to_display=roster)


def load_roster_from_rules(
    rules_path: str | Path | None = None,
) -> RaccoonWalletRoster | None:
    """Load roster union from rules workbook sheets. Returns None if workbook unreadable."""

    try:
        sheets = read_rules_excel(
            rules_path,
            only_sheets=(SHEET_THRESHOLDS, SHEET_WALLET_LIMITS, SHEET_PARTNER_GROUPS),
        )
    except Exception:
        return None

    return build_roster_from_sheets(sheets)


def _normalize_group_name(name: str) -> str:
    return str(name or "").strip().lower()


def _sorted_partner_norms(partners: Any) -> tuple[str, ...]:
    if not partners:
        return ()
    norms = {
        normalize_partner_name(str(p))
        for p in partners
        if str(p).strip() and normalize_partner_name(str(p))
    }
    return tuple(sorted(norms))


def build_groups_from_yaml(cfg: dict[str, Any]) -> RaccoonWalletGroupsMembership:
    """YAML groups membership: group_name -> sorted normalized partner keys."""

    groups_raw = cfg.get("groups") or {}
    membership: RaccoonWalletGroupsMembership = {}
    if not isinstance(groups_raw, dict):
        return membership

    for gname, gdata in groups_raw.items():
        group_key = _normalize_group_name(str(gname))
        if not group_key:
            continue
        partners = ()
        if isinstance(gdata, dict):
            partners = _sorted_partner_norms(gdata.get("partners"))
        membership[group_key] = partners

    return membership


def _groups_from_partner_groups_sheet(
    df: pd.DataFrame | None,
) -> RaccoonWalletGroupsMembership:
    if df is None or df.empty:
        return {}

    if "analyzers" not in df.columns:
        return {}

    frame = df.copy()
    frame["enabled"] = pd.to_numeric(frame.get("enabled"), errors="coerce").fillna(0).astype(int)
    frame["analyzers"] = frame.get("analyzers").astype(str).str.strip()
    frame["_analyzers_list"] = frame["analyzers"].apply(_parse_analyzers_csv)
    frame["group_name"] = frame.get("group_name").astype(str).str.strip()
    frame["partner"] = frame.get("partner").astype(str).str.strip()

    active = frame[
        (frame["enabled"] == 1)
        & (frame["_analyzers_list"].map(lambda lst: ANALYZER_KEY in lst))
    ]

    membership: dict[str, set[str]] = {}
    for _, row in active.iterrows():
        group_key = _normalize_group_name(row["group_name"])
        if not group_key:
            continue
        pnorm = normalize_partner_name(row["partner"])
        if not pnorm:
            continue
        membership.setdefault(group_key, set()).add(pnorm)

    return {k: tuple(sorted(v)) for k, v in sorted(membership.items())}


def build_groups_from_sheets(sheets: dict[str, pd.DataFrame]) -> RaccoonWalletGroupsMembership:
    return _groups_from_partner_groups_sheet(sheets.get(SHEET_PARTNER_GROUPS))


def load_groups_from_rules(
    rules_path: str | Path | None = None,
) -> RaccoonWalletGroupsMembership | None:
    """Load group membership from partner_groups sheet. Returns None if unreadable."""

    try:
        sheets = read_rules_excel(
            rules_path,
            only_sheets=(SHEET_PARTNER_GROUPS,),
        )
    except Exception:
        return None

    return build_groups_from_sheets(sheets)


def roster_covers_yaml(
    yaml_roster: RaccoonWalletRoster,
    rules_roster: RaccoonWalletRoster,
) -> bool:
    """True when Rules roster is non-empty and covers every YAML partner."""

    if not rules_roster.norm_to_display:
        return False
    return yaml_roster.normalized_keys <= rules_roster.normalized_keys


def partners_cfg_from_roster(roster: RaccoonWalletRoster) -> dict[str, dict[str, Any]]:
    """Runtime cfg partners dict: display name -> settings (empty dict per partner)."""

    return {display: {} for display in roster.sorted_display_names()}


def build_groups_cfg_from_sheets(sheets: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Runtime cfg groups dict: group display name -> {partners: [display names]}."""

    df = sheets.get(SHEET_PARTNER_GROUPS)
    if df is None or df.empty or "analyzers" not in df.columns:
        return {}

    frame = df.copy()
    frame["enabled"] = pd.to_numeric(frame.get("enabled"), errors="coerce").fillna(0).astype(int)
    frame["analyzers"] = frame.get("analyzers").astype(str).str.strip()
    frame["_analyzers_list"] = frame["analyzers"].apply(_parse_analyzers_csv)
    frame["group_name"] = frame.get("group_name").astype(str).str.strip()
    frame["partner"] = frame.get("partner").astype(str).str.strip()

    active = frame[
        (frame["enabled"] == 1)
        & (frame["_analyzers_list"].map(lambda lst: ANALYZER_KEY in lst))
    ]

    group_meta: dict[str, dict[str, Any]] = {}
    for _, row in active.iterrows():
        display_gname = str(row["group_name"]).strip()
        gkey = _normalize_group_name(display_gname)
        partner_display = str(row["partner"]).strip()
        if not gkey or not partner_display:
            continue
        if gkey not in group_meta:
            group_meta[gkey] = {"display_name": display_gname, "partners": []}
        partners_list: list[str] = group_meta[gkey]["partners"]
        if partner_display not in partners_list:
            partners_list.append(partner_display)

    return {
        str(meta["display_name"]): {"partners": list(meta["partners"])}
        for meta in group_meta.values()
    }


def load_groups_cfg_from_rules(
    rules_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load runtime groups cfg from partner_groups. None if workbook unreadable."""

    try:
        sheets = read_rules_excel(
            rules_path,
            only_sheets=(SHEET_PARTNER_GROUPS,),
        )
    except Exception:
        return None

    return build_groups_cfg_from_sheets(sheets)


def groups_diff(
    yaml_groups: RaccoonWalletGroupsMembership,
    rules_groups: RaccoonWalletGroupsMembership,
) -> dict[str, Any]:
    """Compare YAML vs Rules V2 group membership; empty dict if identical."""

    yaml_keys = set(yaml_groups.keys())
    rules_keys = set(rules_groups.keys())

    yaml_only_groups = sorted(yaml_keys - rules_keys)
    rules_only_groups = sorted(rules_keys - yaml_keys)

    membership_diff: dict[str, dict[str, list[str]]] = {}
    for group_key in sorted(yaml_keys & rules_keys):
        yaml_members = yaml_groups[group_key]
        rules_members = rules_groups[group_key]
        if yaml_members == rules_members:
            continue
        yaml_only = sorted(set(yaml_members) - set(rules_members))
        rules_only = sorted(set(rules_members) - set(yaml_members))
        membership_diff[group_key] = {
            "yaml_only": yaml_only,
            "rules_only": rules_only,
        }

    if not yaml_only_groups and not rules_only_groups and not membership_diff:
        return {}

    return {
        "yaml_only_groups": yaml_only_groups,
        "rules_only_groups": rules_only_groups,
        "membership_diff": membership_diff,
    }


def roster_diff(
    yaml_roster: RaccoonWalletRoster,
    rules_roster: RaccoonWalletRoster,
) -> dict[str, Any]:
    yaml_norms = yaml_roster.normalized_keys
    rules_norms = rules_roster.normalized_keys

    yaml_only = sorted(yaml_norms - rules_norms)
    rules_only = sorted(rules_norms - yaml_norms)

    if not yaml_only and not rules_only:
        return {}

    return {
        "yaml_only": yaml_only,
        "rules_only": rules_only,
        "yaml_count": len(yaml_norms),
        "rules_count": len(rules_norms),
        "yaml_display": {k: yaml_roster.norm_to_display[k] for k in yaml_only},
        "rules_display": {k: rules_roster.norm_to_display[k] for k in rules_only},
    }


__all__ = [
    "ANALYZER_KEY",
    "RaccoonWalletGroupsMembership",
    "RaccoonWalletRoster",
    "build_groups_cfg_from_sheets",
    "build_groups_from_sheets",
    "build_groups_from_yaml",
    "build_roster_from_sheets",
    "build_roster_from_yaml",
    "groups_diff",
    "load_groups_cfg_from_rules",
    "load_groups_from_rules",
    "load_roster_from_rules",
    "partners_cfg_from_roster",
    "roster_covers_yaml",
    "roster_diff",
]
