# coding: utf-8
"""
Hourly Report — рефакторинг с нормализацией методов и разделением логики.
"""

import os
from datetime import datetime, timedelta
import pandas as pd
import yaml
from zoneinfo import ZoneInfo
import json
import hashlib
from pathlib import Path

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from integrations.telegram_bot import send_message_sync
from utils.normalization import normalize_partner_name
from core.rules_provider import get_rules_snapshot

icon, name = LOG_PROFILES["HOURLY"]
logger = get_logger(name, icon)

# ---------------------------------------
# Константы
# ---------------------------------------
MSK = ZoneInfo("Europe/Moscow")
BASE_DIR = "/tmp/hourly_raccoon"
STATE_PATH = os.path.join(BASE_DIR, "last_sent.json")
CONFIG_PATH = "config/raccoon_hourly_report.yaml"

SUCCESS_STATUS = "оплачен"
PENDING_STATUS = "ожидает оплаты"
CONVERSION_MIN_OPS = 10
CONVERSION_DROP_PP = 20
THRESHOLDS_ANALYZER = "raccoon_wallet"
WARN_DEDUP_PATH = os.path.join(BASE_DIR, "conversion_warn_dedup.json")
CONVERSION_ALERT_STATE_PATH = os.path.join(BASE_DIR, "conversion_alert_state.json")
CONVERSION_ALERT_STATE_VERSION = "conv_alert_new_op_v1"

# Payin cumulative report chat (TELEGRAM_CHAT_ID_HOURLY_RACCOON).
PAYIN_REPORT_CHAT_ENV = "TELEGRAM_CHAT_ID_HOURLY_RACCOON"
# Conversion monitor warnings/alerts (TELEGRAM_CHAT_ID_RACCOON_WALLET).
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
    except:
        return "0"


def normalize_method(v):  # NEW
    """Универсальная нормализация enum метода."""
    if not isinstance(v, str):
        return ""
    return v.strip().upper()


def load_cfg():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

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


def _conversion_status_norm(s) -> str:
    return str(s).strip().lower()


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


def run_conversion_monitor(df_window: pd.DataFrame, now: datetime | None = None) -> None:
    """Проверка конверсии после обновления payin; dedup по новым операциям."""
    facts = build_conversion_facts(df_window)
    if not facts:
        return

    thresholds = load_partner_conversion_thresholds()
    now = now or datetime.now(MSK)
    hour_bucket = _hour_bucket(now)
    sent_warn_norms = _get_warn_sent_norms(hour_bucket)

    partner_keys = _collect_partner_operation_keys(df_window)
    alert_state = _conversion_alert_state_for_day(_load_conversion_alert_state(), now.date())
    alert_state_dirty = False
    alert_chat_id = _conversion_alert_chat_id()

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


def run_conversion_monitor_from_payin(now: datetime | None = None) -> None:
    """Load today's payin window and run conversion monitor (post-download hook)."""
    start_dt, end_dt, _ = get_time_window(now)
    try:
        df_window = _load_payin_window(start_dt, end_dt)
    except FileNotFoundError:
        logger.warning("[conversion_monitor] payin.xlsx missing, skip")
        return
    run_conversion_monitor(df_window, now=now)

def _calc_fingerprint(df_payin: pd.DataFrame, df_payout: pd.DataFrame | None, header_date) -> dict:
    def block(df: pd.DataFrame | None) -> dict:
        if df is None or df.empty:
            return {"rows": 0, "total": 0.0, "max_dt": ""}
        max_dt = df["Дата/Время создания"].max() if "Дата/Время создания" in df.columns else None
        total = float(df["Сумма"].sum()) if "Сумма" in df.columns else 0.0
        return {
            "rows": int(len(df)),
            "total": round(total, 2),
            "max_dt": _safe_dt_iso(max_dt),
        }

    payload = {
        "day": str(header_date),        # фиксируем сутки
        "payin": block(df_payin),
        "payout": block(df_payout),
    }

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
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
        logger.warning(f"[hourly_report] failed to save state: {e}")

def filter_dt(df, col, start_dt, end_dt):
    df = df.copy()
    s = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    # если tz-naive → локализуем; если tz-aware → конвертим
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


    df1 = pd.read_excel(payin_p, dtype=str)

    return df1


def get_time_window(now: datetime | None = None):
    now = now or datetime.now(MSK)
    today = now.date()

    # округляем end до минуты (чтобы окно красиво писалось)
    end_now = now.replace(second=0, microsecond=0)

    # финальный отчёт за вчера в 00:00–00:02 (опционально)
    if now.hour == 0 and now.minute <= 2:
        day = today - timedelta(days=1)
        start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=MSK)
        end = datetime(day.year, day.month, day.day, 23, 59, tzinfo=MSK)
        header_date = day
        return start, end, header_date

    # каждые 5 минут: сегодня 00:00–сейчас
    start = datetime(today.year, today.month, today.day, 0, 0, tzinfo=MSK)
    end = end_now.replace(tzinfo=MSK) if end_now.tzinfo is None else end_now.astimezone(MSK)
    header_date = today
    return start, end, header_date


# ======================================================
#                 ПОДГОТОВКА ДАННЫХ (NEW)
# ======================================================

def prepare_data(start_dt, end_dt):  # NEW
    """Загрузка файлов + нормализация + фильтрация (только оплаченные для отчёта)."""
    df_payin = _load_payin_window(start_dt, end_dt)
    return df_payin[df_payin["Статус"].map(_conversion_status_norm) == SUCCESS_STATUS].copy()


def _load_payin_window(start_dt, end_dt) -> pd.DataFrame:
    """PayIn за окно: все статусы (для conversion monitor + база для отчёта)."""
    df_payin = load_hourly_files()
    df_payin["norm"] = df_payin["Партнер"].astype(str).apply(normalize_partner_name)
    df_payin = filter_dt(df_payin, "Дата/Время создания", start_dt, end_dt)
    df_payin["Сумма"] = pd.to_numeric(df_payin["Сумма"], errors="coerce").fillna(0)
    return df_payin


# ======================================================
#                 АГРЕГАЦИЯ PAYIN (NEW)
# ======================================================

def aggregate_payin(df_payin, payin_cfg, payin_groups):
    result = []

    payin_amounts = {}
    for partner_key in payin_cfg:
        norm = normalize_partner_name(partner_key)
        df_p = df_payin[df_payin["norm"] == norm]
        payin_amounts[partner_key] = df_p["Сумма"].sum()

    printed_groups = set()

    for partner_key, pdata in payin_cfg.items():
        result.append({
            "title": pdata.get("title", partner_key),
            "amount": payin_amounts.get(partner_key, 0),
            "comment": pdata.get("comment"),
            "is_group": False,
        })

        for gkey, gdata in payin_groups.items():
            if gkey in printed_groups:
                continue

            if partner_key not in gdata.get("combine", []):
                continue

            total = sum(payin_amounts.get(x, 0) for x in gdata["combine"])

            result.append({
                "title": gdata.get("title", gkey),
                "amount": total,
                "comment": gdata.get("comment"),  # ← фикс
                "is_group": True,
            })

            printed_groups.add(gkey)

    return result

# ======================================================
#                    ФОРМАТИРОВАНИЕ (NEW)
# ======================================================
def format_section_with_layout(lines, title, data, layout):
    lines.append(title)
    counter = 1

    data_by_title = {item["title"]: item for item in data}

    for block in layout:
        group = block.get("group", [])
        spacing = block.get("spacing", 0)

        for key in group:
            item = data_by_title.get(key)
            if not item:
                continue

            if "methods" in item:  # PAYOUT
                lines.append(f"{counter}) {item['title']}:")
                for m in item["methods"]:
                    # комментарий метода
                    comment = f" {m['comment']}" if m.get("comment") else ""
                    lines.append(f" - {m['title']} – {fmt_int(m['amount'])}{comment}")
            else:  # PAYIN или группа
                comment = f" {item['comment']}" if item.get("comment") else ""
                lines.append(f"{counter}) {item['title']} – {fmt_int(item['amount'])}{comment}")

            counter += 1

        for _ in range(spacing):
            lines.append("")

def format_report(payin_data, header_date, end_dt, total_payin):
    cfg = load_cfg()
    lines = []
    lines.append(f"Итого поступления: {fmt_int(total_payin)}")
    lines.append("")

    lines.append(f"📊 {header_date.strftime('%d.%m')} | 00:00–{end_dt.strftime('%H:%M')} (накопительно)")
    lines.append("")


    # PAYIN
    payin_layout = cfg.get("payin_layout", [])
    lines.append("_______________________")
    lines.append("")
    format_section_with_layout(lines, "Поступления:", payin_data, payin_layout)

    return "\n".join(lines)


# ======================================================
#                 ОСНОВНАЯ ФУНКЦИЯ (REFACTORED)
# ======================================================

def run_hourly_report():

    cfg = load_cfg()
    start_dt, end_dt, header_date = get_time_window()

    # 1) подготовка
    df_payin = prepare_data(start_dt, end_dt)

    # --- Guard №1: если за интервал нет операций вообще — не отправляем ---
    if df_payin.empty:
        logger.info(f"[hourly_report] interval empty ({start_dt:%d.%m %H:%M}–{end_dt:%H:%M}) -> skip send")
        return None

    # --- Guard №2: если отчёт за этот интервал уже отправляли и данные не изменились — не отправляем ---
    cur_state = _calc_fingerprint(df_payin, None, header_date)
    last_state = _load_last_state()

    if last_state.get("hash") == cur_state.get("hash"):
        logger.info(
            f"[hourly_report] no changes since last send "
            f"({start_dt:%d.%m %H:%M}–{end_dt:%H:%M}) hash={cur_state.get('hash', '')[:8]} -> skip"
        )
        return None


    # 2) агрегация
    payin_data = aggregate_payin(df_payin, cfg.get("payin", {}), cfg.get("payin_groups", {}))

    total_payin = df_payin["Сумма"].sum()

    # 3) форматирование
    txt = format_report( payin_data, header_date, end_dt, total_payin)



    # 4) отправка
    send_message_sync(txt, chat_id=_hourly_chat_id())
    logger.info("[hourly_report] Отчёт отправлен")
    _save_last_state(cur_state)

    return txt
