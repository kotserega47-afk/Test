# coding: utf-8
"""
Raccoon 10-min PayIn report (WalletReporter) — by-method layout from PayIn data;
conversion thresholds from rules.xlsx (thresholds_partner).
"""

import os
from datetime import datetime, timedelta
import pandas as pd
from zoneinfo import ZoneInfo
import json
import hashlib
from pathlib import Path

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from integrations.telegram_bot import send_message_sync
from utils.normalization import normalize_partner_name
from core.rules_provider import get_rules_snapshot

icon, name = LOG_PROFILES["RACCOON_HOURLY"]
logger = get_logger(name, icon)

# ---------------------------------------
# Константы
# ---------------------------------------
MSK = ZoneInfo("Europe/Moscow")
BASE_DIR = "/tmp/hourly_raccoon"
STATE_PATH = os.path.join(BASE_DIR, "last_sent.json")
METHOD_COLUMN = "Метод пополнения"
METHOD_EMPTY_LABEL = "Без метода"
SUCCESS_STATUS = "оплачен"
PENDING_STATUS = "ожидает оплаты"
CONVERSION_MIN_OPS = 10
CONVERSION_DROP_PP = 20
THRESHOLDS_ANALYZER = "raccoon_wallet"
WARN_DEDUP_PATH = os.path.join(BASE_DIR, "conversion_warn_dedup.json")
CONVERSION_ALERT_STATE_PATH = os.path.join(BASE_DIR, "conversion_alert_state.json")
CONVERSION_ALERT_STATE_VERSION = "conv_alert_new_op_v1"

# Версия входа в SHA256 fingerprint: bump при изменении семантики отчёта/состояния skip-send.
FINGERPRINT_VERSION = "unknown_partners_v2"

PAYIN_REPORT_CHAT_ENV = "TELEGRAM_CHAT_ID_HOURLY_RACCOON"
CONVERSION_ALERT_CHAT_ENV = "TELEGRAM_CHAT_ID_RACCOON_WALLET"


def _hourly_chat_id() -> str:
    chat_id = (os.getenv(PAYIN_REPORT_CHAT_ENV) or "").strip()
    if not chat_id:
        raise RuntimeError(f"Не задан {PAYIN_REPORT_CHAT_ENV}")
    return chat_id


def _conversion_alert_chat_id() -> str:
    chat_id = (os.getenv(CONVERSION_ALERT_CHAT_ENV) or "").strip()
    if not chat_id:
        raise RuntimeError(f"Не задан {CONVERSION_ALERT_CHAT_ENV}")
    return chat_id


# ---------------------------------------
# Утилиты
# ---------------------------------------

def fmt_int(v):
    """Форматирование чисел для отчёта."""
    try:
        v = int(round(float(v)))
        return f"{v:,}".replace(",", " ")
    except Exception:
        return "0"


def _method_display(val) -> str:
    """Display value для метода пополнения: trim; пусто/NaN → «Без метода»."""
    try:
        if val is None or pd.isna(val):
            return METHOD_EMPTY_LABEL
    except Exception:
        pass
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return METHOD_EMPTY_LABEL
    return s


def _safe_dt_iso(x) -> str:
    try:
        if x is None or pd.isna(x):
            return ""
    except Exception:
        pass
    try:
        return x.isoformat()
    except Exception:
        return str(x)


def _operation_key(norm: str, dt_val, amount) -> str:
    """Stable PayIn row identity for new-operation dedup (status excluded)."""
    amt = round(float(pd.to_numeric(amount, errors="coerce") or 0.0), 2)
    return f"{norm}|{_safe_dt_iso(dt_val)}|{amt}"


def _collect_partner_operation_keys(df: pd.DataFrame) -> dict[str, set[str]]:
    """All PayIn row keys per partner (all statuses)."""
    if df.empty or "norm" not in df.columns:
        return {}

    dt_col = "Дата/Время создания"
    if dt_col not in df.columns:
        return {}

    out: dict[str, set[str]] = {}
    for norm, sub in df.groupby("norm"):
        keys = {
            _operation_key(norm, r[dt_col], r.get("Сумма", 0))
            for _, r in sub.iterrows()
        }
        out[str(norm)] = keys
    return out


def _load_conversion_alert_state() -> dict:
    try:
        if not os.path.exists(CONVERSION_ALERT_STATE_PATH):
            return {}
        with open(CONVERSION_ALERT_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_conversion_alert_state(state: dict) -> None:
    try:
        Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
        with open(CONVERSION_ALERT_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[conversion_monitor] alert state save failed: {e}")


def _conversion_alert_state_for_day(state: dict, day) -> dict:
    """Return alert state scoped to current day; reset partners on day rollover."""
    day_str = str(day)
    if state.get("version") != CONVERSION_ALERT_STATE_VERSION or state.get("day") != day_str:
        return {
            "version": CONVERSION_ALERT_STATE_VERSION,
            "day": day_str,
            "partners": {},
        }
    state.setdefault("partners", {})
    return state


def _has_new_operations(current_keys: set[str], last_alert_keys: list[str] | set[str]) -> bool:
    last = set(last_alert_keys or [])
    return bool(current_keys - last)


def _payin_rows_signature(df: pd.DataFrame) -> str:
    """
    Детерминированная подпись набора оплаченных строк PayIn.
    Нужна, чтобы не считать «без изменений» случаи, когда rows/total/max_dt совпали, а состав строк другой.
    """
    if df.empty:
        return "empty"
    d = df.copy()
    if "norm" not in d.columns and "Партнер" in d.columns:
        d["norm"] = d["Партнер"].astype(str).apply(normalize_partner_name)
    if "norm" not in d.columns:
        return "no_norm"
    amt = pd.to_numeric(d["Сумма"], errors="coerce").fillna(0.0)
    d = d.assign(_amt=amt)
    dt_col = "Дата/Время создания"
    if dt_col not in d.columns:
        return "no_dt"
    lines: list[str] = []
    sub = d[["norm", "_amt", dt_col]].sort_values(["norm", dt_col, "_amt"])
    for _, r in sub.iterrows():
        lines.append(f"{r['norm']}|{round(float(r['_amt']), 2)}|{_safe_dt_iso(r[dt_col])}")
    raw = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _calc_fingerprint(df_payin: pd.DataFrame, df_payout: pd.DataFrame | None, header_date) -> dict:
    def payout_block(df: pd.DataFrame | None) -> dict:
        if df is None or df.empty:
            return {"rows": 0, "total": 0.0, "max_dt": ""}
        max_dt = df["Дата/Время создания"].max() if "Дата/Время создания" in df.columns else None
        total = float(df["Сумма"].sum()) if "Сумма" in df.columns else 0.0
        return {
            "rows": int(len(df)),
            "total": round(total, 2),
            "max_dt": _safe_dt_iso(max_dt),
        }

    if df_payin is None or df_payin.empty:
        payin_block = {"rows": 0, "total": 0.0, "max_dt": "", "rows_sig": "empty"}
    else:
        max_dt = df_payin["Дата/Время создания"].max() if "Дата/Время создания" in df_payin.columns else None
        total = float(df_payin["Сумма"].sum()) if "Сумма" in df_payin.columns else 0.0
        payin_block = {
            "rows": int(len(df_payin)),
            "total": round(total, 2),
            "max_dt": _safe_dt_iso(max_dt),
            "rows_sig": _payin_rows_signature(df_payin),
        }

    payload = {
        "day": str(header_date),
        "payin": payin_block,
        "payout": payout_block(df_payout),
    }

    hash_input = {"fingerprint_version": FINGERPRINT_VERSION, **payload}
    raw = json.dumps(hash_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload["hash"] = hashlib.sha256(raw).hexdigest()
    return payload


def _load_last_state() -> dict:
    try:
        if not os.path.exists(STATE_PATH):
            return {}
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_last_state(state: dict) -> None:
    try:
        Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[raccoon_hourly_report] failed to save state: {e}")


def filter_dt(df, col, start_dt, end_dt):
    df = df.copy()
    s = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    if getattr(s.dt, "tz", None) is None:
        s = s.dt.tz_localize(MSK)
    else:
        s = s.dt.tz_convert(MSK)

    df[col] = s
    return df[(df[col] >= start_dt) & (df[col] <= end_dt)]


def load_hourly_files():
    payin_p = os.path.join(BASE_DIR, "payin.xlsx")

    if not os.path.exists(payin_p):
        raise FileNotFoundError(payin_p)

    return pd.read_excel(payin_p, dtype=str)


def get_time_window(now: datetime | None = None):
    now = now or datetime.now(MSK)
    today = now.date()

    end_now = now.replace(second=0, microsecond=0)

    if now.hour == 0 and now.minute <= 2:
        day = today - timedelta(days=1)
        start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=MSK)
        end = datetime(day.year, day.month, day.day, 23, 59, tzinfo=MSK)
        header_date = day
        return start, end, header_date

    start = datetime(today.year, today.month, today.day, 0, 0, tzinfo=MSK)
    end = end_now.replace(tzinfo=MSK) if end_now.tzinfo is None else end_now.astimezone(MSK)
    header_date = today
    return start, end, header_date


def _conversion_status_norm(s) -> str:
    return str(s).strip().lower()


def _load_payin_window(start_dt, end_dt) -> pd.DataFrame:
    """PayIn за окно: все статусы (для conversion monitor + база для отчёта)."""
    df_payin = load_hourly_files()
    df_payin["norm"] = df_payin["Партнер"].astype(str).apply(normalize_partner_name)
    df_payin = filter_dt(df_payin, "Дата/Время создания", start_dt, end_dt)
    df_payin["Сумма"] = pd.to_numeric(df_payin["Сумма"], errors="coerce").fillna(0)
    if METHOD_COLUMN in df_payin.columns:
        df_payin["method_display"] = df_payin[METHOD_COLUMN].map(_method_display)
    else:
        df_payin["method_display"] = METHOD_EMPTY_LABEL
    return df_payin


def load_partner_conversion_thresholds() -> dict[str, float]:
    """norm → threshold_min (%) из rules.xlsx / thresholds_partner (runtime snapshot)."""
    try:
        path = get_rules_snapshot(force_sync=False).local_path
        df = pd.read_excel(path, sheet_name="thresholds_partner")
    except Exception as e:
        logger.warning(f"[conversion_monitor] thresholds_partner read failed: {e}")
        return {}

    if df.empty:
        return {}

    df = df.copy()
    df["enabled"] = pd.to_numeric(df.get("enabled"), errors="coerce").fillna(0).astype(int)
    df["analyzer"] = df.get("analyzer").astype(str).str.strip().str.lower()
    df["partner"] = df.get("partner").astype(str).str.strip()
    df["metric"] = df.get("metric").astype(str).str.strip().str.lower()
    df["threshold"] = pd.to_numeric(df.get("threshold"), errors="coerce")
    df["threshold_min"] = pd.to_numeric(df.get("threshold_min"), errors="coerce")

    df = df[(df["enabled"] == 1) & (df["analyzer"] == THRESHOLDS_ANALYZER)]
    if df.empty:
        return {}

    out: dict[str, float] = {}
    for _, r in df.iterrows():
        metric = str(r["metric"] or "").strip().lower()
        if metric == "threshold":
            metric = "conversion_rate"
        if metric != "conversion_rate":
            continue
        p_raw = str(r["partner"] or "").strip()
        if not p_raw:
            continue
        v = r["threshold_min"]
        if pd.isna(v):
            v = r["threshold"]
        if pd.isna(v):
            continue
        out[normalize_partner_name(p_raw)] = float(v)
    return out


def build_conversion_facts(df: pd.DataFrame) -> list[dict]:
    """
    Rolling-window конверсия по партнёру: последние 10 non-pending операций
    (по «Дата/Время создания» desc). conversion = paid_count / 10 * 100.
    """
    if df.empty or "Статус" not in df.columns:
        return []

    dt_col = "Дата/Время создания"
    if dt_col not in df.columns:
        return []

    d = df.copy()
    d["_status"] = d["Статус"].map(_conversion_status_norm)
    countable = d[d["_status"] != PENDING_STATUS]
    if countable.empty:
        return []

    facts: list[dict] = []
    for norm, sub in countable.groupby("norm"):
        label = str(sub["Партнер"].dropna().astype(str).iloc[0]) if len(sub) else "?"
        window_ops = int(len(sub))
        last_n = (
            sub.sort_values(dt_col, ascending=False)
            .head(CONVERSION_MIN_OPS)
        )
        rolling_ready = len(last_n) >= CONVERSION_MIN_OPS
        paid_in_window = int((last_n["_status"] == SUCCESS_STATUS).sum()) if rolling_ready else 0
        conv = (paid_in_window / CONVERSION_MIN_OPS * 100.0) if rolling_ready else None

        facts.append({
            "norm": norm,
            "partner": label,
            "operations_count": CONVERSION_MIN_OPS if rolling_ready else window_ops,
            "window_non_pending_count": window_ops,
            "conversion_pct": conv,
            "rolling_ready": rolling_ready,
        })
    return facts


def _hour_bucket(now: datetime) -> str:
    n = now.astimezone(MSK) if now.tzinfo else now.replace(tzinfo=MSK)
    return n.strftime("%Y-%m-%dT%H")


def _load_warn_dedup_state() -> dict:
    try:
        if not os.path.exists(WARN_DEDUP_PATH):
            return {}
        with open(WARN_DEDUP_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_warn_dedup_state(state: dict) -> None:
    try:
        Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
        with open(WARN_DEDUP_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[conversion_monitor] warn dedup save failed: {e}")


def _get_warn_sent_norms(hour_bucket: str) -> set[str]:
    st = _load_warn_dedup_state()
    if st.get("hour") != hour_bucket:
        return set()
    return set(st.get("sent") or [])


def _format_conversion_alert(partner: str, conv: float, threshold: float) -> str:
    return (
        f"🔴 Падение конверсии: {partner}\n"
        f"Конверсия последних {CONVERSION_MIN_OPS} операций: {conv:.1f}%\n"
        f"Порог: {threshold:.1f}%"
    )


def _format_missing_threshold_warning(partner: str, ops: int) -> str:
    return (
        f"⚠️ Нет порога конверсии: {partner}\n"
        f"Операций: {ops}"
    )


def run_conversion_monitor(df_window: pd.DataFrame, now: datetime | None = None) -> None:
    """Проверка конверсии после обновления payin; conversion alert dedup по новым операциям."""
    facts = build_conversion_facts(df_window)
    if not facts:
        return

    thresholds = load_partner_conversion_thresholds()
    now = now or datetime.now(MSK)
    hour_bucket = _hour_bucket(now)
    sent_warn_norms = _get_warn_sent_norms(hour_bucket)
    alert_chat_id = _conversion_alert_chat_id()

    partner_keys = _collect_partner_operation_keys(df_window)
    alert_state = _conversion_alert_state_for_day(_load_conversion_alert_state(), now.date())
    alert_state_dirty = False

    for fact in facts:
        norm = fact["norm"]
        partner = fact["partner"]
        window_ops = int(fact.get("window_non_pending_count", 0))
        threshold = thresholds.get(norm)

        if threshold is None:
            if window_ops < 1:
                continue
            current_keys = partner_keys.get(norm, set())
            partner_state = alert_state["partners"].get(norm, {})
            last_warn_keys = partner_state.get("last_warn_operation_keys", [])
            if not _has_new_operations(current_keys, last_warn_keys):
                logger.info(
                    f"[conversion_monitor] skip missing-threshold warn: no new operations "
                    f"partner={partner!r} norm={norm!r}"
                )
                continue
            if norm in sent_warn_norms:
                continue
            send_message_sync(
                _format_missing_threshold_warning(partner, window_ops),
                chat_id=alert_chat_id,
            )
            sent_warn_norms.add(norm)
            updated = dict(partner_state)
            updated["last_warn_operation_keys"] = sorted(current_keys)
            alert_state["partners"][norm] = updated
            alert_state_dirty = True
            continue

        if not fact.get("rolling_ready"):
            continue

        conv = fact["conversion_pct"]
        if conv is not None and conv <= threshold - CONVERSION_DROP_PP:
            current_keys = partner_keys.get(norm, set())
            partner_state = alert_state["partners"].get(norm, {})
            last_alert_keys = partner_state.get("last_alert_operation_keys", [])
            if not _has_new_operations(current_keys, last_alert_keys):
                logger.info(
                    f"[conversion_monitor] skip alert: no new operations partner={partner!r} "
                    f"norm={norm!r} conv={conv:.1f}%"
                )
                continue
            send_message_sync(
                _format_conversion_alert(partner, conv, threshold),
                chat_id=alert_chat_id,
            )
            updated = dict(partner_state)
            updated["last_alert_operation_keys"] = sorted(current_keys)
            alert_state["partners"][norm] = updated
            alert_state_dirty = True

    if alert_state_dirty:
        _save_conversion_alert_state(alert_state)

    _save_warn_dedup_state({"hour": hour_bucket, "sent": sorted(sent_warn_norms)})


def aggregate_payin_by_method(df_payin: pd.DataFrame) -> list[dict]:
    """Группировка: method_display → партнёры (по norm), суммы, сортировка по убыванию."""
    if df_payin.empty:
        return []

    agg = df_payin.groupby(["method_display", "norm"], as_index=False).agg(
        amount=("Сумма", "sum"),
        partner_label=("Партнер", lambda s: str(s.dropna().astype(str).iloc[0]) if len(s) else "?"),
    )
    method_totals = agg.groupby("method_display")["amount"].sum()
    methods_sorted = method_totals.sort_values(ascending=False).index.tolist()

    blocks: list[dict] = []
    for method in methods_sorted:
        sub = agg[agg["method_display"] == method].sort_values("amount", ascending=False)
        blocks.append({
            "method": method,
            "total": float(method_totals[method]),
            "partners": [
                {"title": str(row["partner_label"]), "amount": float(row["amount"])}
                for _, row in sub.iterrows()
            ],
        })
    return blocks


def format_report(method_blocks, header_date, end_dt, total_payin):
    lines = []
    lines.append(f"Итого поступления: {fmt_int(total_payin)}")
    lines.append("")
    lines.append(f"📊 {header_date.strftime('%d.%m')} | 00:00–{end_dt.strftime('%H:%M')} (накопительно)")
    lines.append("")
    lines.append("_______________________")
    lines.append("")

    for i, block in enumerate(method_blocks):
        lines.append(f"{block['method']} — {fmt_int(block['total'])}")
        lines.append("")
        for p in block["partners"]:
            lines.append(f"{p['title']} — {fmt_int(p['amount'])}")
        if i < len(method_blocks) - 1:
            lines.append("")

    return "\n".join(lines)


def run_hourly_report():
    start_dt, end_dt, header_date = get_time_window()

    df_window = _load_payin_window(start_dt, end_dt)
    run_conversion_monitor(df_window)

    df_payin = df_window[df_window["Статус"].map(_conversion_status_norm) == SUCCESS_STATUS].copy()

    if df_payin.empty:
        logger.info(f"[raccoon_hourly_report] interval empty ({start_dt:%d.%m %H:%M}–{end_dt:%H:%M}) -> skip send")
        return None

    cur_state = _calc_fingerprint(df_payin, None, header_date)
    last_state = _load_last_state()

    if last_state.get("hash") == cur_state.get("hash"):
        logger.info(
            f"[raccoon_hourly_report] no changes since last send "
            f"({start_dt:%d.%m %H:%M}–{end_dt:%H:%M}) hash={cur_state.get('hash', '')[:8]} -> skip report"
        )
        return None

    method_blocks = aggregate_payin_by_method(df_payin)
    total_payin = df_payin["Сумма"].sum()

    txt = format_report(method_blocks, header_date, end_dt, total_payin)

    send_message_sync(txt, chat_id=_hourly_chat_id())
    logger.info("[raccoon_hourly_report] Отчёт отправлен")
    _save_last_state(cur_state)

    return txt
