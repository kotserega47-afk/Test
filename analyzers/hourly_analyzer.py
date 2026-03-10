# analyzers/hourly_analyzer.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
from zoneinfo import ZoneInfo

from utils.normalization import normalize_partner_name, parse_dt_series_msk
from core.rules_provider import get_snapshot_v2

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
    snapshot = get_snapshot_v2()
    analyzer_job_key = "wallet"

    # -------------------------------------------------------------------------
    # Normalize all boundary datetimes to Europe/Moscow
    # -------------------------------------------------------------------------
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=MSK)
    else:
        start_dt = start_dt.astimezone(MSK)

    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=MSK)
    else:
        end_dt = end_dt.astimezone(MSK)

    if header_date.tzinfo is None:
        header_date = header_date.replace(tzinfo=MSK)
    else:
        header_date = header_date.astimezone(MSK)

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
    payin_comment: Dict[str, str] = {}
    payout_comment: Dict[Tuple[str, str], str] = {}

    for rule in snapshot.limit_rules:
        if not rule.enabled:
            continue
        if rule.job_key != analyzer_job_key:
            continue
        if rule.metric_key != "daily_max_amount":
            continue

        comment = (rule.comment or "").strip()
        if not comment:
            continue

        if rule.scope_type == "partner":
            partner_def = snapshot.partners.get(rule.scope_key)
            if not partner_def:
                continue

            partner_norm = normalize_partner_name(
                partner_def.source_name or partner_def.display_name or ""
            )

            method = (rule.method_key or "").upper()

            if method:
                payout_comment[(partner_norm, method)] = comment
            else:
                payin_comment[partner_norm] = comment
                payout_comment[(partner_norm, "")] = comment


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

        display_partner = (
            df_payout.loc[df_payout["norm"] == partner_norm, partner_col]
            .astype(str)
            .iloc[0]
        )

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

        display_partner = (
            df_payin.loc[df_payin["norm"] == pn, partner_col]
            .astype(str)
            .iloc[0]
        )

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

    return HourlyDTO(
        start_dt=start_dt,
        end_dt=end_dt,
        header_date=header_date,
        payout=payout_blocks,
        payin=payin_rows,
    )