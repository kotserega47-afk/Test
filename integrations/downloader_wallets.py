import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path
import re

import pandas as pd
import yaml
import pytz

# добавляем корень проекта в PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync
from utils.logger import logger
from excel_utils import (
    load_payin,
    COL_PARTNER,
    COL_DATETIME,
    COL_STATUS,
    COL_INFO,
    COL_AMOUNT,
)

# -------------------------------
#  Пути и конфиг
# -------------------------------

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_WALLET") or os.getenv("TELEGRAM_CHAT_ID")

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "config",
    "wallet_config.yaml",
)

STATE_PATH = Path("/tmp/wallet_alerts_state.json")  # сейчас не используется, оставим на будущее


def _now_msk():
    tz = pytz.timezone("Europe/Moscow")
    return datetime.now(tz)


# -------------------------------
#  Утилиты нормализации
# -------------------------------

def _normalize(v):
    """Нормализуем строки: регистр, ё/е, хвосты в скобках, пробелы, Амобайл→А-мобайл"""
    if not isinstance(v, str):
        return ""
    v = v.strip().lower().replace("ё", "е")

    # удаляем хвосты вида "(123)" или " (7)" в конце
    v = re.sub(r"\(\d+\)$", "", v).strip()

    # Амобайл → А-мобайл
    v = v.replace("амобайл", "а-мобайл")

    # одинарные пробелы
    return " ".join(v.split())


def _status_success(s):
    """Статус успешной операции"""
    s = _normalize(s)
    return s in {"оплачен", "успешно", "success", "paid"}


# -------------------------------
#  Конфиг
# -------------------------------

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
    cfg.setdefault("groups", {})

    return cfg


CFG = _load_cfg()


# -------------------------------
#  Основной анализ
# -------------------------------

def analyze_wallets(payin_path: str):
    """Анализ PayIn для Wallet Analyzer и отправка отчёта в Telegram"""

    logger.info("🔍 Начинаю анализ Wallet…")

    # -------------------------------
    #  Загружаем PayIn
    # -------------------------------
    try:
        df = load_payin(payin_path)
    except Exception as e:
        logger.exception(f"❌ Ошибка загрузки PayIn: {e}")
        return

    # нормализация столбцов
    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(_normalize)
    df["_timestamp"] = pd.to_datetime(df[COL_DATETIME], errors="coerce")

    now = _now_msk()
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

    # временные окна
    window_start = now - timedelta(minutes=CFG["window_minutes"])
    success_window_start = now - timedelta(minutes=CFG["success_window_minutes"])
    offset_start = window_start - timedelta(minutes=CFG["offset_minutes"])

    df_window = df[df["_timestamp"] >= offset_start]
    df_today = df[df["_timestamp"].dt.date == now.date()]

    # список сообщений (группы + индивидуальные)
    group_msgs: list[str] = []
    solo_msgs: list[str] = []

    # заранее собираем инфу по группам
    group_limits: dict[str, float] = {}

    for group_name, gdata in CFG.get("groups", {}).items():
        limit = gdata.get("daily_max_amount", 0)
        group_limits[group_name] = limit

    partners_cfg: dict = CFG.get("partners", {})

    # -------------------------------
    #  Формируем единый список партнёров: групповые + индивидуальные
    # -------------------------------
    all_partners: dict[str, dict] = {}

    # групповые партнёры – имя → {"__group__": group_name}
    for group_name, gdata in CFG.get("groups", {}).items():
        for p in gdata.get("partners", []):
            all_partners[p] = {"__group__": group_name}

    # индивидуальные – как есть из partners
    for p, settings in partners_cfg.items():
        all_partners[p] = settings

    # -------------------------------
    #  ОБРАБОТКА КАЖДОГО ПАРТНЁРА
    # -------------------------------
    for partner_name, settings in all_partners.items():
        key_norm = _normalize(partner_name)

        # --- subset окна
        subset = df_window[df_window["_partner_norm"] == key_norm]
        total = len(subset)

        # --- subset успехов (отдельное "success window")
        subset_success = subset[subset["_timestamp"] >= success_window_start]
        success = subset_success[COL_STATUS].apply(_status_success).sum()

        # --- конверсия, %
        conv_pct = (success / total * 100) if total else 0.0

        # -------------------------------
        #  Читаем threshold и API-настройки
        # -------------------------------
        alert_threshold = 0.0          # в долях (0.36 → 36%)
        api_keyword = ""
        api_threshold_pct = 0.0        # уже в процентах

        if "__group__" in settings:
            # это партнёр из группы – ищем его реальные настройки в partners_cfg по нормализованному имени
            matched_cfg = None
            for p_name, p_cfg in partners_cfg.items():
                if _normalize(p_name) == key_norm:
                    matched_cfg = p_cfg
                    break

            if matched_cfg:
                alert_threshold = float(matched_cfg.get("threshold", 0) or 0)
                api_keyword = str(matched_cfg.get("api_cancel_keyword", "") or "").lower()
                api_threshold_pct = float(matched_cfg.get("api_cancel_threshold", 0) or 0)
            else:
                # настроек нет – оставляем по нулям
                alert_threshold = 0.0
                api_keyword = ""
                api_threshold_pct = 0.0
        else:
            # обычный (не групповой) партнёр – берём всё из settings
            alert_threshold = float(settings.get("threshold", 0) or 0)
            api_keyword = str(settings.get("api_cancel_keyword", "") or "").lower()
            api_threshold_pct = float(settings.get("api_cancel_threshold", 0) or 0)

        alert_pct = alert_threshold * 100.0
        status_conv = "🚨 Ниже алерта" if conv_pct < alert_pct else "✅ Выше алерта"

        # -------------------------------
        #  Сумма за сутки
        # -------------------------------
        df_today_p = df_today[df_today["_partner_norm"] == key_norm]

        amount_series = (
            df_today_p[COL_AMOUNT]
            .astype(str)
            .str.replace(" ", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        amount_today = pd.to_numeric(amount_series, errors="coerce").sum()

        # --- лимит по сумме за сутки (daily_max)
        if "__group__" in settings:
            group_name = settings["__group__"]
            daily_limit = float(group_limits.get(group_name, 0) or 0)
        else:
            daily_limit = float(settings.get("daily_max_amount", 0) or 0)

        # -------------------------------
        #  API ошибки
        # -------------------------------
        if api_keyword:
            df_api_errors = subset[
                subset[COL_INFO].astype(str).str.lower().str.contains(api_keyword)
            ]
        else:
            df_api_errors = pd.DataFrame()

        api_count = len(df_api_errors)
        api_pct = (api_count / total * 100) if total else 0.0

        status_api = "🚨 Превышение" if api_pct > api_threshold_pct else "✅ Норма"

        # -------------------------------
        #  ФИЛЬТР: исключаем совсем пустых
        #  (нет операций, нет суммы, нет API-ошибок)
        # -------------------------------
        if total == 0 and amount_today == 0 and api_count == 0:
            continue

        # -------------------------------
        #  Формируем сообщение по партнёру
        # -------------------------------
        msg = (
            f"📊 *{partner_name}*\n"
            f"🕒 Окно: {CFG['window_minutes']} мин (смещение {CFG['offset_minutes']})\n"
            f"Всего операций: {total}\n"
            f"Успешных: {success}\n"
            f"Конверсия: {conv_pct:.1f}% (алерт < {alert_pct:.1f}%) — {status_conv}\n"
            f"Сумма за сутки: {amount_today:,.0f} / лимит {daily_limit:,.0f}\n"
            f"API ошибки: {api_count} шт ({api_pct:.1f}%) — {status_api}\n"
            f"⏰ {now_iso}"
        )

        # групповые наверх, одиночные ниже
        if "__group__" in settings:
            group_msgs.append(msg)
        else:
            solo_msgs.append(msg)

    # -------------------------------
    #  Итоговый текст
    # -------------------------------
    full_text = "📦 *Wallet Analyzer — статистика*\n\n"

    if group_msgs:
        full_text += "\n".join(group_msgs) + "\n\n"

    if solo_msgs:
        full_text += "🔹 *Индивидуальные партнёры*\n\n"
        full_text += "\n\n".join(solo_msgs)

    # -------------------------------
    #  Отправка
    # -------------------------------
    if not full_text.strip():
        logger.info("ℹ️ Нечего отправлять (нет данных по партнёрам).")
        return

    send_message_sync(full_text, chat_id=CHAT_ID)
    logger.info("📤 Wallet статистика отправлена.")
