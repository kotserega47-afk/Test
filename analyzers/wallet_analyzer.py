import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import re

import pandas as pd
import yaml
import pytz

# Добавляем корень проекта в PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync
from utils.logger import logger


# -------------------------------
#  Константы и пути
# -------------------------------

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_WALLET") or os.getenv("TELEGRAM_CHAT_ID")

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "config",
    "wallet_config.yaml",
)


def _now_msk() -> datetime:
    tz = pytz.timezone("Europe/Moscow")
    return datetime.now(tz)


# -------------------------------
#  Нормализация
# -------------------------------

def _normalize(v):
    """
    Нормализуем строки:
    - нижний регистр
    - ё → е
    - убираем хвосты вида "(123)" в конце
    - Амобайл → А-мобайл
    - схлопываем пробелы
    """
    if not isinstance(v, str):
        return ""
    v = v.strip().lower().replace("ё", "е")

    # хвосты " (116)" в конце партнёра
    v = re.sub(r"\(\d+\)$", "", v).strip()

    # амобайл → а-мобайл
    v = v.replace("амобайл", "а-мобайл")

    # одинарные пробелы
    return " ".join(v.split())


def _status_success(s: str) -> bool:
    """Преобразуем статус в булево 'успешно/неуспешно'."""
    s = _normalize(s)
    return s in {"оплачен", "успешно", "success", "paid"}


# -------------------------------
#  Загрузка конфига
# -------------------------------

def _load_cfg() -> dict:
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
    """Анализ PayIn для Wallet Analyzer и отправка отчёта в Telegram."""

    logger.info(f"🔍 [WalletAnalyzer] Загружаю PayIn: {payin_path}")

    # ---------- 1. Загрузка PayIn ----------
    try:
        df = pd.read_excel(payin_path)
    except Exception as e:
        logger.exception(f"❌ Не удалось прочитать PayIn: {e}")
        send_message_sync(f"⚠️ Не удалось прочитать PayIn: {e}", chat_id=CHAT_ID)
        return

    if df.empty:
        logger.info("[WalletAnalyzer] PayIn пуст — выходим")
        return

    # ---------- 2. Настройка колонок ----------
    COL_DT = "Дата/Время создания"
    COL_PARTNER = "Партнер"
    COL_STATUS = "Статус"
    COL_INFO = "Инфо"
    COL_AMOUNT = "Сумма"

    tz = pytz.timezone("Europe/Moscow")

    # Дата/время
    df["_dt"] = pd.to_datetime(
        df[COL_DT],
        format="%d.%m.%Y %H:%M:%S",
        errors="coerce",
    )

    # Локализация к Москве, если нет tz
    if df["_dt"].dt.tz is None:
        df["_dt"] = df["_dt"].dt.tz_localize(
            tz, nonexistent="shift_forward", ambiguous="NaT"
        )

    # Нормализованный партнёр и статус
    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(_normalize)
    df["_status"] = df[COL_STATUS].astype(str)

    now = _now_msk()
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

    window_min = CFG["window_minutes"]
    offset_min = CFG["offset_minutes"]
    success_window = CFG["success_window_minutes"]

    # ---------- 3. Временные окна ----------
    # Окно для подсчёта total
    end_time = now - timedelta(minutes=offset_min)
    start_time = end_time - timedelta(minutes=window_min)

    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)]

    # Окно для успехов (последние success_window минут)
    start_sw = now - timedelta(minutes=success_window)
    df_success_window = df[(df["_dt"] >= start_sw) & (df["_dt"] <= now)]

    # Сутки для сумм
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    df_today = df[(df["_dt"] >= start_day) & (df["_dt"] <= now)]

    partners_cfg: dict = CFG.get("partners", {})
    groups_cfg: dict = CFG.get("groups", {})

    # ---------- 4. Подготовка информаций о группах ----------
    group_limits: dict[str, float] = {}
    for group_name, gdata in groups_cfg.items():
        group_limits[group_name] = float(gdata.get("daily_max_amount", 0) or 0)

    # ---------- 5. Формируем общий список партнёров (группы + одиночные) ----------
    all_partners: dict[str, dict] = {}

    # из групп
    for group_name, gdata in groups_cfg.items():
        for p in gdata.get("partners", []):
            all_partners[p] = {"__group__": group_name}

    # индивидуальные
    for p, settings in partners_cfg.items():
        all_partners[p] = settings

    group_msgs = []
    solo_msgs = []

    # ---------- 6. Обработка каждого партнёра ----------
    for partner_name, settings in all_partners.items():
        key_norm = _normalize(partner_name)

        # срез по окну
        subset = df_window[df_window["_partner_norm"] == key_norm]
        total = len(subset)

        # успехи (по отдельному success-window)
        subset_success = df_success_window[df_success_window["_partner_norm"] == key_norm]
        success = subset_success["_status"].apply(_status_success).sum()

        # конверсия, %
        conv_pct = (success / total * 100) if total else 0.0

        # ---------- 6.1. Читаем threshold и API-настройки ----------
        alert_threshold = 0.0      # доли (0.36 → 36%)
        api_keyword = ""
        api_threshold_pct = 0.0    # проценты

        if "__group__" in settings:
            # партнёр из группы → ищем его реальные настройки в partners_cfg по нормализованному имени
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
                alert_threshold = 0.0
                api_keyword = ""
                api_threshold_pct = 0.0
        else:
            # обычный партнёр – в settings уже всё лежит
            alert_threshold = float(settings.get("threshold", 0) or 0)
            api_keyword = str(settings.get("api_cancel_keyword", "") or "").lower()
            api_threshold_pct = float(settings.get("api_cancel_threshold", 0) or 0)

        alert_pct = alert_threshold * 100.0
        status_conv = "🚨 Ниже алерта" if conv_pct < alert_pct else "✅ Выше алерта"

        # ---------- 6.2. Сумма за сутки ----------
        df_today_p = df_today[df_today["_partner_norm"] == key_norm]

        amount_series = (
            df_today_p[COL_AMOUNT]
            .astype(str)
            .str.replace(" ", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        amount_today = pd.to_numeric(amount_series, errors="coerce").sum()

        # дневной лимит
        if "__group__" in settings:
            group_name = settings["__group__"]
            daily_limit = group_limits.get(group_name, 0.0)
        else:
            daily_limit = float(settings.get("daily_max_amount", 0) or 0.0)

        # ---------- 6.3. API-ошибки ----------
        if api_keyword:
            df_api_errors = subset[
                subset[COL_INFO].astype(str).str.lower().str.contains(api_keyword)
            ]
        else:
            df_api_errors = pd.DataFrame()

        api_count = len(df_api_errors)
        api_pct = (api_count / total * 100) if total else 0.0
        status_api = "🚨 Превышение" if api_pct > api_threshold_pct else "✅ Норма"

        # ---------- 6.4. Фильтр: исключаем полностью пустых ----------
        if total == 0 and amount_today == 0 and api_count == 0:
            continue

        # ---------- 6.5. Формируем сообщение ----------
        msg = (
            f"📊 *{partner_name}*\n"
            f"🕒 Окно: {window_min} мин (смещение {offset_min})\n"
            f"Всего операций: {total}\n"
            f"Успешных: {success}\n"
            f"Конверсия: {conv_pct:.1f}% (алерт < {alert_pct:.1f}%) — {status_conv}\n"
            f"Сумма за сутки: {amount_today:,.0f} / лимит {daily_limit:,.0f}\n"
            f"API ошибки: {api_count} шт ({api_pct:.1f}%) — {status_api}\n"
            f"⏰ {now_iso}"
        )

        if "__group__" in settings:
            group_msgs.append(msg)
        else:
            solo_msgs.append(msg)

    # ---------- 7. Итоговое сообщение ----------
    full_text = "📦 *Wallet Analyzer — статистика*\n\n"

    if group_msgs:
        full_text += "\n\n".join(group_msgs) + "\n\n"

    if solo_msgs:
        full_text += "🔹 *Индивидуальные партнёры*\n\n"
        full_text += "\n\n".join(solo_msgs)

    if not group_msgs and not solo_msgs:
        logger.info("ℹ️ Нечего отправлять (нет данных по партнёрам).")
        return

    send_message_sync(full_text, chat_id=CHAT_ID)
    logger.info("📤 Wallet статистика отправлена.")
