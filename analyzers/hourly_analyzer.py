# analyzers/hourly_analyzer.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
from zoneinfo import ZoneInfo

from utils.normalization import normalize_partner_name, parse_dt_series_msk
from core.rules_provider import get_snapshot_v2, get_indexes_v2
from core.rules_v2.accessors import HourlyRulesAccessor

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
    header_date: datetime
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


def _to_msk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def _clean_method(value: object) -> str:
    s = str(value or "").strip().upper()
    return s


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
    - Rules are resolved via RulesSnapshotV2 + HourlyRulesAccessor
    - Layout is applied in Reporter
    """
    snapshot = get_snapshot_v2()
    indexes = get_indexes_v2()
    rules = HourlyRulesAccessor(snapshot=snapshot, indexes=indexes)
    job_key = "hourly"

    start_dt = _to_msk(start_dt)
    end_dt = _to_msk(end_dt)
    header_date = _to_msk(header_date)

    df_payin = pd.read_excel(payin_path, dtype=str)
    df_payout = pd.read_excel(payout_path, dtype=str)

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

    # -------------------------------------------------------------------------
    # Normalize partner + datetime
    # -------------------------------------------------------------------------
    df_payin["norm"] = df_payin[partner_col].astype(str).apply(normalize_partner_name)
    df_payout["norm"] = df_payout[partner_col].astype(str).apply(normalize_partner_name)

    s_payin = parse_dt_series_msk(df_payin[dt_col])
    s_payout = parse_dt_series_msk(df_payout[dt_col])

    m_payin = s_payin.notna() & (s_payin >= start_dt) & (s_payin <= end_dt)
    m_payout = s_payout.notna() & (s_payout >= start_dt) & (s_payout <= end_dt)

    df_payin = df_payin.loc[m_payin].copy()
    df_payout = df_payout.loc[m_payout].copy()

    # paid only
    df_payin = df_payin[df_payin[status_col].astype(str).str.lower() == "оплачен"].copy()
    df_payout = df_payout[df_payout[status_col].astype(str).str.lower() == "оплачен"].copy()

    # amounts
    df_payin["_amount"] = _parse_amount(df_payin[amount_col]).fillna(0.0)
    df_payout["_amount"] = _parse_amount(df_payout[amount_col]).fillna(0.0)

    # payout method
    if method_col is None:
        df_payout["_method"] = ""
    else:
        df_payout["_method"] = (
            df_payout[method_col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

    # -------------------------------------------------------------------------
    # Build partner metadata strictly via accessor-layer
    # -------------------------------------------------------------------------
    partner_meta: Dict[str, Dict[str, str]] = {}

    observed_norms = set(
        df_payin["norm"].dropna().astype(str).str.strip().tolist()
        + df_payout["norm"].dropna().astype(str).str.strip().tolist()
    )

    for partner_norm in observed_norms:
        if not partner_norm:
            continue

        partner = rules.resolve_partner(partner_norm)
        if not partner:
            continue

        partner_key = partner.partner_key
        primary_group = rules.get_primary_group(job_key, partner_key)
        group_key = primary_group.group_key if primary_group else None

        group_title = rules.get_group_display_name(group_key) if group_key else None
        partner_title = rules.get_partner_display_name(partner_key) or partner.display_name or partner_norm
        default_method = rules.get_default_method_key(partner_key) or ""

        partner_meta[partner_norm] = {
            "partner_key": partner_key,
            "group_key": group_key or "",
            "title": group_title or partner_title,
            "entity_code": group_key or partner_norm,
            "default_method": _clean_method(default_method),
        }

    defaults = df_payout["norm"].map(
        lambda x: partner_meta.get(str(x).strip(), {}).get("default_method", "")
    ).fillna("")

    m = df_payout["_method"].eq("") & defaults.ne("")
    if m.any():
        df_payout.loc[m, "_method"] = defaults.loc[m]

    # -------------------------------------------------------------------------
    # Comments / exclusions via accessor-layer
    # -------------------------------------------------------------------------
    payin_comment: Dict[str, str] = {}
    payout_comment: Dict[Tuple[str, str], str] = {}

    for partner_norm, meta in partner_meta.items():
        partner_key = meta["partner_key"] or None
        group_key = meta["group_key"] or None

        exclusion = rules.get_exclusion(
            start_dt,
            partner_key=partner_key,
            group_key=group_key,
        )
        exclusion_reason = (exclusion.reason or "").strip() if exclusion else ""

        payin_rule = rules.resolve_limit_rule(
            "daily_max_amount",
            partner_key=partner_key,
            group_key=group_key,
            method_key=None,
        )

        base_comment = ""
        if payin_rule and (payin_rule.comment or "").strip():
            base_comment = (payin_rule.comment or "").strip()

        if exclusion_reason:
            base_comment = exclusion_reason

        if base_comment:
            payin_comment[partner_norm] = base_comment
            payout_comment[(partner_norm, "")] = base_comment

        for method in ("UNI", "CARD", "CARDS", "SBP", "PAYOUT"):
            method_comment = exclusion_reason

            if not method_comment:
                payout_rule = rules.resolve_limit_rule(
                    "daily_max_amount",
                    partner_key=partner_key,
                    group_key=group_key,
                    method_key=method.lower(),
                )
                if payout_rule and (payout_rule.comment or "").strip():
                    method_comment = (payout_rule.comment or "").strip()

            if method_comment:
                payout_comment[(partner_norm, method)] = method_comment

    # -------------------------------------------------------------------------
    # Aggregate payout
    # -------------------------------------------------------------------------
    payout_blocks_raw: List[HourlyPayoutBlock] = []

    g_payout = (
        df_payout.groupby(["norm", "_method"], dropna=False)["_amount"]
        .sum()
        .reset_index()
        .sort_values(["norm", "_method"])
    )

    for partner_norm, sub in g_payout.groupby("norm"):
        partner_norm = str(partner_norm or "").strip()
        if not partner_norm:
            continue

        display_partner = (
            df_payout.loc[df_payout["norm"] == partner_norm, partner_col]
            .astype(str)
            .iloc[0]
        )

        meta = partner_meta.get(partner_norm, {})
        block_code = meta.get("entity_code") or partner_norm
        block_title = meta.get("title") or display_partner

        methods: List[HourlyMethodRow] = []

        for _, row in sub.iterrows():
            method = _clean_method(row["_method"])
            amt = float(row["_amount"] or 0.0)

            comment = (
                payout_comment.get((partner_norm, method))
                or payout_comment.get((partner_norm, ""))
                or ""
            )

            methods.append(
                HourlyMethodRow(
                    method_code=method,
                    title=method or "—",
                    amount=amt,
                    comment=comment,
                )
            )

        payout_blocks_raw.append(
            HourlyPayoutBlock(
                group_code=block_code,
                title=block_title,
                methods=methods,
            )
        )

    # merge by entity/group code
    merged_payout: Dict[str, HourlyPayoutBlock] = {}

    for block in payout_blocks_raw:
        if block.group_code not in merged_payout:
            merged_payout[block.group_code] = HourlyPayoutBlock(
                group_code=block.group_code,
                title=block.title,
                methods=list(block.methods),
            )
        else:
            merged_payout[block.group_code].methods.extend(block.methods)

    payout_blocks = list(merged_payout.values())

    # -------------------------------------------------------------------------
    # Aggregate payin
    # -------------------------------------------------------------------------
    payin_rows: List[HourlyRow] = []

    g_payin = (
        df_payin.groupby(["norm"], dropna=False)["_amount"]
        .sum()
        .reset_index()
        .sort_values(["norm"])
    )

    for _, row in g_payin.iterrows():
        partner_norm = str(row["norm"] or "").strip()
        if not partner_norm:
            continue

        display_partner = (
            df_payin.loc[df_payin["norm"] == partner_norm, partner_col]
            .astype(str)
            .iloc[0]
        )

        meta = partner_meta.get(partner_norm, {})
        entity_code = meta.get("entity_code") or partner_norm
        title = meta.get("title") or display_partner
        amt = float(row["_amount"] or 0.0)
        comment = payin_comment.get(partner_norm, "")

        payin_rows.append(
            HourlyRow(
                entity_code=entity_code,
                title=title,
                amount=amt,
                comment=comment,
            )
        )

    return HourlyDTO(
        start_dt=start_dt,
        end_dt=end_dt,
        header_date=header_date,
        payout=payout_blocks,
        payin=payin_rows,
    )