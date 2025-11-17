# coding: utf-8
"""
Hourly Report — финальная версия.
Формирует идеальный отчёт с нужными отступами, группами и форматированием.
"""

import os
from datetime import datetime, timedelta
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from utils.logger import logger
from integrations.telegram_bot import send_message_sync
from load_data import normalize_partner_name


# ---------------------------------------
# Константы
# ---------------------------------------
MSK = ZoneInfo("Europe/Moscow")
BASE_DIR = "/tmp/hourly"
CONFIG_PATH = "config/hourly_report.yaml"

CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID_HOURLY")
    or os.getenv("TELEGRAM_CHAT_ID")
)


# ---------------------------------------
# Утилиты
# ---------------------------------------
def fmt_int(v):
    try:
        v = int(round(float(v)))
        return f"{v:,}".replace(",", " ")
    except:
        return "0"


def load_cfg():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def filter_dt(df, col, start_dt, end_dt):
    df = df.copy()
    df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    def fix(x):
        if pd.isna(x):
            return x
        if x.tzinfo is None:
            return x.replace(tzinfo=MSK)
        return x.astimezone(MSK)

    df[col] = df[col].apply(fix)
    return df[(df[col] >= start_dt) & (df[col] <= end_dt)]


def load_hourly_files():
    payin_p = os.path.join(BASE_DIR, "payin.xlsx")
    payout_p = os.path.join(BASE_DIR, "payout.xlsx")

    if not os.path.exists(payin_p):
        raise FileNotFoundError(payin_p)

    if not os.path.exists(payout_p):
        raise FileNotFoundError(payout_p)

    df1 = pd.read_excel(payin_p, dtype=str)
    df2 = pd.read_excel(payout_p, dtype=str)

    return df1, df2


def get_time_window():
    now = datetime.now(MSK)
    today = now.date()

    if now.hour == 0:
        day = today - timedelta(days=1)
        start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=MSK)
        end   = datetime(day.year, day.month, day.day, 23, 59, tzinfo=MSK)
        header_date = day
    else:
        day = today
        start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=MSK)
        end   = datetime(day.year, day.month, day.day, now.hour, 0, tzinfo=MSK)
        header_date = day

    return start, end, header_date


# ======================================================
#                 ОСНОВНАЯ ФУНКЦИЯ
# ======================================================
def run_hourly_report():

    cfg = load_cfg()

    payout_cfg = cfg.get("payout", {})
    payin_cfg = cfg.get("payin", {})
    payin_groups = cfg.get("payin_groups", {})

    start_dt, end_dt, header_date = get_time_window()

    # ---------------- LOAD ----------------
    df_payin, df_payout = load_hourly_files()

    # нормализация
    df_payin["norm"] = df_payin["Партнер"].astype(str).apply(normalize_partner_name)
    df_payout["norm"] = df_payout["Партнер"].astype(str).apply(normalize_partner_name)

    # дата
    df_payin = filter_dt(df_payin, "Дата/Время создания", start_dt, end_dt)
    df_payout = filter_dt(df_payout, "Дата/Время создания", start_dt, end_dt)

    # оплачено
    df_payin = df_payin[df_payin["Статус"].str.lower() == "оплачен"]
    df_payout = df_payout[df_payout["Статус"].str.lower() == "оплачен"]

    # суммы → numeric
    df_payin["Сумма"] = pd.to_numeric(df_payin["Сумма"], errors="coerce").fillna(0)
    df_payout["Сумма"] = pd.to_numeric(df_payout["Сумма"], errors="coerce").fillna(0)

    lines = []
    lines.append(f"Данные на {header_date.strftime('%d.%m')} с 00:00 по {end_dt.strftime('%H:%M')}")
    lines.append("")

    # ======================================================
    #                       PAYOUT
    # ======================================================
    lines.append("Выплаты:")

    for partner_key, partner_data in payout_cfg.items():
        title = partner_data.get("title", partner_key)
        lines.append(title + ":")

        partner_norm = normalize_partner_name(partner_key)
        df_p = df_payout[df_payout["norm"] == partner_norm]

        for method_code, mdata in partner_data.get("methods", {}).items():

            df_m = df_p[df_p["enum метод"].astype(str).str.upper() ==
                        method_code.upper()]

            amount = df_m["Сумма"].sum()

            m_title = mdata.get("title", method_code)
            limit = mdata.get("limit")
            vilka = mdata.get("vilka")

            lim = f" (Лимит {fmt_int(limit)})" if limit is not None else ""
            vk  = f" ВИЛКА {vilka}" if vilka else ""

            lines.append(f" - {m_title} - {fmt_int(amount)}{lim}{vk}")

        lines.append("")  # пустая строка между payout-группами

    lines.append("__________________")
    lines.append("")
    lines.append("Поступления:")

    # ======================================================
    #                     PAYIN: SINGLE
    # ======================================================

    # сначала считаем суммы по каждому партнёру
    payin_amounts = {}
    for partner_key in payin_cfg:
        norm = normalize_partner_name(partner_key)
        df_p = df_payin[df_payin["norm"] == norm]
        payin_amounts[partner_key] = df_p["Сумма"].sum()

    # выводим партнёров
    printed_groups = set()
    first = True

    for partner_key, pdata in payin_cfg.items():

        if not first:
            lines.append("")   # пустая строка между PARTNERS
        first = False

        title = pdata.get("title", partner_key)
        amount = payin_amounts.get(partner_key, 0)
        limit = pdata.get("limit")
        vilka = pdata.get("vilka")

        lim = f" (Лимит {fmt_int(limit)})" if limit is not None else ""
        vk  = f" ВИЛКА {vilka}" if vilka else ""

        # одиночная строка
        lines.append(f"{title} - {fmt_int(amount)}{lim}{vk}")

        # теперь суммарные группы
        for gkey, gdata in payin_groups.items():
            if gkey in printed_groups:
                continue

            combine_list = gdata.get("combine", [])
            if partner_key not in combine_list:
                continue

            total = sum(payin_amounts.get(x, 0) for x in combine_list)

            g_title = gdata.get("title", gkey)
            g_limit = gdata.get("limit")
            g_vilka = gdata.get("vilka")

            lim2 = f" (Лимит {fmt_int(g_limit)})" if g_limit is not None else ""
            vk2  = f" ВИЛКА {g_vilka}" if g_vilka else ""

            lines.append(
                f"{g_title} - {fmt_int(total)}{lim2}{vk2}"
            )

            printed_groups.add(gkey)

    # ======================================================
    # SEND
    # ======================================================
    txt = "\n".join(lines)
    send_message_sync(txt, chat_id=CHAT_ID)
    logger.info("[hourly_report] Отчёт отправлен")

    return txt
