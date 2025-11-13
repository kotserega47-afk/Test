import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml
import pytz
import re

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync
from utils.logger import logger


CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_WALLET") or os.getenv("TELEGRAM_CHAT_ID")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "config", "wallet_config.yaml")

STATE_PATH = Path("/tmp/wallet_alerts_state.json")


def _now_msk():
    tz = pytz.timezone("Europe/Moscow")
    return datetime.now(tz)


def _normalize(v):
    if not isinstance(v, str):
        return ""
    v = v.strip().lower().replace("ё", "е")

    # 🔥 Удаляем хвосты вида " (123)", "(45)", "(7)"
    v = re.sub(r"\(\d+\)$", "", v).strip()

    # Приводим Амобайл → А-мобайл
    v = v.replace("амобайл", "а-мобайл")

    # Убираем двойные пробелы
    return " ".join(v.split())


def _status_success(s):
    s = _normalize(s)
    return s in {"оплачен", "успешно", "success", "paid"}


def _load_cfg():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("wallet_config.yaml не найден")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg.setdefault("window_minutes", 8)
    cfg.setdefault("offset_minutes", 8)
    cfg.setdefault("min_events", 10)
    cfg.setdefault("success_window_minutes", 5)
    cfg.setdefault("alert_cooldown_min", 20)
    cfg.setdefault("partners", {})

    return cfg


def _load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _save_state(st):
    try:
        STATE_PATH.write_text(json.dumps(st, ensure_ascii=False), "utf-8")
    except Exception:
        pass


def analyze_wallets(payin_path: str):
    cfg = _load_cfg()
    window_min = cfg["window_minutes"]
    offset_min = cfg["offset_minutes"]
    success_window = cfg["success_window_minutes"]

    logger.info(f"[Analyzer] Загружаю PayIn: {payin_path}")

    try:
        df = pd.read_excel(payin_path)
    except Exception as e:
        send_message_sync(f"⚠️ Не удалось прочитать PayIn: {e}", chat_id=CHAT_ID)
        return

    if df.empty:
        logger.info("[Analyzer] PayIn пуст — выходим")
        return

    # колонки
    COL_DT = "Дата/Время создания"
    COL_PARTNER = "Партнер"
    COL_STATUS = "Статус"
    COL_INFO = "Инфо"
    COL_AMOUNT = "Сумма"

    tz = pytz.timezone("Europe/Moscow")
    df["_dt"] = pd.to_datetime(df[COL_DT], format="%d.%m.%Y %H:%M:%S", errors="coerce")

    if df["_dt"].dt.tz is None:
        df["_dt"] = df["_dt"].dt.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")

    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(_normalize)
    df["_status"] = df[COL_STATUS].astype(str)

    now = datetime.now(tz)
    end_time = now - timedelta(minutes=offset_min)
    start_time = end_time - timedelta(minutes=window_min)

    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)]
    start_sw = now - timedelta(minutes=success_window)
    df_success_window = df[(df["_dt"] >= start_sw) & (df["_dt"] <= now)]
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    df_today = df[(df["_dt"] >= start_day) & (df["_dt"] <= now)]

    partners_cfg = cfg["partners"]

    # =============================================
    #  ТЕСТОВЫЙ ВЫВОД ВСЕХ ЦИФР В TELEGRAM
    # =============================================
    messages = []
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

    for partner_name, settings in partners_cfg.items():
        key_norm = _normalize(partner_name)
        subset = df_window[df_window["_partner_norm"] == key_norm]
        total = len(subset)
        success = subset["_status"].apply(_status_success).sum()
        conv = (success / total * 100) if total else 0

        df_today_p = df_today[df_today["_partner_norm"] == key_norm]
        amount_today = pd.to_numeric(df_today_p[COL_AMOUNT], errors="coerce").sum()

        msg = (
            f"📊 *{partner_name}*\n"
            f"🕒 Окно: {window_min} мин (смещение {offset_min})\n"
            f"Всего операций: {total}\n"
            f"Успешных: {success}\n"
            f"Конверсия: {conv:.1f}%\n"
            f"Сумма за сутки: {amount_today:,.2f}\n"
            f"⏰ {now_iso}"
        )
        messages.append(msg)

    if messages:
        full_message = "📦 *Wallet Analyzer — статистика*\n\n" + "\n\n".join(messages)
        send_message_sync(full_message, chat_id=CHAT_ID)
        logger.info(f"[Analyzer] Отправлено {len(messages)} отчётов партнёров")
