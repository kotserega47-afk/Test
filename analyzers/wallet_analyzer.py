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
from load_data import normalize_partner_name


CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_WALLET") or os.getenv("TELEGRAM_CHAT_ID")

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "config",
    "wallet_config.yaml"
)

STATE_PATH = Path("/tmp/wallet_alerts_state.json")


def _status_success(s):
    s = normalize_partner_name(s)
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

    logger.info(f"[Analyzer] Загружаю PayIn: {payin_path}")

    # === Загрузка файла ===
    try:
        df = pd.read_excel(payin_path)
    except Exception as e:
        send_message_sync(f"⚠️ Не удалось прочитать PayIn: {e}", chat_id=CHAT_ID)
        return

    if df.empty:
        logger.info("[Analyzer] PayIn пуст — выходим")
        return

    # === Колонки ===
    COL_DT = "Дата/Время создания"
    COL_PARTNER = "Партнер"
    COL_STATUS = "Статус"
    COL_INFO = "Инфо"
    COL_AMOUNT = "Сумма"

    # === Нормализация дат и партнёров ===
    tz = pytz.timezone("Europe/Moscow")

    df[COL_DT] = (
        df[COL_DT]
        .astype(str)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    df["_dt"] = pd.to_datetime(df[COL_DT], dayfirst=True, errors="coerce")

    if df["_dt"].dt.tz is None:
        df["_dt"] = df["_dt"].dt.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")

    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(normalize_partner_name)
    df["_status"] = df[COL_STATUS].astype(str)
    df["_info_norm"] = df[COL_INFO].astype(str).str.lower()

    # === Временные промежутки ===
    now = datetime.now(tz)
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

    end_time = now - timedelta(minutes=offset_min)
    start_time = end_time - timedelta(minutes=window_min)

    # окно конверсии
    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)]

    # последние 2 часа
    start_2h = now - timedelta(hours=2)
    df_last2h = df[(df["_dt"] >= start_2h) & (df["_dt"] <= now)]

    # за сутки
    df_today = df[df["_dt"] >= now.replace(hour=0, minute=0, second=0, microsecond=0)]

    partners_cfg = cfg["partners"]
    groups_cfg = cfg["groups"]

    messages = []
    bad = []   # список проблемных партнёров

    # === Основной цикл по партнёрам ===
    for partner_name, settings in partners_cfg.items():

        key_norm = normalize_partner_name(partner_name)

        # окно конверсии
        subset = df_window[df_window["_partner_norm"] == key_norm]
        total = len(subset)
        success = subset["_status"].apply(_status_success).sum()
        conv = (success / total * 100) if total else 0

        # последние 2 часа
        last2h_total = len(df_last2h[df_last2h["_partner_norm"] == key_norm])
        if last2h_total == 0:
            continue  # скрыть партнёра полностью

        # суммы за сутки
        today_part = df_today[df_today["_partner_norm"] == key_norm]
        amount_today = pd.to_numeric(today_part[COL_AMOUNT], errors="coerce").sum()

        # === Пороги ===
        threshold = settings.get("threshold", 0)
        api_keyword = settings.get("api_cancel_keyword", "").lower()
        api_threshold = settings.get("api_cancel_threshold", 100)

        # === Конверсия ===
        conv_bad = conv < threshold * 100
        conv_icon = "🟢" if not conv_bad else "🚨"

        # === API ошибки ===
        api_total = subset["_info_norm"].str.contains(api_keyword).sum()
        api_rate = (api_total / total * 100) if total else 0

        api_bad = api_rate > api_threshold
        api_icon = "🟢" if not api_bad else "🚨"

        # === Лимиты ===
        daily_limit = settings.get("daily_max_amount")
        group_name = None
        group_daily_limit = None

        # группа?
        for gname, gdata in groups_cfg.items():
            if partner_name in gdata.get("partners", []):
                group_name = gname
                group_daily_limit = gdata.get("daily_max_amount")
                break

        if group_daily_limit is not None:
            daily_limit = group_daily_limit

        # процент заполненности лимита
        if daily_limit:
            if group_name:  # групповой лимит
                group_partners = groups_cfg[group_name]["partners"]
                df_group_today = df_today[
                    df_today["_partner_norm"].isin([normalize_partner_name(p) for p in group_partners])
                ]
                group_amount_today = pd.to_numeric(df_group_today[COL_AMOUNT], errors="coerce").sum()
                percent_filled = int(group_amount_today / daily_limit * 100)
            else:  # индивидуальный лимит
                percent_filled = int(amount_today / daily_limit * 100)
        else:
            percent_filled = 0

        # уровни лимита:
        # 🟢 < 90%
        # 🟡 >= 90%
        # 🚨 превышен
        limit_bad = False
        limit_prewarning = False

        if daily_limit:
            if amount_today > daily_limit:
                limit_bad = True
                limit_icon = "🚨"
            elif percent_filled >= 90:
                limit_prewarning = True
                limit_icon = "🟡"
            else:
                limit_icon = "🟢"
        else:
            limit_icon = "🟢"

        # === Последняя операция ===
        df_partner_all = df[df["_partner_norm"] == key_norm]
        last_op_time = df_partner_all["_dt"].max()
        last_op_str = last_op_time.strftime("%d.%m %H:%M:%S")

        # === Формирование сообщения ===
        msg = (
            f"📊 *{partner_name}*\n"
            f"🕒 Окно: {window_min} мин (смещение {offset_min})\n"
            f"Всего операций: {total}\n"
            f"Успешных: {success}\n"
            f"Конверсия: {conv:.1f}% (< {threshold * 100:.1f}%) — {conv_icon}\n"
            f"Сумма за сутки: {amount_today:,.0f} / лимит {daily_limit:,.0f} ({percent_filled}%) — {limit_icon}\n"
            f"API ошибки: {api_total} шт ({api_rate:.1f}%) — {api_icon}\n"
            f"Последняя операция: {last_op_str}\n"
            f"⏰ {now_iso}"
        )

        messages.append(msg)

        # === Классификация BAD ===
        has_ops = total > 0
        is_bad = False

        if has_ops:
            if conv_bad:
                is_bad = True
            if api_bad:
                is_bad = True
            if limit_bad:
                is_bad = True
            if limit_prewarning:
                is_bad = True

        if is_bad:
            bad.append({
                "name": partner_name,
                "conv": conv,
                "threshold": threshold,
                "api_rate": api_rate,
                "api_threshold": api_threshold,
                "limit_bad": limit_bad,
                "limit_prewarning": limit_prewarning,
                "percent": percent_filled,
                "conv_bad": conv_bad,
                "api_bad": api_bad,
            })

    # === Основное сообщение ===
    if messages:
        full_message = "📦 *Wallet Analyzer — статистика*\n\n" + "\n\n".join(messages)
        send_message_sync(full_message, chat_id=CHAT_ID)
        logger.info(f"[Analyzer] Отправлено {len(messages)} отчётов партнёров")

    # === Второе сообщение: BAD ===
    if len(bad) == 0:
        send_message_sync("🟢 *Все партнёры в норме!*", chat_id=CHAT_ID)
    else:
        summary = ["❗ *Обнаружены отклонения:*"]
        for p in bad:
            block = f"\n📊 *{p['name']}*\n"
            if p["conv_bad"]:
                block += f"Конверсия: {p['conv']:.1f}% (< {p['threshold']*100:.1f}%) — 🚨\n"
            if p["api_bad"]:
                block += f"API ошибки: {p['api_rate']:.1f}% (> {p['api_threshold']}%) — 🚨\n"
            if p["limit_bad"]:
                block += f"Лимит превышен — 🚨\n"
            if p["limit_prewarning"]:
                block += f"Лимит почти исчерпан ({p['percent']}%) — 🟡\n"
            summary.append(block)

        send_message_sync("\n".join(summary), chat_id=CHAT_ID)
