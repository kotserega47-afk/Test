# coding: utf-8
"""
Hourly Report — полностью переписанная версия.

Особенности:
- Работает через YAML: payin, payin_groups, payout
- Поддерживает локальный режим (--local)
- Поддерживает подмену времени (--fake-time)
- Безопасная отправка в Telegram
- Корректная логика времени:
      если час != 0 → с 00:00 по HH:00
      если час == 0 → за вчера, но шапка = сегодняшняя
- Неизвестные партнёры добавляются автоматически в конце отчёта
"""

import os
import argparse
from datetime import datetime, timedelta

import pandas as pd
import yaml
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from utils.logger import logger
from integrations.telegram_bot import send_message_sync
from load_data import normalize_partner_name


# -------------------------------------------------------
# ИНИЦИАЛИЗАЦИЯ
# -------------------------------------------------------
load_dotenv()

MSK = ZoneInfo("Europe/Moscow")
print("MSK CHECK:", datetime.now(MSK), "OFFSET:", datetime.now(MSK).utcoffset())
BASE_DIR = "/tmp/hourly"
CONFIG_PATH = os.path.join("config", "hourly_report.yaml")

CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID_HOURLY")
    or os.getenv("TELEGRAM_CHAT_ID")
)


# -------------------------------------------------------
# LOAD CONFIG
# -------------------------------------------------------
def load_cfg():
    if not os.path.exists(CONFIG_PATH):
        logger.error(f"[hourly_report] Конфиг не найден: {CONFIG_PATH}")
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# -------------------------------------------------------
# TIME LOGIC
# -------------------------------------------------------
def get_time_bounds(fake_time: str | None):
    """
    Возвращает (start_dt, end_dt, header_date).

    Логика (скорректированная):
    - если запуск ровно в 00:00 → отчёт ЗА ВЧЕРА (00:00–23:59) и шапка = ВЧЕРА
    - иначе → за сегодня (00:00–текущий час), шапка = сегодня
    """
    if fake_time:
        now = datetime.strptime(fake_time, "%Y-%m-%d %H:%M")
        now = now.replace(tzinfo=MSK)
    else:
        now = datetime.now(MSK)

    today = now.date()

    if now.hour == 0:
        # Отчёт за вчера
        op_day = today - timedelta(days=1)
        start_dt = datetime(op_day.year, op_day.month, op_day.day, 0, 0, tzinfo=MSK)
        end_dt   = datetime(op_day.year, op_day.month, op_day.day, 23, 59, tzinfo=MSK)
        header_date = op_day  # ← ВАЖНО: теперь шапка показывает вчера
    else:
        op_day = today
        start_dt = datetime(op_day.year, op_day.month, op_day.day, 0, 0, tzinfo=MSK)
        end_dt   = datetime(op_day.year, op_day.month, op_day.day, now.hour, 0, tzinfo=MSK)
        header_date = op_day
    print(f"ОКНО: start={start_dt}, end={end_dt}")
    return start_dt, end_dt, header_date


# -------------------------------------------------------
# LOAD FILES
# -------------------------------------------------------
def load_excel_local():
    payin_path = os.path.join(BASE_DIR, "payin.xlsx")
    payout_path = os.path.join(BASE_DIR, "payout.xlsx")

    if not os.path.exists(payin_path):
        logger.error(f"Файл не найден: {payin_path}")
        raise FileNotFoundError(payin_path)

    if not os.path.exists(payout_path):
        logger.error(f"Файл не найден: {payout_path}")
        raise FileNotFoundError(payout_path)

    df_payin = pd.read_excel(payin_path, dtype=str)
    df_payout = pd.read_excel(payout_path, dtype=str)

    return df_payin, df_payout


# -------------------------------------------------------
# FORMAT HELPERS
# -------------------------------------------------------
def fmt_int(v):
    """ 1845342 → '1 845 342' """
    try:
        v = int(round(float(v)))
        return f"{v:,}".replace(",", " ")
    except:
        return str(v)


# -------------------------------------------------------
# FILTER BY DATE
# -------------------------------------------------------
def filter_by_dt(df, col, start_dt, end_dt):
    df = df.copy()

    # Преобразуем в datetime
    df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    # Если дата уже с tz (aware) → приводим к MSK
    # Если без tz (naive) → локализуем как MSK
    def fix_tz(x):
        if pd.isna(x):
            return x
        if x.tzinfo is None:
            # дата без tz → считаем, что она в московской зоне
            return x.replace(tzinfo=MSK)
        else:
            # aware datetime → переводим в MSK
            return x.astimezone(MSK)

    df[col] = df[col].apply(fix_tz)

    # Теперь нормальная фильтрация
    return df[(df[col] >= start_dt) & (df[col] <= end_dt)]


# -------------------------------------------------------
# MAIN REPORT
# -------------------------------------------------------
def run_hourly_report(local_mode=False, fake_time=None, print_mode=False, debug=False):

    # ---------- LOAD CONFIG ----------
    cfg = load_cfg()

    payout_cfg = cfg.get("payout", {})
    payin_cfg = cfg.get("payin", {})
    payin_groups_cfg = cfg.get("payin_groups", {})

    # ---------- TIME ----------
    start_dt, end_dt, header_date = get_time_bounds(fake_time)

    if debug:
        logger.info(f"[TIME] start={start_dt}, end={end_dt}, header_date={header_date}")

    # ---------- LOAD FILES ----------
    df_payin, df_payout = load_excel_local()

    # ---------- NORMALIZE ----------
    df_payin["partner_norm"] = df_payin["Партнер"].astype(str).apply(normalize_partner_name)
    df_payout["partner_norm"] = df_payout["Партнер"].astype(str).apply(normalize_partner_name)

    # ---------- FILTER BY DATE ----------
    df_payin = filter_by_dt(df_payin, "Дата/Время создания", start_dt, end_dt)
    df_payout = filter_by_dt(df_payout, "Дата/Время создания", start_dt, end_dt)

    # ---------- FILTER BY STATUS ----------
    df_payin = df_payin[df_payin["Статус"].str.lower() == "оплачен"]
    df_payout = df_payout[df_payout["Статус"].str.lower() == "оплачен"]

    # ---------- AMOUNTS ----------
    df_payin["Сумма"] = pd.to_numeric(df_payin["Сумма"], errors="coerce").fillna(0)
    df_payout["Сумма"] = pd.to_numeric(df_payout["Сумма"], errors="coerce").fillna(0)

    # -------------------------------------------------------
    # RENDER REPORT
    # -------------------------------------------------------
    lines = []

    header = f"Данные на {header_date.strftime('%d.%m')} с 00:00 по {end_dt.strftime('%H:%M')}"
    lines.append(header)
    lines.append("")

    # ===================== PAYOUT =====================
    lines.append("Выплаты:")

    for partner_key, methods in payout_cfg.items():
        partner_title = partner_key
        partner_norm = normalize_partner_name(partner_key)

        lines.append(f"{partner_title}:")

        df_p = df_payout[df_payout["partner_norm"] == partner_norm]

        # Правило: у Abhsber пустой enum метод = UNI
        if partner_norm == normalize_partner_name("HH (Abhsber IN + Выплаты) Тинь (117)"):
            df_p["enum метод"] = (
                df_p["enum метод"]
                .astype(str)
                .str.strip()
                .replace("", "UNI")
                .replace("nan", "UNI")
            )

        for method_code, mdata in methods.items():
            title = mdata.get("title", method_code)
            limit = mdata.get("limit")
            vilka = mdata.get("vilka")

            df_m = df_p[df_p["enum метод"].astype(str).str.upper() == method_code.upper()]
            amount = df_m["Сумма"].sum()

            lim_txt = f" (Лимит {fmt_int(limit)})" if limit is not None else ""
            vilka_txt = f" ВИЛКА {vilka}" if vilka else ""

            lines.append(f" - {title} - {fmt_int(amount)}{lim_txt}{vilka_txt}")

    lines.append("__________________")
    lines.append("Поступления:")

    # ===================== PAYIN: SINGLE =====================
    for partner_key, cfg_item in payin_cfg.items():
        partner_norm = normalize_partner_name(partner_key)
        df_p = df_payin[df_payin["partner_norm"] == partner_norm]

        amount = df_p["Сумма"].sum()

        title = cfg_item.get("title", partner_key)
        limit = cfg_item.get("limit")
        vilka = cfg_item.get("vilka")

        lim_txt = f" (Лимит {fmt_int(limit)})" if limit is not None else ""
        vilka_txt = f" ВИЛКА {vilka}" if vilka else ""

        lines.append(f"{title} - {fmt_int(amount)}{lim_txt}{vilka_txt}")

    # ===================== PAYIN: GROUPS =====================
    for gcode, gdata in payin_groups_cfg.items():
        title = gdata.get("title", gcode)
        limit = gdata.get("limit")
        vilka = gdata.get("vilka")

        total = 0
        for sub in gdata.get("combine", []):
            sub_norm = normalize_partner_name(sub)
            df_sub = df_payin[df_payin["partner_norm"] == sub_norm]
            total += df_sub["Сумма"].sum()

        lim_txt = f" (Лимит {fmt_int(limit)})" if limit is not None else ""
        vilka_txt = f" ВИЛКА {vilka}" if vilka else ""

        lines.append(f"{title} - {fmt_int(total)}{lim_txt}{vilka_txt}")

    # ===================== PAYIN: UNKNOWN =====================
    known_norms = set(normalize_partner_name(k) for k in payin_cfg.keys())
    for gdata in payin_groups_cfg.values():
        for x in gdata.get("combine", []):
            known_norms.add(normalize_partner_name(x))

    all_norms = set(df_payin["partner_norm"].unique())
    unknown_norms = sorted(all_norms - known_norms)

    if unknown_norms:
        lines.append("")
        lines.append("Прочие партнёры:")

        for unk in unknown_norms:
            df_sub = df_payin[df_payin["partner_norm"] == unk]
            amount = df_sub["Сумма"].sum()
            orig = df_sub["Партнер"].iloc[0]
            lines.append(f"{orig} - {fmt_int(amount)}")

    # -------------------------------------------------------
    # SEND AND PRINT
    # -------------------------------------------------------
    report_text = "\n".join(lines)

    if print_mode:
        print("\n======= REPORT =======\n")
        print(report_text)
        print("\n======================\n")

    send_message_sync(report_text, chat_id=CHAT_ID)
    logger.info("[hourly_report] Отчёт отправлен")

    return report_text


# -------------------------------------------------------
# MAIN ENTRY POINT
# -------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="Use local files from /tmp/hourly")
    parser.add_argument("--fake-time", type=str, help="Override time: 'YYYY-MM-DD HH:MM'")
    parser.add_argument("--print", action="store_true", help="Print report to console")
    parser.add_argument("--debug", action="store_true", help="Debug output")

    args = parser.parse_args()

    run_hourly_report(
        local_mode=True,
        fake_time=args.fake_time,
        print_mode=args.print,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
