# analyzers/wallet_analyzer.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.config_manager import (
    get_partner_groups_df,
    get_wallet_limits_df,
)
from utils.normalization import normalize_partner_name


# =============================================================================
# DTOs
# =============================================================================

@dataclass(frozen=True)
class LimitResolved:
    status: str                  # ACTIVE | STOP | MISSING
    limit_value: Optional[float]
    scope: Optional[str]         # partner | group
    scope_value: Optional[str]   # partner name or group name
    method: Optional[str]        # UNI/BST or None (default)
    rule_id: Optional[str]
    comment: str = ""
    reason: str = ""


@dataclass(frozen=True)
class WalletMethodRow:
    method: str                  # UNI/BST
    amount: float                # sum(amount) for the day
    limit: LimitResolved


@dataclass(frozen=True)
class WalletPartnerBlock:
    partner: str
    group: Optional[str]
    rows: List[WalletMethodRow]


@dataclass(frozen=True)
class WalletDTO:
    report_day: date
    blocks: List[WalletPartnerBlock]


# =============================================================================
# Resolver (wallet_limits)
# =============================================================================

def _parse_analyzers_cell(s: str) -> List[str]:
    parts = [p.strip().lower() for p in str(s or "").split(",")]
    return sorted(set(p for p in parts if p))


def _build_partner_group_map(pg_df: pd.DataFrame, *, analyzer: str) -> Dict[str, str]:
    """
    Returns: partner_norm -> group_name
    """
    df = pg_df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    # expect validator already ran in config_manager; still be defensive
    df = df[df["enabled"] == 1].copy()
    df["_analyzers_list"] = df["analyzers"].map(_parse_analyzers_cell)
    df = df[df["_analyzers_list"].map(lambda xs: analyzer.lower() in xs)]
    df["partner_norm"] = df["partner"].map(normalize_partner_name)
    df["group_name_norm"] = df["group_name"].astype(str).str.strip().str.lower()

    res: Dict[str, str] = {}
    # if duplicates exist, keep first but better to have validator catch it
    for _, r in df.iterrows():
        pn = r["partner_norm"]
        gn = r["group_name_norm"]
        if pn and gn and pn not in res:
            res[pn] = gn
    return res


def _build_partner_default_method_map(pg_df: pd.DataFrame, *, analyzer: str) -> Dict[str, str]:
    """
    Returns: partner_norm -> default_method (e.g. UNI/BST)

    Rules:
    - Reads optional column 'default_method' from partner_groups sheet.
    - Applies only enabled=1 rows and matching analyzer.
    - Values normalized to upper-case.
    """
    df = pg_df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    if "default_method" not in df.columns:
        return {}

    df = df[df["enabled"] == 1].copy()
    df["_analyzers_list"] = df["analyzers"].map(_parse_analyzers_cell)
    df = df[df["_analyzers_list"].map(lambda xs: analyzer.lower() in xs)]
    df["partner_norm"] = df["partner"].map(normalize_partner_name)
    df["default_method_norm"] = df["default_method"].astype(str).fillna("").str.strip().str.upper()

    res: Dict[str, str] = {}
    for _, r in df.iterrows():
        pn = r["partner_norm"]
        dm = r["default_method_norm"]
        if pn and dm and pn not in res:
            res[pn] = dm
    return res

def resolve_wallet_limit(
    limits_df: pd.DataFrame,
    *,
    analyzer: str,
    partner: str,
    group: Optional[str],
    method: str,
    limit_type: str = "daily_max_amount",
) -> LimitResolved:
    """
    Canonical contract:
    - source of truth: rules.xlsx / wallet_limits
    - limit_value == 0 => STOP (aggregate still calculated, but no %/breach)
    - precedence:
        1) partner + method
        2) partner + default (method empty)
        3) group + method
        4) group + default
    - enabled=1 and limit_value NaN must be FATAL earlier in validation
    """
    df = limits_df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    analyzer = analyzer.lower().strip()
    limit_type = limit_type.lower().strip()
    method = (method or "").strip().upper()
    partner_norm = normalize_partner_name(partner)
    group_norm = (group or "").strip().lower() or None

    # filter active + analyzer + limit_type
    df = df[df["enabled"] == 1].copy()
    df["_analyzers_list"] = df["analyzers"].map(_parse_analyzers_cell)
    df = df[df["_analyzers_list"].map(lambda xs: analyzer in xs)]
    df = df[df["limit_type"].astype(str).str.strip().str.lower() == limit_type].copy()

    # normalize keys
    df["scope"] = df["scope"].astype(str).str.strip().str.lower()
    df["method_norm"] = df.get("method", "").astype(str).fillna("").str.strip().str.upper()
    df["scope_value_norm"] = df["scope_value"].astype(str).str.strip()

    is_partner = df["scope"] == "partner"
    df.loc[is_partner, "scope_value_norm"] = df.loc[is_partner, "scope_value_norm"].map(normalize_partner_name)
    df.loc[~is_partner, "scope_value_norm"] = df.loc[~is_partner, "scope_value_norm"].str.lower()

    def _pick(scope: str, scope_value_norm: str, method_norm: str) -> Optional[pd.Series]:
        m = (df["scope"] == scope) & (df["scope_value_norm"] == scope_value_norm) & (df["method_norm"] == method_norm)
        sub = df[m]
        if sub.empty:
            return None
        # duplicates should have been caught by validate_wallet_limits (fatal)
        return sub.iloc[0]

    # candidate keys
    cand: List[Tuple[str, str, str]] = [
        ("partner", partner_norm, method),
        ("partner", partner_norm, ""),  # default
    ]
    if group_norm:
        cand += [
            ("group", group_norm, method),
            ("group", group_norm, ""),
        ]

    for scope, scope_value_norm, m_norm in cand:
        row = _pick(scope, scope_value_norm, m_norm)
        if row is None:
            continue

        lv = float(row["limit_value"])
        comment = str(row.get("comment", "") or "").strip()
        reason = str(row.get("reason", "") or "").strip()
        rule_id = str(row.get("id", "") or "").strip() or None

        if lv == 0:
            return LimitResolved(
                status="STOP",
                limit_value=0.0,
                scope=scope,
                scope_value=row["scope_value"],
                method=m_norm or None,
                rule_id=rule_id,
                comment=comment,
                reason=reason,
            )

        return LimitResolved(
            status="ACTIVE",
            limit_value=lv,
            scope=scope,
            scope_value=row["scope_value"],
            method=m_norm or None,
            rule_id=rule_id,
            comment=comment,
            reason=reason,
        )

    return LimitResolved(
        status="MISSING",
        limit_value=None,
        scope=None,
        scope_value=None,
        method=None,
        rule_id=None,
        comment="",
        reason="",
    )


# =============================================================================
# Data extraction (payout daily sums)
# =============================================================================

def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    # Exact match first (case-insensitive), then substring match (case-insensitive).
    # Substring match is needed for columns like "enum метод".
    cols_norm = [(str(c).strip().lower(), c) for c in df.columns]

    # 1) exact
    cols_exact = {k: orig for k, orig in cols_norm}
    for cand in candidates:
        key = cand.strip().lower()
        if key in cols_exact:
            return cols_exact[key]

    # 2) substring (stable: preserves df.columns order)
    cand_norm = [c.strip().lower() for c in candidates]
    for k, orig in cols_norm:
        for cand in cand_norm:
            if cand and cand in k:
                return orig

    return None


def _parse_dt_series(s: pd.Series) -> pd.Series:
    # handle excel datetimes or text
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


def _parse_amount_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    # replace comma decimal and spaces
    return pd.to_numeric(
        s.astype(str)
         .str.replace(" ", "", regex=False)
         .str.replace("\u00a0", "", regex=False)
         .str.replace(",", ".", regex=False),
        errors="coerce",
    ).astype(float)


def build_wallet_dto_from_payout_xlsx(
    payout_path: str,
    *,
    analyzer: str = "wallet",
    rules_force_sync: bool = False,
    report_day: Optional[date] = None,
) -> WalletDTO:
    """
    Rule-driven wallet DTO builder for daily payouts.

    Reads:
      - rules.xlsx / partner_groups
      - rules.xlsx / wallet_limits

    Output:
      WalletDTO(report_day=..., blocks=[...])

    Notes:
      - STOP (limit_value==0) is shown, but does not participate in %/breach.
      - This function does NOT send Telegram and does NOT format text.
    """
    if report_day is None:
        report_day = datetime.now().date()

    # rules
    pg_df = get_partner_groups_df(force_sync=rules_force_sync)
    limits_df = get_wallet_limits_df(force_sync=rules_force_sync)
    partner_to_group = _build_partner_group_map(pg_df, analyzer=analyzer)
    partner_default_method = _build_partner_default_method_map(pg_df, analyzer=analyzer)

    # payout data
    df = pd.read_excel(payout_path)
    dt_col = _find_col(df, ["date", "datetime", "created_at", "дата", "дата/время", "дата/время создания", "дата создания"])
    partner_col = _find_col(df, ["partner", "партнер", "партнёр"])
    amount_col = _find_col(df, ["amount", "сумма", "sum", "итого"])
    method_col = _find_col(df, ["enum метод", "method", "метод", "пул", "канал"])

    if partner_col is None or amount_col is None:
        raise RuntimeError("payout file: missing required columns (partner, amount)")

    if dt_col is not None:
        df["_dt"] = _parse_dt_series(df[dt_col])
        df = df[df["_dt"].dt.date == report_day].copy()
    else:
        # no datetime column => assume file already for the day
        df = df.copy()

    df["_partner"] = df[partner_col].astype(str)
    df["_partner_norm"] = df["_partner"].map(normalize_partner_name)
    df["_amount"] = _parse_amount_series(df[amount_col]).fillna(0.0)

    if method_col is None:
        df["_method"] = ""
    else:
        df["_method"] = df[method_col].astype(str).fillna("").str.strip().str.upper()

    # Fill empty method from rules (partner_groups.default_method).
    if "partner_default_method" in locals():
        defaults = df["_partner_norm"].map(partner_default_method).fillna("")
        m = df["_method"].eq("") & defaults.ne("")
        if m.any():
            df.loc[m, "_method"] = defaults.loc[m]

    # Fail-fast: method must be resolved to UNI/BST (or explicit default) for every partner row.
    bad = df["_method"].eq("")
    if bad.any():
        bad_partners = (
            df.loc[bad, "_partner"].astype(str).dropna().unique().tolist()
        )
        raise RuntimeError(
            "payout file: enum method empty and no default_method in rules for partners: "
            + ", ".join(bad_partners[:20])
            + (" …" if len(bad_partners) > 20 else "")
        )

    # aggregate
    g = (
        df.groupby(["_partner_norm", "_method"], dropna=False)["_amount"]
        .sum()
        .reset_index()
        .sort_values(["_partner_norm", "_method"])
    )

    blocks: List[WalletPartnerBlock] = []
    for partner_norm, sub in g.groupby("_partner_norm"):
        if not partner_norm:
            continue
        # use original display name: pick first matching row
        display_partner = (
            df.loc[df["_partner_norm"] == partner_norm, "_partner"].astype(str).iloc[0]
            if (df["_partner_norm"] == partner_norm).any()
            else partner_norm
        )

        group_name = partner_to_group.get(partner_norm)
        rows: List[WalletMethodRow] = []

        for _, r in sub.iterrows():
            m = (r["_method"] or "").strip().upper() or "UNI"  # default display
            amount = float(r["_amount"] or 0.0)

            lim = resolve_wallet_limit(
                limits_df,
                analyzer=analyzer,
                partner=display_partner,
                group=group_name,
                method=m,
                limit_type="daily_max_amount",
            )

            rows.append(WalletMethodRow(method=m, amount=amount, limit=lim))

        blocks.append(WalletPartnerBlock(partner=display_partner, group=group_name, rows=rows))

    return WalletDTO(report_day=report_day, blocks=blocks)
