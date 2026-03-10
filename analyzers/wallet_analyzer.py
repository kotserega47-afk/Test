# analyzers/wallet_analyzer.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from core.rules_provider import get_snapshot_v2, get_indexes_v2
from core.rules_v2.accessors import WalletRulesAccessor
from utils.normalization import normalize_partner_name, parse_dt_series_msk

MSK_TZ = ZoneInfo("Europe/Moscow")


# =============================================================================
# Existing payout DTOs / limit resolver
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
    method: str
    amount: float
    limit: LimitResolved


@dataclass(frozen=True)
class WalletPartnerBlock:
    partner: str
    group: Optional[str]
    rows: List[WalletMethodRow]


@dataclass(frozen=True)
class WalletDTO:
    report_day: datetime.date
    blocks: List[WalletPartnerBlock]


# =============================================================================
# New stats DTOs for wallet report
# =============================================================================

@dataclass(frozen=True)
class WalletPartnerStats:
    partner: str
    group: Optional[str]

    total_ops: int
    success_ops: int

    conversion_pct: float
    conversion_threshold_pct: Optional[float]
    conversion_insufficient: bool
    conversion_bad: bool

    payin_amount: float
    daily_limit: Optional[float]
    daily_limit_comment: str
    percent_filled: int
    limit_bad: bool
    limit_warn: bool

    api_cancel_count: int
    api_total_ops: int
    api_cancel_pct: float
    api_cancel_threshold_pct: Optional[float]
    api_insufficient: bool
    api_bad: bool

    nok_wallets_count: int

    last_success_at: Optional[datetime]
    last_success_minutes_ago: int | None


@dataclass(frozen=True)
class WalletAlertBlock:
    partner: str
    lines: List[str]


@dataclass(frozen=True)
class WalletStatsDTO:
    report_day: datetime.date
    generated_at: datetime
    window_minutes: int
    offset_minutes: int
    min_events: int
    stuck_payins_count: int
    stuck_payouts_count: int
    partners: List[WalletPartnerStats]
    alerts: List[WalletAlertBlock]


# =============================================================================
# Helpers
# =============================================================================
def _calc_last_success_minutes_ago(now: datetime, last_success_at: datetime | None) -> int | None:
    if last_success_at is None:
        return None
    return int((now - last_success_at).total_seconds() // 60)

def _parse_analyzers_cell(s: str) -> List[str]:
    parts = [p.strip().lower() for p in str(s or "").split(",")]
    return sorted(set(p for p in parts if p))


def _norm_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    return out


def _parse_amount_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    return pd.to_numeric(
        s.astype(str)
         .str.replace(" ", "", regex=False)
         .str.replace("\u00a0", "", regex=False)
         .str.replace(",", ".", regex=False),
        errors="coerce",
    ).astype(float)


def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols_norm = [(str(c).strip().lower(), c) for c in df.columns]
    exact = {k: orig for k, orig in cols_norm}
    for cand in candidates:
        key = cand.strip().lower()
        if key in exact:
            return exact[key]
    cand_norm = [c.strip().lower() for c in candidates]
    for k, orig in cols_norm:
        for cand in cand_norm:
            if cand and cand in k:
                return orig
    return None


def _normalize_status(s: str) -> str:
    return str(s or "").strip().lower()


def _status_success(s: str) -> bool:
    return _normalize_status(s) == "оплачен"


def _status_countable(s: str) -> bool:
    return _normalize_status(s) in {"оплачен", "ошибка"}


def _status_pending(s: str) -> bool:
    return _normalize_status(s) == "ожидает оплаты"


def _get_wallet_job_params(*, rules_force_sync: bool = False) -> dict:
    snapshot = get_snapshot_v2(force_sync=rules_force_sync)
    indexes = get_indexes_v2(force_sync=rules_force_sync)
    rules = WalletRulesAccessor(snapshot=snapshot, indexes=indexes)

    required_keys = {
        "window_minutes",
        "offset_minutes",
        "min_events",
        "pending_payin_minutes",
        "pending_payout_minutes",
    }

    params = {
        key: rules.get_job_param("wallet", key)
        for key in required_keys
    }

    missing = [k for k in required_keys if params.get(k) in (None, "")]
    if missing:
        raise RuntimeError(
            "job_params: missing required wallet params: " + ", ".join(sorted(missing))
        )

    def _require_int(name: str) -> int:
        raw = params.get(name)
        try:
            value = int(raw)
        except Exception:
            raise RuntimeError(
                f"job_params: wallet param '{name}' must be int, got {raw!r}"
            )

        if name == "offset_minutes":
            if value < 0:
                raise RuntimeError(
                    f"job_params: wallet param '{name}' must be >= 0, got {value}"
                )
        else:
            if value < 1:
                raise RuntimeError(
                    f"job_params: wallet param '{name}' must be >= 1, got {value}"
                )

        return value

    return {
        "window_minutes": _require_int("window_minutes"),
        "offset_minutes": _require_int("offset_minutes"),
        "min_events": _require_int("min_events"),
        "pending_payin_minutes": _require_int("pending_payin_minutes"),
        "pending_payout_minutes": _require_int("pending_payout_minutes"),
    }


# =============================================================================
# Legacy payout-summary builder (preserved)
# =============================================================================

def build_wallet_dto_from_payout_xlsx(
    payout_path: str,
    *,
    analyzer: str = "wallet",
    rules_force_sync: bool = False,
    report_day: Optional[datetime.date] = None,
) -> WalletDTO:
    if report_day is None:
        report_day = datetime.now(MSK_TZ).date()

    partner_to_group = _build_partner_group_map(pg_df, analyzer=analyzer)
    partner_default_method = _build_partner_default_method_map(pg_df, analyzer=analyzer)

    snapshot = get_snapshot_v2(force_sync=rules_force_sync)
    indexes = get_indexes_v2(force_sync=rules_force_sync)
    rules = WalletRulesAccessor(snapshot=snapshot, indexes=indexes)

    analyzer_job_key = analyzer.strip().lower()

    partner_to_group: Dict[str, str] = {}
    partner_default_method: Dict[str, str] = {}

    for partner_key, partner_def in snapshot.partners.items():
        raw_name = partner_def.source_name or partner_def.display_name or ""
        partner_norm = normalize_partner_name(raw_name)
        if not partner_norm:
            continue

        memberships = rules.get_group_memberships(analyzer_job_key, partner_key)
        if memberships:
            group_key = memberships[0]
            if group_key:
                partner_to_group[partner_norm] = group_key

            member = rules.get_primary_group(analyzer_job_key, partner_key)
            if member and member.default_method_key:
                partner_default_method[partner_norm] = str(member.default_method_key).upper()

    df = pd.read_excel(payout_path)
    dt_col = _find_col(df, ["date", "datetime", "created_at", "дата", "дата/время", "дата/время создания", "дата создания"])
    partner_col = _find_col(df, ["partner", "партнер", "партнёр"])
    amount_col = _find_col(df, ["amount", "сумма", "sum", "итого"])
    method_col = _find_col(df, ["enum метод", "method", "метод", "пул", "канал"])

    if partner_col is None or amount_col is None:
        raise RuntimeError("payout file: missing required columns (partner, amount)")

    if dt_col is not None:
        df["_dt"] = parse_dt_series_msk(df[dt_col])
        df = df[df["_dt"].dt.date == report_day].copy()
    else:
        df = df.copy()

    df["_partner"] = df[partner_col].fillna("").astype(str)
    df["_partner_norm"] = df["_partner"].map(normalize_partner_name)
    df["_amount"] = _parse_amount_series(df[amount_col]).fillna(0.0)

    if method_col is None:
        df["_method"] = ""
    else:
        df["_method"] = (
            df[method_col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

    defaults = df["_partner_norm"].map(partner_default_method).fillna("")
    m = df["_method"].eq("") & defaults.ne("")
    if m.any():
        df.loc[m, "_method"] = defaults.loc[m]

    bad = df["_method"].eq("")
    if bad.any():
        bad_partners = df.loc[bad, "_partner"].astype(str).dropna().unique().tolist()
        raise RuntimeError(
            "payout file: enum method empty and no default_method in rules for partners: "
            + ", ".join(bad_partners[:20])
            + (" …" if len(bad_partners) > 20 else "")
        )

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

        display_partner = (
            df.loc[df["_partner_norm"] == partner_norm, "_partner"].astype(str).iloc[0]
            if (df["_partner_norm"] == partner_norm).any()
            else partner_norm
        )
        group_name = partner_to_group.get(partner_norm)
        rows: List[WalletMethodRow] = []

        for _, r in sub.iterrows():
            method = (r["_method"] or "").strip().upper() or "UNI"
            amount = float(r["_amount"] or 0.0)
            partner_obj = rules.resolve_partner(display_partner)
            partner_key = partner_obj.partner_key if partner_obj else None

            limit_rule = rules.resolve_limit_rule(
                "daily_max_amount",
                partner_key=partner_key,
                group_key=group_name,
                method_key=(method.lower() if method else None),
            )

            if limit_rule is None:
                lim = LimitResolved(
                    status="MISSING",
                    limit_value=None,
                    scope=None,
                    scope_value=None,
                    method=None,
                    rule_id=None,
                    comment="",
                    reason="",
                )
            elif float(limit_rule.limit_value) == 0:
                lim = LimitResolved(
                    status="STOP",
                    limit_value=float(limit_rule.limit_value),
                    scope=limit_rule.scope_type,
                    scope_value=limit_rule.scope_key,
                    method=limit_rule.method_key,
                    rule_id=limit_rule.rule_key,
                    comment=limit_rule.comment or "",
                    reason="",
                )
            else:
                lim = LimitResolved(
                    status="ACTIVE",
                    limit_value=float(limit_rule.limit_value),
                    scope=limit_rule.scope_type,
                    scope_value=limit_rule.scope_key,
                    method=limit_rule.method_key,
                    rule_id=limit_rule.rule_key,
                    comment=limit_rule.comment or "",
                    reason="",
                )

            rows.append(WalletMethodRow(method=method, amount=amount, limit=lim))

        blocks.append(WalletPartnerBlock(partner=display_partner, group=group_name, rows=rows))

    return WalletDTO(report_day=report_day, blocks=blocks)


# =============================================================================
# New wallet stats builder for main report
# =============================================================================

def build_wallet_stats_dto(
    payin_path: str,
    payout_path: str,
    *,
    analyzer: str = "wallet",
    rules_force_sync: bool = False,
    now: Optional[datetime] = None,
) -> WalletStatsDTO:
    runtime = _get_wallet_job_params(rules_force_sync=rules_force_sync)

    snapshot = get_snapshot_v2(force_sync=rules_force_sync)
    indexes = get_indexes_v2(force_sync=rules_force_sync)

    rules = WalletRulesAccessor(snapshot=snapshot, indexes=indexes)

    analyzer_job_key = analyzer.strip().lower()

    partner_to_group: Dict[str, str] = {}
    group_to_partners: Dict[str, List[str]] = {}
    partner_default_method: Dict[str, str] = {}
    conv_thresholds: Dict[str, float] = {}
    api_thresholds: Dict[str, float] = {}
    min_events_map: Dict[str, int] = {}

    for partner_key, partner_def in snapshot.partners.items():
        raw_name = partner_def.source_name or partner_def.display_name or ""
        partner_norm = normalize_partner_name(raw_name)
        if not partner_norm:
            continue

        memberships = rules.get_group_memberships(analyzer_job_key, partner_key)
        if memberships:
            group_key = memberships[0]
            if group_key:
                partner_to_group[partner_norm] = group_key
                group_to_partners.setdefault(group_key, []).append(partner_norm)

            member = rules.get_primary_group(analyzer_job_key, partner_key)
            if member and member.default_method_key:
                partner_default_method[partner_norm] = str(member.default_method_key).upper()

        conv_rule = rules.resolve_threshold_rule("conversion_rate", partner_key=partner_key)
        if conv_rule and conv_rule.threshold_min is not None:
            conv_thresholds[partner_norm] = float(conv_rule.threshold_min)

        api_rule = rules.resolve_threshold_rule("api_cancel_rate", partner_key=partner_key)
        if api_rule and api_rule.threshold_max is not None:
            api_thresholds[partner_norm] = float(api_rule.threshold_max)

        min_events_rule = (
                rules.resolve_threshold_rule("conversion_rate", partner_key=partner_key)
                or rules.resolve_threshold_rule("api_cancel_rate", partner_key=partner_key)
        )
        if min_events_rule and min_events_rule.min_events is not None:
            min_events_map[partner_norm] = int(min_events_rule.min_events)

    if now is None:
        now = datetime.now(MSK_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=MSK_TZ)
    else:
        now = now.astimezone(MSK_TZ)

    df = pd.read_excel(payin_path)

    col_dt = _find_col(df, ["дата/время создания", "дата/время", "дата создания", "date", "created_at"])
    col_partner = _find_col(df, ["партнер", "партнёр", "partner"])
    col_status = _find_col(df, ["статус", "status"])
    col_info = _find_col(df, ["инфо", "info"])
    col_amount = _find_col(df, ["сумма", "amount", "sum", "итого"])

    required = {"partner": col_partner, "status": col_status, "info": col_info, "amount": col_amount}
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise RuntimeError(f"payin file: missing required columns: {', '.join(missing)}")
    if col_dt is None:
        raise RuntimeError("payin file: missing required column datetime")

    df["_dt"] = parse_dt_series_msk(df[col_dt])
    df = df[df["_dt"].notna()].copy()

    df["_partner"] = df[col_partner].fillna("").astype(str)
    df["_partner_norm"] = df["_partner"].map(normalize_partner_name)
    df["_status_raw"] = df[col_status].fillna("").astype(str)
    df["_status_norm"] = df["_status_raw"].map(_normalize_status)
    df["_status_success"] = df["_status_raw"].map(_status_success)
    df["_status_countable"] = df["_status_raw"].map(_status_countable)
    df["_status_pending"] = df["_status_raw"].map(_status_pending)
    df["_info_norm"] = df[col_info].fillna("").astype(str).str.lower()
    df["_amount"] = _parse_amount_series(df[col_amount]).fillna(0.0)

    end_time = now - timedelta(minutes=runtime["offset_minutes"])
    start_time = end_time - timedelta(minutes=runtime["window_minutes"])
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)].copy()
    df_today = df[df["_dt"] >= today_start].copy()

    partners: List[WalletPartnerStats] = []
    alerts: List[WalletAlertBlock] = []

    analyzer_partners = sorted(set(df_window["_partner_norm"].dropna().tolist()))
    for partner_norm in analyzer_partners:
        if not partner_norm:
            continue

        partner_mask = df["_partner_norm"] == partner_norm
        partner_window = df_window[df_window["_partner_norm"] == partner_norm]
        partner_window_countable = partner_window[partner_window["_status_countable"]]
        total = int(len(partner_window_countable))
        if total == 0:
            continue

        display_partner = (
            df.loc[partner_mask, "_partner"].astype(str).iloc[0]
            if partner_mask.any()
            else partner_norm
        )
        group_name = partner_to_group.get(partner_norm)

        success = int(partner_window_countable["_status_success"].sum())
        conv_pct = round((success / total * 100.0), 1) if total else 0.0
        conv_threshold = conv_thresholds.get(partner_norm)
        min_events = int(min_events_map.get(partner_norm, runtime["min_events"]))
        conv_insufficient = total < min_events
        conv_bad = bool((not conv_insufficient) and (conv_threshold is not None) and (conv_pct < conv_threshold))

        today_part = df_today[df_today["_partner_norm"] == partner_norm]
        today_success = today_part[today_part["_status_success"]]
        amount_today = float(today_success["_amount"].sum())

        default_method = partner_default_method.get(partner_norm, "")
        partner_obj = rules.resolve_partner(display_partner)
        partner_key = partner_obj.partner_key if partner_obj else None

        limit_rule = rules.resolve_limit_rule(
            "daily_max_amount",
            partner_key=partner_key,
            group_key=group_name,
            method_key=(default_method.lower() if default_method else None),
        )

        if limit_rule is None:
            lim = LimitResolved(
                status="MISSING",
                limit_value=None,
                scope=None,
                scope_value=None,
                method=None,
                rule_id=None,
                comment="",
                reason="",
            )
        elif float(limit_rule.limit_value) == 0:
            lim = LimitResolved(
                status="STOP",
                limit_value=float(limit_rule.limit_value),
                scope=limit_rule.scope_type,
                scope_value=limit_rule.scope_key,
                method=limit_rule.method_key,
                rule_id=limit_rule.rule_key,
                comment=limit_rule.comment or "",
                reason="",
            )
        else:
            lim = LimitResolved(
                status="ACTIVE",
                limit_value=float(limit_rule.limit_value),
                scope=limit_rule.scope_type,
                scope_value=limit_rule.scope_key,
                method=limit_rule.method_key,
                rule_id=limit_rule.rule_key,
                comment=limit_rule.comment or "",
                reason="",
            )

        daily_limit = lim.limit_value if lim.status in {"ACTIVE", "STOP"} else None


        if group_name and daily_limit:
            group_partner_norms = set(group_to_partners.get(group_name, []))
            df_group_today = df_today[df_today["_partner_norm"].isin(group_partner_norms)]
            group_amount_today = float(df_group_today[df_group_today["_status_success"]]["_amount"].sum())
            percent_filled = int(group_amount_today / daily_limit * 100) if daily_limit else 0
        else:
            percent_filled = int(amount_today / daily_limit * 100) if daily_limit else 0

        if lim.status == "STOP":
            limit_bad = False
            limit_warn = False
        elif not daily_limit:
            limit_bad = False
            limit_warn = False
        else:
            limit_bad = percent_filled >= 100
            limit_warn = (not limit_bad) and percent_filled >= 90

        one_hour_ago = now - timedelta(hours=1)
        last_hour = df[(df["_partner_norm"] == partner_norm) & (df["_dt"] >= one_hour_ago)]
        last_hour_countable = last_hour[last_hour["_status_countable"]]
        lh_total = int(len(last_hour_countable))
        api_cancel_count = int(last_hour_countable["_info_norm"].str.contains("отмена по api", case=False, na=False).sum())
        api_pct = round((api_cancel_count / lh_total * 100.0), 1) if lh_total else 0.0
        api_threshold = api_thresholds.get(partner_norm)
        api_insufficient = lh_total < min_events
        api_bad = bool((not api_insufficient) and (api_threshold is not None) and (api_pct > api_threshold))

        nok_wallets_count = int(partner_window["_info_norm"].str.contains("нет доступных аккаунтов", case=False, na=False).sum())

        last_success = df[(df["_partner_norm"] == partner_norm) & (df["_status_success"])]
        last_success_at = None if last_success.empty else last_success["_dt"].max().to_pydatetime()

        if last_success_at is None:
            last_success_minutes_ago = None
        else:
            last_success_minutes_ago = _calc_last_success_minutes_ago(now, last_success_at)

        partners.append(
            WalletPartnerStats(
                partner=display_partner,
                group=group_name,
                total_ops=total,
                success_ops=success,
                conversion_pct=conv_pct,
                conversion_threshold_pct=conv_threshold,
                conversion_insufficient=conv_insufficient,
                conversion_bad=conv_bad,
                payin_amount=amount_today,
                daily_limit=daily_limit,
                daily_limit_comment=lim.comment,
                percent_filled=percent_filled,
                limit_bad=limit_bad,
                limit_warn=limit_warn,
                api_cancel_count=api_cancel_count,
                api_total_ops=total,
                api_cancel_pct=api_pct,
                api_cancel_threshold_pct=api_threshold,
                api_insufficient=api_insufficient,
                api_bad=api_bad,
                nok_wallets_count=nok_wallets_count,
                last_success_at=last_success_at,
                last_success_minutes_ago=last_success_minutes_ago,
            )
        )

        alert_lines: List[str] = []
        if conv_bad and conv_threshold is not None:
            alert_lines.append(f"  Конверсия: {conv_pct:.1f}% (< {conv_threshold:.1f}%) — 🔴")
        if api_bad and api_threshold is not None:
            alert_lines.append(f"  Отмен по API: {api_pct:.1f}% (> {api_threshold:.1f}%) — 🔴")
        if limit_bad:
            alert_lines.append(f"  Лимит превышен ({percent_filled}%) — 🔴")
        elif limit_warn:
            alert_lines.append(f"  Лимит почти исчерпан ({percent_filled}%) — 🟡")
        if nok_wallets_count > 0:
            alert_lines.append(f"  Нет доступных аккаунтов: {nok_wallets_count} — 🔴")

        if alert_lines:
            alerts.append(WalletAlertBlock(partner=display_partner, lines=alert_lines))

    partners.sort(key=lambda x: x.partner.lower())
    alerts.sort(key=lambda x: x.partner.lower())

    stuck_payins_count = int(
        df[(df["_status_pending"]) & ((now - df["_dt"]) > timedelta(minutes=runtime["pending_payin_minutes"]))].shape[0]
    )

    stuck_payouts_count = 0
    try:
        dfp = pd.read_excel(payout_path)
        payout_dt_col = _find_col(dfp, ["дата/время создания", "дата/время", "date", "created_at"])
        payout_status_col = _find_col(dfp, ["статус", "status"])
        if payout_dt_col and payout_status_col:
            dfp["_dt"] = parse_dt_series_msk(dfp[payout_dt_col])
            dfp["_status_raw"] = dfp[payout_status_col].fillna("").astype(str)
            stuck_payouts_count = int(
                dfp[
                    dfp["_dt"].notna()
                    & dfp["_status_raw"].map(_status_pending)
                    & ((now - dfp["_dt"]) > timedelta(minutes=runtime["pending_payout_minutes"]))
                ].shape[0]
            )
    except Exception:
        stuck_payouts_count = 0

    return WalletStatsDTO(
        report_day=now.date(),
        generated_at=now,
        window_minutes=runtime["window_minutes"],
        offset_minutes=runtime["offset_minutes"],
        min_events=runtime["min_events"],
        stuck_payins_count=stuck_payins_count,
        stuck_payouts_count=stuck_payouts_count,
        partners=partners,
        alerts=alerts,
    )
