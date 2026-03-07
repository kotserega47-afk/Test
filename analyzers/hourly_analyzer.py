# analyzers/hourly_analyzer.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
from zoneinfo import ZoneInfo

from core.config_manager import get_wallet_limits_df, get_partner_groups_df, get_job_params
from utils.normalization import normalize_partner_name, parse_dt_series_msk


MSK = ZoneInfo("Europe/Moscow")


# =============================================================================
# DTO
# =============================================================================

@dataclass(frozen=True)
class HourlyRow:
    entity_code: str
    title: str
    amount: float
    comment: str = ""

@dataclass(frozen=True)
class HourlyMethodRow:
    method_code: str
    title: str
    amount: float
    comment: str = ""

@dataclass(frozen=True)
class HourlyPayoutBlock:
    group_code: str
    title: str
    methods: List[HourlyMethodRow]

@dataclass(frozen=True)
class HourlyDTO:
    start_dt: datetime
    end_dt: datetime
    header_date: datetime      # date used in header
    payout: List[HourlyPayoutBlock]
    payin: List[HourlyRow]


# =============================================================================
# Columns
# =============================================================================

def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols_norm = [(str(c).strip().lower(), c) for c in df.columns]
    cols_exact = {k: orig for k, orig in cols_norm}
    for cand in candidates:
        key = cand.strip().lower()
        if key in cols_exact:
            return cols_exact[key]
    cand_norm = [c.strip().lower() for c in candidates]
    for k, orig in cols_norm:
        for cand in cand_norm:
            if cand and cand in k:
                return orig
    return None


def _parse_amount(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    return pd.to_numeric(
        s.astype(str)
         .str.replace(" ", "", regex=False)
         .str.replace("\u00a0", "", regex=False)
         .str.replace(",", ".", regex=False),
        errors="coerce",
    ).astype(float)


# =============================================================================
# Rules-derived comments (from wallet_limits)
# =============================================================================

def _parse_analyzers_cell(s: str) -> List[str]:
    parts = [p.strip().lower() for p in str(s or "").split(",")]
    return sorted(set(p for p in parts if p))


def _build_comments_from_wallet_limits(*, analyzer: str = "wallet") -> Tuple[
    Dict[str, str],                 # payin_comment: partner_norm -> comment (method default)
    Dict[Tuple[str, str], str],     # payout_comment: (partner_norm, method) -> comment
    Dict[str, str],                 # group_comment: group_name_lower -> comment
]:
    df = get_wallet_limits_df()
    df.columns = [str(c).strip().lower() for c in df.columns]

    df = df[df["enabled"] == 1].copy()
    df["_analyzers_list"] = df["analyzers"].map(_parse_analyzers_cell)
    df = df[df["_analyzers_list"].map(lambda xs: analyzer.lower() in xs)]

    # Normalization
    df["scope"] = df["scope"].astype(str).str.strip().str.lower()
    df["comment"] = df.get("comment", "").astype(str).fillna("").str.strip()
    df["method_norm"] = df.get("method", "").astype(str).fillna("").str.strip().str.upper()
    df["scope_value_norm"] = df["scope_value"].astype(str).str.strip()

    is_partner = df["scope"] == "partner"
    df.loc[is_partner, "scope_value_norm"] = df.loc[is_partner, "scope_value_norm"].map(normalize_partner_name)
    df.loc[~is_partner, "scope_value_norm"] = df.loc[~is_partner, "scope_value_norm"].str.lower()

    payin_comment: Dict[str, str] = {}
    payout_comment: Dict[Tuple[str, str], str] = {}
    group_comment: Dict[str, str] = {}

    # partner comments
    partner_df = df[df["scope"] == "partner"].copy()
    for _, r in partner_df.iterrows():
        c = str(r["comment"] or "").strip()
        if not c:
            continue
        pn = str(r["scope_value_norm"] or "").strip()
        m = str(r["method_norm"] or "").strip().upper()

        # default for payin: method empty
        if not m:
            payin_comment[pn] = c
            payout_comment[(pn, "")] = c
        else:
            payout_comment[(pn, m)] = c

    # group comments
    group_df = df[df["scope"] == "group"].copy()
    for _, r in group_df.iterrows():
        c = str(r["comment"] or "").strip()
        if not c:
            continue
        gn = str(r["scope_value_norm"] or "").strip()
        group_comment[gn] = c

    return payin_comment, payout_comment, group_comment


# =============================================================================
# Analyzer
# =============================================================================

def build_hourly_dto_from_files(
    *,
    payin_path: str,
    payout_path: str,
    start_dt: datetime,
    end_dt: datetime,
    header_date: datetime,
) -> HourlyDTO:
    """
    Rule-driven hourly analyzer.
    - No Telegram
    - No YAML business logic
    - Comments pulled from rules.xlsx wallet_limits
    - Layout is applied in Reporter (ordering/grouping/labels)
    """
    df_payin = pd.read_excel(payin_path, dtype=str)
    df_payout = pd.read_excel(payout_path, dtype=str)

    # Columns
    dt_col = "Дата/Время создания"
    partner_col = "Партнер"
    status_col = "Статус"
    amount_col = "Сумма"

    method_col = _find_col(df_payout, ["enum метод", "метод", "method", "пул", "канал"])

    for df in (df_payin, df_payout):
        if partner_col not in df.columns or amount_col not in df.columns or status_col not in df.columns:
            raise RuntimeError("hourly: input file missing required columns (Партнер/Сумма/Статус)")
        if dt_col not in df.columns:
            raise RuntimeError("hourly: input file missing required datetime column (Дата/Время создания)")

    # Normalize partner + datetime
    df_payin["norm"] = df_payin[partner_col].astype(str).apply(normalize_partner_name)
    df_payout["norm"] = df_payout[partner_col].astype(str).apply(normalize_partner_name)

    s_payin = parse_dt_series_msk(df_payin[dt_col])
    s_payout = parse_dt_series_msk(df_payout[dt_col])

    m_payin = s_payin.notna() & (s_payin >= start_dt) & (s_payin <= end_dt)
    m_payout = s_payout.notna() & (s_payout >= start_dt) & (s_payout <= end_dt)

    df_payin = df_payin.loc[m_payin].copy()
    df_payout = df_payout.loc[m_payout].copy()

    # Paid only
    df_payin = df_payin[df_payin[status_col].astype(str).str.lower() == "оплачен"].copy()
    df_payout = df_payout[df_payout[status_col].astype(str).str.lower() == "оплачен"].copy()

    # Amounts
    df_payin["_amount"] = _parse_amount(df_payin[amount_col]).fillna(0.0)
    df_payout["_amount"] = _parse_amount(df_payout[amount_col]).fillna(0.0)

    # Method (payout)
    if method_col is None:
        df_payout["_method"] = ""
    else:
        df_payout["_method"] = df_payout[method_col].astype(str).fillna("").str.strip().str.upper()

    # Comments
    payin_comment, payout_comment, group_comment = _build_comments_from_wallet_limits(analyzer="wallet")

    # Partner groups map (for optional group comments in reporter)
    pg_df = get_partner_groups_df()
    pg_df.columns = [str(c).strip().lower() for c in pg_df.columns]
    pg_df = pg_df[pg_df["enabled"] == 1].copy()
    pg_df["_analyzers_list"] = pg_df["analyzers"].map(_parse_analyzers_cell)
    pg_df = pg_df[pg_df["_analyzers_list"].map(lambda xs: "wallet" in xs)]
    partner_to_group = dict(
        zip(pg_df["partner"].map(normalize_partner_name), pg_df["group_name"].astype(str).str.strip().str.lower())
    )

    # Aggregate payout: partner_norm + method
    payout_blocks: List[HourlyPayoutBlock] = []
    g_payout = (
        df_payout.groupby(["norm", "_method"], dropna=False)["_amount"]
        .sum()
        .reset_index()
        .sort_values(["norm", "_method"])
    )
    for partner_norm, sub in g_payout.groupby("norm"):
        if not partner_norm:
            continue
        display_partner = df_payout.loc[df_payout["norm"] == partner_norm, partner_col].astype(str).iloc[0]
        methods: List[HourlyMethodRow] = []
        for _, r in sub.iterrows():
            method = str(r["_method"] or "").strip().upper()
            amt = float(r["_amount"] or 0.0)
            c = (
                payout_comment.get((partner_norm, method))
                or payout_comment.get((partner_norm, ""))
                or ""
            )
            methods.append(
                HourlyMethodRow(
                    method_code=method,
                    title=method or "—",
                    amount=amt,
                    comment=c,
                )
            )
        payout_blocks.append(
            HourlyPayoutBlock(
                group_code=partner_norm,
                title=display_partner,
                methods=methods,
            )
        )

    # Aggregate payin: partner_norm
    payin_rows: List[HourlyRow] = []
    g_payin = (
        df_payin.groupby(["norm"], dropna=False)["_amount"]
        .sum()
        .reset_index()
        .sort_values(["norm"])
    )
    for _, r in g_payin.iterrows():
        pn = str(r["norm"] or "").strip()
        if not pn:
            continue
        display_partner = df_payin.loc[df_payin["norm"] == pn, partner_col].astype(str).iloc[0]
        amt = float(r["_amount"] or 0.0)
        c = payin_comment.get(pn, "")
        payin_rows.append(
            HourlyRow(
                entity_code=pn,
                title=display_partner,
                amount=amt,
                comment=c,
            )
        )

    # Note: group totals and layout are applied in reporter using rules/job_params (no YAML).
    # We still expose partner_to_group & group_comment through job_params to reporter.
    return HourlyDTO(
        start_dt=start_dt,
        end_dt=end_dt,
        header_date=header_date,
        payout=payout_blocks,
        payin=payin_rows,
    )
