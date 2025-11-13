import os
import sys
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


# === Статусы ===============================================================

def _normalize_status(s: str) -> str:
    return str(s).strip().lower()


def _status_success(s: str) -> bool:
    """Успешный статус — только 'Оплачен'"""
    return _normalize_status(s) == "оплачен"


def _status_countable(s: str) -> bool:
    """Статусы, которые считаются в конверсии"""
    s = _normalize_status(s)
    return s in {"оплачен", "ошибка"}


# === Конфиг =================================================================

def _load_cfg():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("wallet_config.yaml не найден")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg.setdefault("window_minutes", 8)
    cfg.setdefault("offset_minutes", 8)
    cfg.setdefault("min_events", 10)
    cfg.setdefault("partners", {})
    cfg.setdefault("groups", {})

    return cfg


# === Основной анализатор =====================================================

def analyze_wallets(payin_path: str):
    cfg = _load_cfg()
    window_min = cfg["window_minutes"]
    offset_min = cfg["offset_minutes"]

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

    # нормализация дат
    df[COL_DT] = (
        df[COL_DT].astype(str)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    df["_dt"] = pd.to_datetime(df[COL_DT], dayfirst=True, errors="coerce")
    if df["_dt"].dt.tz is None:
        df["_dt"] = df["_dt"].dt.tz_localize(tz, nonexistent="shift_forward")

    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(normalize_partner_name)
    df["_status_raw"] = df[COL_STATUS].astype(str)
    df["_status_success"] = df["_status_raw"].apply(_status_success)
    df["_status_count"] = df["_status_raw"].apply(_status_countable)
    df["_info_norm"] = df[COL_INFO].astype(str).str.lower()

    now = datetime.now(tz)
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

    # временные окна
    end_time = now - timedelta(minutes=offset_min)
    start_time = end_time - timedelta(minutes=window_min)

    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)]
    df_last2h = df[(df["_dt"] >= now - timedelta(hours=2)) & (df["_dt"] <= now)]
    df_today = df[df["_dt"] >= now.replace(hour=0, minute=0, second=0, microsecond=0)]

    partners_cfg = cfg["partners"]
    groups_cfg = cfg["groups"]

    messages = []
    bad = []

    # === цикл по партнёрам ==================================================

    for partner_name, settings in partners_cfg.items():
        key_norm = normalize_partner_name(partner_name)

        # окно конверсии: только считаемые статусы
        sub_all = df_window[df_window["_partner_norm"] == key_norm]
        subset = sub_all[sub_all["_status_count"]]

        total = len(subset)
        success = subset["_status_success"].sum()
        conv = (success / total * 100) if total else 0

        # последние 2 часа
        if len(df_last2h[df_last2h["_partner_norm"] == key_norm]) == 0:
            continue

        # суммы за сутки — только успешные
        today_part = df_today[df_today["_partner_norm"] == key_norm]
        today_success = today_part[today_part["_status_success"]]
        amount_today = pd.to_numeric(today_success[COL_AMOUNT], errors="coerce").sum()

        # пороги
        threshold = settings.get("threshold", 0)
        api_keyword = settings.get("api_cancel_keyword", "").lower()
        api_threshold = settings.get("api_cancel_threshold", 100)

        # конверсия
        conv_bad = conv < threshold * 100
        conv_icon = "🟢" if not conv_bad else "🚨"

        # API ошибки — считаем только среди countable
        api_total = sub_all["_info_norm"].str.contains(api_keyword).sum()
        api_rate = (api_total / total * 100) if total else 0

        api_bad = api_rate > api_threshold
        api_icon = "🟢" if not api_bad else "🚨"

        # === Нет кошельков ===
        nok_wallets_total = sub_all["_info_norm"].str.contains("нет кошельков").sum()
        nok_bad = nok_wallets_total > 0
        nok_icon = "🟢" if not nok_bad else "🚨"

        # лимиты
        daily_limit = settings.get("daily_max_amount")
        group_name = None

        for gname, gdata in groups_cfg.items():
            if partner_name in gdata["partners"]:
                group_name = gname
                daily_limit = gdata["daily_max_amount"]
                break

        # процент лимита
        if group_name:
            group_partners = groups_cfg[group_name]["partners"]
            df_group_today = df_today[
                df_today["_partner_norm"].isin([normalize_partner_name(p) for p in group_partners])
            ]
            df_group_success = df_group_today[df_group_today["_status_success"]]
            group_amount_today = pd.to_numeric(df_group_success[COL_AMOUNT], errors="coerce").sum()
            percent_filled = int(group_amount_today / daily_limit * 100)
        else:
            percent_filled = int(amount_today / daily_limit * 100) if daily_limit else 0

        # уровни лимита
        limit_bad = False
        limit_warn = False

        if daily_limit:
            if percent_filled >= 100:
                limit_bad = True
                limit_icon = "🚨"
            elif percent_filled >= 90:
                limit_warn = True
                limit_icon = "🟡"
            else:
                limit_icon = "🟢"
        else:
            limit_icon = "🟢"

        # последняя операция
        last_op_time = df[df["_partner_norm"] == key_norm]["_dt"].max()
        last_op_str = last_op_time.strftime("%d.%m %H:%M:%S")

        # сообщение
        msg = (
            f"📊 *{partner_name}*\n"
            f"🕒 Окно: {window_min} мин (смещение {offset_min})\n"
            f"Всего операций: {total}\n"
            f"Успешных: {success}\n"
            f"Конверсия: {conv:.1f}% (< {threshold*100:.1f}%) — {conv_icon}\n"
            f"Сумма за сутки: {amount_today:,.0f} / лимит {daily_limit:,.0f} ({percent_filled}%) — {limit_icon}\n"
            f"API ошибки: {api_total} шт ({api_rate:.1f}%) — {api_icon}\n"
            f"Нет кошельков: {nok_wallets_total} шт — {nok_icon}\n"
            f"Последняя операция: {last_op_str}\n"
            f"⏰ {now_iso}"
        )

        messages.append(msg)

        # классификация BAD
        if total > 0:
            is_bad = False
            if conv_bad:
                is_bad = True
            if api_bad:
                is_bad = True
            if limit_bad:
                is_bad = True
            if limit_warn:
                is_bad = True
            if nok_bad:
                is_bad = True

            if is_bad:
                bad.append({
                    "name": partner_name,
                    "conv": conv,
                    "threshold": threshold,
                    "api_rate": api_rate,
                    "api_threshold": api_threshold,
                    "limit_bad": limit_bad,
                    "limit_warn": limit_warn,
                    "percent": percent_filled,
                    "conv_bad": conv_bad,
                    "api_bad": api_bad,
                    "nok_bad": nok_bad,
                    "nok_count": nok_wallets_total,
                })

    # отправка основного блока
    if messages:
        send_message_sync(
            "📦 *Wallet Analyzer — статистика*\n\n" + "\n\n".join(messages),
            chat_id=CHAT_ID
        )

    # отправка BAD-блока
    if not bad:
        send_message_sync("🟢 *Все партнёры в норме!*", chat_id=CHAT_ID)
    else:
        lines = ["❗ *Обнаружены отклонения:*"]
        for p in bad:
            block = f"\n📊 *{p['name']}*\n"
            if p["conv_bad"]:
                block += (
                    f"Конверсия: {p['conv']:.1f}% "
                    f"(< {p['threshold']*100:.1f}%) — 🚨\n"
                )
            if p["api_bad"]:
                block += (
                    f"API ошибки: {p['api_rate']:.1f}% "
                    f"(> {p['api_threshold']}%) — 🚨\n"
                )
            if p["limit_bad"]:
                block += "Лимит превышен — 🚨\n"
            if p["nok_bad"]:
                block += f"Нет кошельков: {p['nok_count']} — 🚨\n"
            if p["limit_warn"]:
                block += f"Лимит почти исчерпан ({p['percent']}%) — 🟡\n"

            lines.append(block)

        send_message_sync("\n".join(lines), chat_id=CHAT_ID)
