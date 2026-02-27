# analyzers/wallet_analyzer.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from zoneinfo import ZoneInfo

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from utils.normalization import normalize_partner_name

from core.config_manager import get_thresholds_partner_df


icon, name = LOG_PROFILES["ANALYZER"]
logger = get_logger(name, icon)

MSK = ZoneInfo("Europe/Moscow")


# =============================================================================
# DTOs
# =============================================================================

@dataclass(frozen=True)
class WalletPartnerResult:
    partner_name: str
    total: int
    success: int
    conv_pct: float

    amount_today: float
    daily_limit: float
    percent_filled: int

    api_cancel_total: int
    api_cancel_rate_pct: float

    nok_wallets_total: int
    last_success_dt: Optional[datetime]

    conv_threshold_pct: float
    api_cancel_threshold_pct: float
    min_events: int

    # flags
    conv_bad: bool
    api_bad: bool
    limit_bad: bool
    limit_warn: bool
    nok_bad: bool


@dataclass(frozen=True)
class WalletPendingResult:
    pending_payin_count: int
    pending_payout_count: int
    payin_minutes: int
    payout_minutes: int


@dataclass(frozen=True)
class WalletAnalysisResult:
    ok: bool
    errors: List[str] = field(default_factory=list)

    window_minutes: int = 0
    offset_minutes: int = 0
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    now_dt: Optional[datetime] = None

    partners: List[WalletPartnerResult] = field(default_factory=list)
    pending: Optional[WalletPendingResult] = None

    partners_with_ops: int = 0
    bad_count: int = 0


# =============================================================================
# Helpers
# =============================================================================

def _norm_str(x: Any) -> str:
    return str(x).strip() if x is not None else ""


def _safe_int(x: Any, default: int) -> int:
    try:
        if x is None:
            return default
        if pd.isna(x):
            return default
        return int(float(x))
    except Exception:
        return default


def _safe_float(x: Any, default: float) -> float:
    try:
        if x is None:
            return default
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def _normalize_status(s: Any) -> str:
    return str(s).strip().lower()


def _status_success(s: Any) -> bool:
    return _normalize_status(s) == "оплачен"


def _status_countable(s: Any) -> bool:
    # учитываем только "оплачен" и "ошибка"
    return _normalize_status(s) in {"оплачен", "ошибка"}


def _parse_dt_series_msk(series: pd.Series) -> pd.Series:
    """
    У тебя даты текстом, поэтому:
    - dayfirst=True
    - tz локализуем в MSK
    """
    s = series.astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
    if getattr(dt.dt, "tz", None) is None:
        dt = dt.dt.tz_localize(MSK, nonexistent="shift_forward", ambiguous="NaT")
    else:
        dt = dt.dt.tz_convert(MSK)
    return dt


def _build_thresholds_index(
    df_thr: pd.DataFrame,
    *,
    analyzer_name: str = "wallet",
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    Возвращает lookup:
      (partner_norm, metric) -> row dict
    """
    df = df_thr.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Required-ish columns (best-effort)
    for c in ["partner", "metric", "enabled"]:
        if c not in df.columns:
            raise RuntimeError(f"thresholds_partner missing required column: {c}")

    # normalize fields
    df["partner"] = df["partner"].map(_norm_str)
    df["metric"] = df["metric"].map(lambda s: str(s).strip().lower())
    df["enabled"] = df["enabled"].map(lambda v: _safe_int(v, 0))

    # analyzer optional
    if "analyzer" in df.columns:
        df["analyzer"] = df["analyzer"].map(lambda s: str(s).strip().lower())
        df = df[df["analyzer"] == analyzer_name]

    df = df[df["enabled"] == 1]
    df = df[df["partner"] != ""]
    df = df[df["metric"] != ""]

    idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for _, r in df.iterrows():
        p_norm = normalize_partner_name(r["partner"])
        metric = str(r["metric"]).strip().lower()
        idx[(p_norm, metric)] = r.to_dict()

    return idx


def _get_thr(
    idx: Dict[Tuple[str, str], Dict[str, Any]],
    *,
    partner_norm: str,
    metric: str,
) -> Optional[Dict[str, Any]]:
    return idx.get((partner_norm, metric.strip().lower()))


# =============================================================================
# Main
# =============================================================================

def analyze_wallets(
    payin_path: str,
    payout_path: str,
    *,
    now: Optional[datetime] = None,
    tz: ZoneInfo = MSK,
    force_sync_rules: bool = False,
) -> WalletAnalysisResult:
    """
    Analyzer-only (без Telegram/Dropbox/event_log):
    - берёт thresholds из rules.xlsx (thresholds_partner)
    - читает payin/payout
    - возвращает WalletAnalysisResult
    """

    # --- load thresholds from rules.xlsx ---
    try:
        df_thr = get_thresholds_partner_df(force_sync=force_sync_rules)
        thr_idx = _build_thresholds_index(df_thr, analyzer_name="wallet")
    except Exception as e:
        msg = f"rules thresholds_partner load failed: {e}"
        logger.exception(f"[Analyzer] {msg}")
        return WalletAnalysisResult(ok=False, errors=[msg])

    # defaults (пока не вынесены в rules)
    DEFAULT_WINDOW_MIN = 8
    DEFAULT_OFFSET_MIN = 8
    DEFAULT_MIN_EVENTS = 10

    # window/offset — берём из rules, если есть отдельные метрики (опционально), иначе default
    # (Это НЕ thresholds в cfg, но и не ломает контракт: если метрик нет — дефолт)
    now_dt = now or datetime.now(tz)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=tz)

    window_min = DEFAULT_WINDOW_MIN
    offset_min = DEFAULT_OFFSET_MIN

    end_dt = now_dt - timedelta(minutes=offset_min)
    start_dt = end_dt - timedelta(minutes=window_min)

    # --- read payin ---
    try:
        df = pd.read_excel(payin_path)
    except Exception as e:
        msg = f"payin read failed: {e}"
        logger.exception(f"[Analyzer] {msg}")
        return WalletAnalysisResult(
            ok=False,
            errors=[msg],
            window_minutes=window_min,
            offset_minutes=offset_min,
            start_dt=start_dt,
            end_dt=end_dt,
            now_dt=now_dt,
        )

    if df.empty:
        logger.info("[Analyzer] PayIn пуст — выходим")
        return WalletAnalysisResult(
            ok=True,
            window_minutes=window_min,
            offset_minutes=offset_min,
            start_dt=start_dt,
            end_dt=end_dt,
            now_dt=now_dt,
        )

    # expected columns
    COL_DT = "Дата/Время создания"
    COL_PARTNER = "Партнер"
    COL_STATUS = "Статус"
    COL_INFO = "Инфо"
    COL_AMOUNT = "Сумма"

    missing = [c for c in [COL_DT, COL_PARTNER, COL_STATUS, COL_INFO, COL_AMOUNT] if c not in df.columns]
    if missing:
        msg = f"payin missing columns: {missing}"
        logger.error(f"[Analyzer] {msg}")
        return WalletAnalysisResult(
            ok=False,
            errors=[msg],
            window_minutes=window_min,
            offset_minutes=offset_min,
            start_dt=start_dt,
            end_dt=end_dt,
            now_dt=now_dt,
        )

    # normalize payin
    df = df.copy()
    df["_dt"] = _parse_dt_series_msk(df[COL_DT])
    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(normalize_partner_name)
    df["_status_raw"] = df[COL_STATUS].astype(str)
    df["_status_success"] = df["_status_raw"].apply(_status_success)
    df["_status_count"] = df["_status_raw"].apply(_status_countable)
    df["_info_norm"] = df[COL_INFO].astype(str).str.lower()

    df_window = df[(df["_dt"] >= start_dt) & (df["_dt"] < end_dt)]
    today0 = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    df_today = df[df["_dt"] >= today0]

    # партнеры — строго из thresholds_partner (enabled=1)
    partners_norm = sorted({k[0] for k in thr_idx.keys()})
    if not partners_norm:
        return WalletAnalysisResult(
            ok=True,
            window_minutes=window_min,
            offset_minutes=offset_min,
            start_dt=start_dt,
            end_dt=end_dt,
            now_dt=now_dt,
            partners=[],
            partners_with_ops=0,
            bad_count=0,
        )

    partner_results: List[WalletPartnerResult] = []

    for partner_norm in partners_norm:
        # операционная витрина: используем "каноническое" имя как встретилось в данных (если есть)
        partner_mask = df_window["_partner_norm"] == partner_norm
        sub_all = df_window[partner_mask]
        subset = sub_all[sub_all["_status_count"]]

        total = int(len(subset))
        if total == 0:
            continue

        success = int(subset["_status_success"].sum())
        conv = (success / total * 100.0) if total else 0.0

        # --- thresholds (STRICT CONTRACT) ---
        t_conv = _get_thr(thr_idx, partner_norm=partner_norm, metric="conversion_rate")
        t_api = _get_thr(thr_idx, partner_norm=partner_norm, metric="api_cancel_rate")

        if not t_conv:
            # fail-fast: rule missing
            msg = f"threshold missing: partner='{partner_norm}' metric=conversion_rate"
            logger.error(f"[Analyzer] {msg}")
            return WalletAnalysisResult(
                ok=False,
                errors=[msg],
                window_minutes=window_min,
                offset_minutes=offset_min,
                start_dt=start_dt,
                end_dt=end_dt,
                now_dt=now_dt,
            )

        if not t_api:
            msg = f"threshold missing: partner='{partner_norm}' metric=api_cancel_rate"
            logger.error(f"[Analyzer] {msg}")
            return WalletAnalysisResult(
                ok=False,
                errors=[msg],
                window_minutes=window_min,
                offset_minutes=offset_min,
                start_dt=start_dt,
                end_dt=end_dt,
                now_dt=now_dt,
            )

        conv_threshold_pct = _safe_float(t_conv.get("threshold_min"), 0.0)          # % (50, 35…)
        api_cancel_threshold_pct = _safe_float(t_api.get("threshold_max"), 100.0)  # % (9, 12…)

        # min_events — берём из conversion_rate, fallback на default
        min_events = _safe_int(t_conv.get("min_events"), DEFAULT_MIN_EVENTS)

        # --- conversion flag ---
        if total < min_events:
            conv_bad = False
        else:
            conv_bad = conv < conv_threshold_pct

        # --- today amount ---
        today_part = df_today[df_today["_partner_norm"] == partner_norm]
        today_success = today_part[today_part["_status_success"]]
        amount_today = float(pd.to_numeric(today_success[COL_AMOUNT], errors="coerce").fillna(0).sum())

        # --- API cancel rate (last hour) ---
        error_keyword = "отмена по api"
        one_hour_ago = now_dt - timedelta(hours=1)

        last_hour = df[(df["_partner_norm"] == partner_norm) & (df["_dt"] >= one_hour_ago)]
        lh_countable = last_hour[last_hour["_status_count"]]
        lh_total = int(len(lh_countable))
        lh_papi = int(lh_countable["_info_norm"].str.contains(error_keyword, case=False, na=False).sum())

        if lh_total < min_events:
            api_rate = 0.0
            api_bad = False
            api_total = lh_papi
        else:
            api_rate = (lh_papi / lh_total * 100.0) if lh_total else 0.0
            api_total = lh_papi
            api_bad = api_rate > api_cancel_threshold_pct

        # --- nok accounts ---
        nok_wallets_total = int(sub_all["_info_norm"].str.contains("нет доступных аккаунтов").sum())
        nok_bad = nok_wallets_total > 0

        # --- limits ---
        # Пока заглушка (дальше переносим в wallet_limits rules):
        daily_limit = 0.0
        percent_filled = 0
        limit_bad = False
        limit_warn = False

        # --- last success ---
        last_success = df[(df["_partner_norm"] == partner_norm) & (df["_status_success"])]
        last_success_dt = None if last_success.empty else last_success["_dt"].max()

        # human name (как в данных)
        partner_name = partner_norm
        try:
            # берём первое "сырое" имя из окна
            raw_names = df_window.loc[df_window["_partner_norm"] == partner_norm, COL_PARTNER].astype(str)
            if not raw_names.empty:
                partner_name = raw_names.iloc[0]
        except Exception:
            pass

        partner_results.append(
            WalletPartnerResult(
                partner_name=str(partner_name),
                total=total,
                success=success,
                conv_pct=float(conv),
                amount_today=float(amount_today),
                daily_limit=float(daily_limit),
                percent_filled=int(percent_filled),
                api_cancel_total=int(api_total),
                api_cancel_rate_pct=float(api_rate),
                nok_wallets_total=int(nok_wallets_total),
                last_success_dt=last_success_dt,
                conv_threshold_pct=float(conv_threshold_pct),
                api_cancel_threshold_pct=float(api_cancel_threshold_pct),
                min_events=int(min_events),
                conv_bad=bool(conv_bad),
                api_bad=bool(api_bad),
                limit_bad=bool(limit_bad),
                limit_warn=bool(limit_warn),
                nok_bad=bool(nok_bad),
            )
        )

    # --- pending (пока дефолты, далее тоже в rules) ---
    PAYIN_PENDING_MIN = 10
    PAYOUT_PENDING_MIN = 180

    df_pending_payin = df[
        (df["_status_raw"].astype(str).str.lower() == "ожидает оплаты")
        & ((now_dt - df["_dt"]) > timedelta(minutes=PAYIN_PENDING_MIN))
    ]
    pending_payin_count = int(len(df_pending_payin))

    pending_payout_count = 0
    try:
        dfp = pd.read_excel(payout_path)

        COL_DT_P = "Дата/Время создания"
        COL_STATUS_P = "Статус"

        if not dfp.empty and COL_DT_P in dfp.columns and COL_STATUS_P in dfp.columns:
            dfp = dfp.copy()
            dfp["_dt"] = _parse_dt_series_msk(dfp[COL_DT_P])
            dfp["_status_raw"] = dfp[COL_STATUS_P].astype(str)

            df_pending_payout = dfp[
                (dfp["_status_raw"].astype(str).str.lower() == "ожидает оплаты")
                & ((now_dt - dfp["_dt"]) > timedelta(minutes=PAYOUT_PENDING_MIN))
            ]
            pending_payout_count = int(len(df_pending_payout))

    except Exception as e:
        # не фатал
        logger.warning(f"[Analyzer] payout read/parse failed: {e}")

    bad_count = sum(
        1 for r in partner_results
        if (r.conv_bad or r.api_bad or r.limit_bad or r.limit_warn or r.nok_bad)
    )

    return WalletAnalysisResult(
        ok=True,
        errors=[],
        window_minutes=window_min,
        offset_minutes=offset_min,
        start_dt=start_dt,
        end_dt=end_dt,
        now_dt=now_dt,
        partners=partner_results,
        pending=WalletPendingResult(
            pending_payin_count=pending_payin_count,
            pending_payout_count=pending_payout_count,
            payin_minutes=PAYIN_PENDING_MIN,
            payout_minutes=PAYOUT_PENDING_MIN,
        ),
        partners_with_ops=len(partner_results),
        bad_count=bad_count,
    )