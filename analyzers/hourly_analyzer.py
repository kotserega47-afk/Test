# analyzers/hourly_analyzer.py
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
from zoneinfo import ZoneInfo

from utils.normalization import normalize_partner_name, parse_dt_series_msk
from core.rules_provider import get_snapshot_v2, get_indexes_v2
from core.rules_v2.accessors import HourlyRulesAccessor
from core.rules_v2.models import RulesSnapshotV2

MSK = ZoneInfo("Europe/Moscow")

logger = logging.getLogger(__name__)

_HOURLY_PAYIN_SECTION = "hourly.config_payins"

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
# Partner resolution (Excel label → partner_code → partner_key → report row)
# =============================================================================


_TRAILING_PARENS_CODES = re.compile(r"\(([^)]+)\)\s*$")


def extract_trailing_parenthetical_codes(label: str) -> list[str]:
    """
    Parse the last parenthetical segment on an Excel partner label.

    Examples:
      ``HH (Аврора Сбер) (109)`` → ``["109"]``
      ``Group (108+109)`` → ``["108", "109"]``
    """

    s = str(label or "").strip()
    if not s:
        return []
    m = _TRAILING_PARENS_CODES.search(s)
    if not m:
        return []
    inner = m.group(1).strip()
    codes: list[str] = []
    for part in re.split(r"\s*\+\s*", inner):
        token = part.strip()
        if token.isdigit():
            codes.append(token)
    return codes


def _build_hourly_payin_member_key_to_report_sources(snapshot: RulesSnapshotV2) -> dict[str, set[str]]:
    """
    Map canonical ``member_key`` (``source_partner`` on hourly payin rows) to
    ``report_items.source_key`` values for that row.
    """

    out: dict[str, set[str]] = defaultdict(set)
    payin_items = [
        it
        for it in snapshot.report_items
        if it.enabled
        and it.report_key == "hourly"
        and it.section_key == _HOURLY_PAYIN_SECTION
        and it.item_type == "payin_row"
    ]
    item_keys = {it.item_key for it in payin_items}
    src_by_item = {it.item_key: str(it.source_key or "").strip() for it in payin_items}

    for m in snapshot.report_item_members:
        if not m.enabled or m.member_type != "source_partner":
            continue
        if m.item_key not in item_keys:
            continue
        sk = src_by_item.get(m.item_key, "")
        if not sk:
            continue
        mk = str(m.member_key or "").strip()
        if mk:
            out[mk].add(sk)
    return {k: set(v) for k, v in out.items()}


def _partner_keys_for_excel_partner_cell(
    raw_label: str,
    *,
    indexes,
    rules: HourlyRulesAccessor,
) -> tuple[list[str], list[str]]:
    """
    Resolve partner_key list from an Excel ``Партнер`` cell.

    Returns (partner_keys_in_order, warning_messages).
    """

    raw = str(raw_label or "").strip()
    warns: list[str] = []
    keys: list[str] = []
    if not raw:
        return keys, warns

    for code in extract_trailing_parenthetical_codes(raw):
        pk = indexes.partners_by_code.get(code)
        if not pk:
            warns.append(f"unknown partner_code {code}")
        elif pk not in keys:
            keys.append(pk)

    if not keys:
        p = rules.resolve_partner(raw)
        if p:
            keys.append(p.partner_key)

    if not keys:
        norm = normalize_partner_name(raw)
        if norm:
            p2 = rules.resolve_partner(norm)
            if p2:
                keys.append(p2.partner_key)

    return keys, warns


def _pick_single_payin_report_source(
    partner_keys: list[str],
    payin_member_index: dict[str, set[str]],
) -> tuple[Optional[str], list[str]]:
    warns: list[str] = []
    rkeys: set[str] = set()
    for pk in partner_keys:
        rkeys |= payin_member_index.get(pk, set())
    if not rkeys:
        if partner_keys:
            warns.append(
                "partner(s) not listed on any hourly payin_row (check report_item_members.source_partner)"
            )
        return None, warns
    if len(rkeys) > 1:
        warns.append(f"ambiguous hourly payin_row mapping; using {sorted(rkeys)[0]!r} among {sorted(rkeys)!r}")
    return sorted(rkeys)[0], warns


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
    # Resolve partner_key + hourly payin report source_key (canonical keys)
    # -------------------------------------------------------------------------
    payin_member_index = _build_hourly_payin_member_key_to_report_sources(snapshot)

    payin_pk: list[Optional[str]] = []
    payin_rsk: list[Optional[str]] = []
    payin_key_lists: list[list[str]] = []
    payout_pk: list[Optional[str]] = []
    payout_key_lists: list[list[str]] = []

    warn_lines: list[str] = []

    for raw in df_payin[partner_col].astype(str).tolist():
        keys, w1 = _partner_keys_for_excel_partner_cell(raw, indexes=indexes, rules=rules)
        payin_key_lists.append(keys)
        rsk, w2 = _pick_single_payin_report_source(keys, payin_member_index)
        for w in w1 + w2:
            warn_lines.append(f"payin row partner={raw!r}: {w}")
        payin_pk.append(keys[0] if keys else None)
        payin_rsk.append(rsk)

    for raw in df_payout[partner_col].astype(str).tolist():
        keys, w1 = _partner_keys_for_excel_partner_cell(raw, indexes=indexes, rules=rules)
        payout_key_lists.append(keys)
        for w in w1:
            warn_lines.append(f"payout row partner={raw!r}: {w}")
        payout_pk.append(keys[0] if keys else None)

    for msg in warn_lines[:500]:
        logger.warning("hourly analyzer: %s", msg)
    if len(warn_lines) > 500:
        logger.warning(
            "hourly analyzer: %s more partner-resolution warnings omitted",
            len(warn_lines) - 500,
        )

    df_payin["_partner_key"] = payin_pk
    df_payin["_payin_report_source"] = payin_rsk
    df_payout["_partner_key"] = payout_pk

    unmapped_payin_amt = float(df_payin.loc[df_payin["_payin_report_source"].isna(), "_amount"].sum() or 0.0)
    if unmapped_payin_amt > 0:
        logger.warning(
            "hourly payin: %.2f total amount in rows not mapped to any hourly payin_row (source_key)",
            unmapped_payin_amt,
        )

    # -------------------------------------------------------------------------
    # Build partner metadata (canonical partner_key) + norm fallback for payout
    # -------------------------------------------------------------------------
    partner_meta: Dict[str, Dict[str, str]] = {}
    partner_meta_norm: Dict[str, Dict[str, str]] = {}

    observed_partner_keys: set[str] = set()
    for keys in payin_key_lists + payout_key_lists:
        for pk in keys:
            if pk:
                observed_partner_keys.add(pk)

    for partner_key in sorted(observed_partner_keys):
        partner = rules.get_partner(partner_key)
        if not partner:
            logger.warning(
                "hourly: partner_key %r missing or disabled in snapshot; using minimal payout metadata",
                partner_key,
            )
            partner_meta[partner_key] = {
                "partner_key": partner_key,
                "group_key": "",
                "title": partner_key,
                "entity_code": partner_key,
                "default_method": "",
            }
            continue

        primary_group = rules.get_primary_group(job_key, partner_key)
        group_key = primary_group.group_key if primary_group else None

        group_title = rules.get_group_display_name(group_key) if group_key else None
        partner_title = rules.get_partner_display_name(partner_key) or partner.display_name or partner_key
        default_method = rules.get_default_method_key(partner_key) or ""

        partner_meta[partner_key] = {
            "partner_key": partner_key,
            "group_key": group_key or "",
            "title": group_title or partner_title,
            "entity_code": group_key or partner_key,
            "default_method": _clean_method(default_method),
        }

    for norm in sorted(
        set(df_payout.loc[df_payout["_partner_key"].isna(), "norm"].astype(str).str.strip().tolist())
        | set(df_payin.loc[df_payin["_partner_key"].isna(), "norm"].astype(str).str.strip().tolist())
    ):
        if not norm:
            continue
        if norm in partner_meta_norm:
            continue
        partner_meta_norm[norm] = {
            "partner_key": "",
            "group_key": "",
            "title": norm,
            "entity_code": norm,
            "default_method": "",
        }

    def _default_method_for_payout_row(row: pd.Series) -> str:
        pk = row["_partner_key"]
        if pk is not None and not (isinstance(pk, float) and pd.isna(pk)) and str(pk).strip():
            return partner_meta.get(str(pk).strip(), {}).get("default_method", "")
        return partner_meta_norm.get(str(row["norm"]).strip(), {}).get("default_method", "")

    defaults = df_payout.apply(_default_method_for_payout_row, axis=1)
    m = df_payout["_method"].eq("") & defaults.ne("")
    if m.any():
        df_payout.loc[m, "_method"] = defaults.loc[m]

    # -------------------------------------------------------------------------
    # Comments / exclusions via accessor-layer
    # -------------------------------------------------------------------------
    payin_comment: Dict[str, str] = {}
    payout_comment: Dict[Tuple[str, str], str] = {}

    for partner_key in sorted(observed_partner_keys):
        meta = partner_meta.get(partner_key, {})
        group_key = meta.get("group_key") or None

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
            for rsk in payin_member_index.get(partner_key, set()):
                payin_comment[rsk] = base_comment
            payout_comment[(partner_key, "")] = base_comment

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
                payout_comment[(partner_key, method)] = method_comment

    # -------------------------------------------------------------------------
    # Aggregate payout
    # -------------------------------------------------------------------------
    payout_blocks_raw: List[HourlyPayoutBlock] = []

    df_payout["_payout_entity"] = df_payout.apply(
        lambda r: str(r["_partner_key"]).strip()
        if r["_partner_key"] is not None
        and not (isinstance(r["_partner_key"], float) and pd.isna(r["_partner_key"]))
        and str(r["_partner_key"]).strip()
        else str(r["norm"]).strip(),
        axis=1,
    )

    g_payout = (
        df_payout.groupby(["_payout_entity", "_method"], dropna=False)["_amount"]
        .sum()
        .reset_index()
        .sort_values(["_payout_entity", "_method"])
    )

    for entity, sub in g_payout.groupby("_payout_entity"):
        entity = str(entity or "").strip()
        if not entity:
            continue

        display_partner = (
            df_payout.loc[df_payout["_payout_entity"] == entity, partner_col].astype(str).iloc[0]
        )

        if entity in partner_meta:
            meta = partner_meta[entity]
        else:
            meta = partner_meta_norm.get(entity, {})

        block_code = meta.get("entity_code") or entity
        block_title = meta.get("title") or display_partner

        methods: List[HourlyMethodRow] = []

        for _, row in sub.iterrows():
            method = _clean_method(row["_method"])
            amt = float(row["_amount"] or 0.0)

            pk_for_comment = entity if entity in partner_meta else None
            if pk_for_comment:
                comment = (
                    payout_comment.get((pk_for_comment, method))
                    or payout_comment.get((pk_for_comment, ""))
                    or ""
                )
            else:
                comment = ""

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
    # Aggregate payin by report_items.source_key (not partner display text)
    # -------------------------------------------------------------------------
    payin_rows: List[HourlyRow] = []

    df_pin = df_payin[df_payin["_payin_report_source"].notna()].copy()
    if df_pin.empty:
        g_payin = pd.DataFrame(columns=["_payin_report_source", "_amount"])
    else:
        g_payin = (
            df_pin.groupby(["_payin_report_source"], dropna=False)["_amount"]
            .sum()
            .reset_index()
            .sort_values(["_payin_report_source"])
        )

    for _, row in g_payin.iterrows():
        report_sk = str(row["_payin_report_source"] or "").strip()
        if not report_sk:
            continue

        title = report_sk
        for it in snapshot.report_items:
            if (
                it.enabled
                and it.report_key == "hourly"
                and it.section_key == _HOURLY_PAYIN_SECTION
                and it.item_type == "payin_row"
                and str(it.source_key or "").strip() == report_sk
            ):
                title = str(it.display_name or it.source_key or report_sk).strip()
                break

        amt = float(row["_amount"] or 0.0)
        comment = payin_comment.get(report_sk, "")

        payin_rows.append(
            HourlyRow(
                entity_code=report_sk,
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
