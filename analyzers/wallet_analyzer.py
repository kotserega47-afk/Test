import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync
from utils.logger import logger


CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_WALLET") or os.getenv("TELEGRAM_CHAT_ID")

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "config",
    "wallet_config.yaml"
)

STATE_PATH = Path("/tmp/wallet_alerts_state.json")


def _normalize(v):
    if not isinstance(v, str):
        return ""
    v = v.strip().lower().replace("ё", "е")
    v = v.replace("амобайл", "а-мобайл")
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
    cfg.setdefault("success_window_minutes", 5)
    cfg.setdefault("min_events", 10)
    cfg.setdefault("alert_cooldown_min", 20)
    cfg.setdefault("partners", {})
    cfg.setdefault("groups", {})

    return cfg


def analyze_wallets(payin_path: str):
    cfg = _load_cfg()
    window_min = cfg["window_minutes"]
    offset_min = cfg["offset_minutes"]
    success_window = cfg["success_window_minutes"]
    min_events = cfg["min_events"]

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
    df["_info_norm"] = df[COL_INFO].astype(str).str.lower()

    now = datetime.now(tz)
    end_time = now - timedelta(minutes=offset_min)
    start_time = end_time - timedelta(minutes=window_min)

    # окно для расчёта конверсии
    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)]

    # последние 2 часа — для фильтра "партнера не показывать"
    start_2h = now - timedelta(hours=2)
    df_last2h = df[(df["_dt"] >= start_2h) & (df["_dt"] <= now)]

    # сегодня — для лимитов
    df_today = df[(df["_dt"] >= now.replace(hour=0, minute=0, second=0, microsecond=0)) & (df["_dt"] <= now)]

    partners_cfg = cfg["partners"]
    groups_cfg = cfg.get("groups", {})

    messages = []
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

    for partner_name, settings in partners_cfg.items():

        key_norm = _normalize(partner_name)

        # за окно
        subset = df_window[df_window["_partner_norm"] == key_norm]
        total = len(subset)
        success = subset["_status"].apply(_status_success).sum()
        conv = (success / total * 100) if total else 0

        # за 2 часа — фильтр скрытия
        df_last2h_p = df_last2h[df_last2h["_partner_norm"] == key_norm]
        last2h_total = len(df_last2h_p)

        if last2h_total == 0:
            continue  # скрываем партнёра полностью

        # суммы за сутки
        df_today_p = df_today[df_today["_partner_norm"] == key_norm]
        amount_today = pd.to_numeric(df_today_p[COL_AMOUNT], errors="coerce").sum()

        # --- threshold конверсии ---
        threshold = settings.get("threshold", 0)

        conv_icon = "🟢"
        if conv < threshold * 100:
            conv_icon = "🚨"

        # --- API ошибки ---
        api_keyword = settings.get("api_cancel_keyword", "").lower()
        api_threshold = settings.get("api_cancel_threshold", 100)

        api_total = subset["_info_norm"].str.contains(api_keyword).sum()
        api_rate = (api_total / total * 100) if total else 0

        api_icon = "🟢"
        if api_rate > api_threshold:
            api_icon = "🚨"

        # --- Лимиты ---
        daily_limit = settings.get("daily_max_amount", None)

        # если в группе — заменить групповым лимитом
        for group_name, group_data in groups_cfg.items():
            if partner_name in group_data.get("partners", []):
                daily_limit = group_data.get("daily_max_amount", daily_limit)
                break

        limit_icon = "🟢"
        if daily_limit and amount_today > daily_limit:
            limit_icon = "🚨"

        # --- Сообщение ---
        msg = (
            f"📊 *{partner_name}*\n"
            f"🕒 Окно: {window_min} мин (смещение {offset_min})\n"
            f"Всего операций: {total}\n"
            f"Успешных: {success}\n"
            f"Конверсия: {conv:.1f}% (< {threshold*100:.1f}%) — {conv_icon}\n"
            f"Сумма за сутки: {amount_today:,.0f} / лимит {daily_limit:,.0f} — {limit_icon}\n"
            f"API ошибки: {api_total} шт ({api_rate:.1f}%) — {api_icon}\n"
            f"⏰ {now_iso}"
        )

        messages.append(msg)

    if messages:
        full_message = "📦 *Wallet Analyzer — статистика*\n\n" + "\n\n".join(messages)
        send_message_sync(full_message, chat_id=CHAT_ID)
        logger.info(f"[Analyzer] Отправлено {len(messages)} отчётов партнёров")
