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

icon, name = LOG_PROFILES["HOURLY"]
logger = get_logger(name, icon)

# ---------------------------------------
# Константы
# ---------------------------------------
MSK = ZoneInfo("Europe/Moscow")
BASE_DIR = "/tmp/hourly_raccoon"
STATE_PATH = os.path.join(BASE_DIR, "last_sent.json")
CONFIG_PATH = "config/raccoon_hourly_report.yaml"

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_HOURLY_RACCOON")
if not CHAT_ID:
    raise RuntimeError("Не задан TELEGRAM_CHAT_ID_HOURLY_RACCOON")


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
    """Загрузка файлов + нормализация + фильтрация."""
    df_payin = load_hourly_files()

    # нормализация партнёров
    df_payin["norm"] = df_payin["Партнер"].astype(str).apply(normalize_partner_name)

    # фильтрация по дате
    df_payin = filter_dt(df_payin, "Дата/Время создания", start_dt, end_dt)

    # только оплачено
    df_payin = df_payin[df_payin["Статус"].str.lower() == "оплачен"]

    # суммы
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
    send_message_sync(txt, chat_id=CHAT_ID)
    logger.info("[hourly_report] Отчёт отправлен")
    _save_last_state(cur_state)

    return txt
