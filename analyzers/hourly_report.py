# coding: utf-8
"""
Hourly Report — рефакторинг с нормализацией методов и разделением логики.
"""

import os
from datetime import datetime, timedelta
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from integrations.telegram_bot import send_message_sync
from core.config_manager import get_wallet_limits_df
from utils.normalization import normalize_partner_name, parse_dt_series_msk
icon, name = LOG_PROFILES["HOURLY"]
logger = get_logger(name, icon)

# ---------------------------------------
# Константы
# ---------------------------------------
MSK = ZoneInfo("Europe/Moscow")
BASE_DIR = "/tmp/hourly"
CONFIG_PATH = "config/hourly_report.yaml"

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_HOURLY")
if not CHAT_ID:
    raise RuntimeError("Не задан TELEGRAM_CHAT_ID_HOURLY")


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


def filter_dt(df, col, start_dt, end_dt):
    """
    Фильтрация по интервалу (канон: tz-aware MSK).
    """
    df = df.copy()

    s = parse_dt_series_msk(df[col])

    mask = s.notna() & (s >= start_dt) & (s <= end_dt)

    df[col] = s
    return df[mask]

def _build_hourly_comments():
    df = get_wallet_limits_df(rules_xlsx_path=os.getenv("RULES_XLSX_PATH", "").strip())

    # только активные правила, где analyzer содержит wallet
    df = df[df["enabled"] == 1].copy()
    df = df[df["_analyzers_list"].apply(lambda xs: "wallet" in xs)]

    # нормализуем method
    df["method"] = df["method"].astype(str).str.strip().str.upper().replace({"*": ""})
    df["comment"] = df["comment"].astype(str).fillna("").str.strip()

    # partner comments
    partner = df[df["scope"] == "partner"].copy()
    partner["scope_norm"] = partner["scope_value"].apply(normalize_partner_name)

    payin_comment = {}
    payout_comment = {}

    # payin: method пустой
    for _, r in partner.iterrows():
        if not r["comment"]:
            continue
        m = r["method"] or ""
        key = (r["scope_norm"], m)
        # если это method-specific -> кладём в payout_comment
        if m:
            payout_comment[key] = r["comment"]
        else:
            payin_comment[r["scope_norm"]] = r["comment"]
            payout_comment[(r["scope_norm"], "")] = r["comment"]

    # group comments (ключ = scope_value, как код aurora/abhsber)
    group = df[df["scope"] == "group"].copy()
    group_comment = {}
    for _, r in group.iterrows():
        if r["comment"]:
            group_comment[str(r["scope_value"]).strip().lower()] = r["comment"]

    return payin_comment, payout_comment, group_comment

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
        end = datetime(day.year, day.month, day.day, 23, 59, tzinfo=MSK)
        header_date = day
    else:
        day = today
        start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=MSK)
        end = datetime(day.year, day.month, day.day, now.hour, 0, tzinfo=MSK)
        header_date = day

    return start, end, header_date


# ======================================================
#                 ПОДГОТОВКА ДАННЫХ (NEW)
# ======================================================

def prepare_data(start_dt, end_dt):  # NEW
    """Загрузка файлов + нормализация + фильтрация."""
    df_payin, df_payout = load_hourly_files()

    # нормализация партнёров
    df_payin["norm"] = df_payin["Партнер"].astype(str).apply(normalize_partner_name)
    df_payout["norm"] = df_payout["Партнер"].astype(str).apply(normalize_partner_name)

    # нормализация метода
    df_payin["method_norm"] = None
    df_payout["method_norm"] = df_payout["enum метод"].apply(normalize_method)

    # фильтрация по дате
    df_payin = filter_dt(df_payin, "Дата/Время создания", start_dt, end_dt)
    df_payout = filter_dt(df_payout, "Дата/Время создания", start_dt, end_dt)

    # только оплачено
    df_payin = df_payin[df_payin["Статус"].str.lower() == "оплачен"]
    df_payout = df_payout[df_payout["Статус"].str.lower() == "оплачен"]

    # суммы
    df_payin["Сумма"] = pd.to_numeric(df_payin["Сумма"], errors="coerce").fillna(0)
    df_payout["Сумма"] = pd.to_numeric(df_payout["Сумма"], errors="coerce").fillna(0)

    return df_payin, df_payout


# ======================================================
#                 АГРЕГАЦИЯ PAYOUT (NEW)
# ======================================================

def aggregate_payout(df_payout, payout_cfg, payout_comment: dict[tuple[str, str], str]):
    result = []

    for partner_key, partner_data in payout_cfg.items():
        partner_norm = normalize_partner_name(partner_key)
        df_p = df_payout[df_payout["norm"] == partner_norm]

        methods_result = []
        for method_code, mdata in partner_data.get("methods", {}).items():
            code = str(method_code).strip().upper()
            amount = df_p[df_p["method_norm"] == code]["Сумма"].sum()

            # comment from rules (method-specific -> fallback wildcard)
            comment = (
                payout_comment.get((partner_norm, code))
                or payout_comment.get((partner_norm, ""))
                or ""
            )

            methods_result.append({
                "title": mdata.get("title", method_code),
                "amount": amount,
                "comment": comment,
            })

        result.append({
            "title": partner_data.get("title", partner_key),
            "methods": methods_result,
        })

    return result


# ======================================================
#                 АГРЕГАЦИЯ PAYIN (NEW)
# ======================================================

def aggregate_payin(
    df_payin,
    payin_cfg,
    payin_groups,
    payin_comment: dict[str, str],
    group_comment: dict[str, str],
):
    result = []

    # --- считаем суммы по партнёрам ---
    payin_amounts = {}
    for partner_key in payin_cfg:
        norm = normalize_partner_name(partner_key)
        df_p = df_payin[df_payin["norm"] == norm]
        payin_amounts[partner_key] = df_p["Сумма"].sum()

    printed_groups = set()

    # --- партнёры ---
    for partner_key, pdata in payin_cfg.items():
        norm = normalize_partner_name(partner_key)

        comment = payin_comment.get(norm, "")

        result.append({
            "title": pdata.get("title", partner_key),
            "amount": payin_amounts.get(partner_key, 0),
            "comment": comment,
            "is_group": False,
        })

        # --- группы ---
        for gkey, gdata in payin_groups.items():
            if gkey in printed_groups:
                continue

            if partner_key not in gdata.get("combine", []):
                continue

            total = sum(payin_amounts.get(x, 0) for x in gdata["combine"])

            gkey_norm = str(gkey).strip().lower()
            g_comment = group_comment.get(gkey_norm, "")

            result.append({
                "title": gdata.get("title", gkey),
                "amount": total,
                "comment": g_comment,
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
                    comment = f"; {m['comment']}" if m.get("comment") else ""
                    lines.append(f" - {m['title']} – {fmt_int(m['amount'])}{comment}")
            else:
                comment = f"; {item['comment']}" if item.get("comment") else ""
                lines.append(f"{counter}) {item['title']} – {fmt_int(item['amount'])}{comment}")

            counter += 1

        for _ in range(spacing):
            lines.append("")

def format_report(payout_data, payin_data, header_date, end_dt):
    cfg = load_cfg()

    lines = []
    lines.append(f"Данные на {header_date.strftime('%d.%m')} с 00:00 по {end_dt.strftime('%H:%M')}")
    lines.append("")

    # PAYOUT
    payout_layout = cfg.get("payout_layout", [])
    format_section_with_layout(lines, "Выплаты:", payout_data, payout_layout)

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

    df_payin, df_payout = prepare_data(start_dt, end_dt)

    payin_comment, payout_comment, group_comment = _build_hourly_comments()

    payout_data = aggregate_payout(df_payout, cfg.get("payout", {}), payout_comment)
    payin_data = aggregate_payin(
        df_payin,
        cfg.get("payin", {}),
        cfg.get("payin_groups", {}),
        payin_comment,
        group_comment,
    )

    txt = format_report(payout_data, payin_data, header_date, end_dt)

    send_message_sync(txt, chat_id=CHAT_ID)
    logger.info("[hourly_report] Отчёт отправлен")
    return txt

def run_hourly_report_for_interval(start_dt, end_dt, send=False, chat_id=CHAT_ID, cfg_path=CONFIG_PATH):
    from integrations.telegram_bot import send_message_direct

    cfg = load_cfg()

    df_payin, df_payout = prepare_data(start_dt, end_dt)

    payin_comment, payout_comment, group_comment = _build_hourly_comments()

    payout_data = aggregate_payout(df_payout, cfg.get("payout", {}), payout_comment)
    payin_data = aggregate_payin(
        df_payin,
        cfg.get("payin", {}),
        cfg.get("payin_groups", {}),
        payin_comment,
        group_comment,
    )

    txt = format_report(payout_data, payin_data, start_dt.date(), end_dt)

    if send:
        send_message_direct(txt, chat_id=chat_id)

    return txt