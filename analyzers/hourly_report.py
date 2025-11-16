import os
import yaml
import pandas as pd
from datetime import datetime, time
from utils.logger import logger
from integrations.telegram_bot import send_message_sync

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_HOURLY")

BASE_DIR = "/tmp/hourly"
CONFIG_PATH = os.path.join("config", "hourly_report.yaml")


def load_cfg():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def filter_today(df, dt_col):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    hour_cut = datetime.now().replace(minute=0, second=0, microsecond=0)
    df["_dt"] = pd.to_datetime(df[dt_col], dayfirst=True, errors="coerce")
    return df[(df["_dt"] >= today) & (df["_dt"] < hour_cut)]


def run_hourly_report():
    cfg = load_cfg()

    payin_path = os.path.join(BASE_DIR, "payin.xlsx")
    payout_path = os.path.join(BASE_DIR, "payout.xlsx")

    if not os.path.exists(payin_path) or not os.path.exists(payout_path):
        logger.warning("[hourly_report] Нет файлов для отчёта")
        return

    df_payin = pd.read_excel(payin_path, dtype=str)
    df_payout = pd.read_excel(payout_path, dtype=str)

    df_payin = filter_today(df_payin, "Дата/Время создания")
    df_payout = filter_today(df_payout, "Дата/Время создания")

    df_payin["Сумма"] = pd.to_numeric(df_payin["Сумма"], errors="coerce").fillna(0)
    df_payout["Сумма"] = pd.to_numeric(df_payout["Сумма"], errors="coerce").fillna(0)

    df_payin = df_payin[df_payin["Статус"].str.lower() == "оплачен"]
    df_payout = df_payout[df_payout["Статус"].str.lower() == "оплачен"]

    # ---------- PAYOUT ----------
    payout_cfg = cfg.get("payout", {})
    payout_lines = []

    for partner, dirs in payout_cfg.items():
        payout_lines.append(f"{partner}:")
        df_sub = df_payout[df_payout["Партнер"].str.contains(partner, case=False, na=False)]

        for method, data in dirs.items():
            df_m = df_sub[df_sub["enum метод"].str.upper() == method]
            amount = df_m["Сумма"].sum()
            payout_lines.append(
                f" - {method} - {amount:,.2f} (Лимит {data['limit']:,}) ВИЛКА {data['vilka']}"
            )

        payout_lines.append("")

    # ---------- PAYIN ----------
    payin_cfg = cfg.get("payin", {})
    payin_lines = []

    for partner, groups in payin_cfg.items():
        payin_lines.append(f"{partner}:")

        for gname, gdata in groups.items():

            if "match" in gdata:
                df_m = df_payin.copy()
                for m in gdata["match"]:
                    df_m = df_m[df_m["Партнер"].str.contains(m, case=False, na=False) |
                                df_m["Описание"].str.contains(m, case=False, na=False, regex=False)
                                if "Описание" in df_m.columns else False]
                amount = df_m["Сумма"].sum()
                payin_lines.append(f" - {gname} - {amount:,.2f}")

            if "combine" in gdata:
                total = 0
                for sub in gdata["combine"]:
                    df_m = df_payin.copy()
                    for m in groups[sub]["match"]:
                        df_m = df_m[df_m["Партнер"].str.contains(m, case=False, na=False)]
                    total += df_m["Сумма"].sum()
                payin_lines.append(f" - {gname} - {total:,.2f}")

        payin_lines.append("")

    # ---------- FINAL OUTPUT ----------
    now = datetime.now()
    header = f"Данные на {now.strftime('%d.%m')} с 00:00 по {now.strftime('%H:00')}"

    text = (
        header + "\n\n"
        "Выплаты:\n" + "\n".join(payout_lines) +
        "\nПоступления:\n" + "\n".join(payin_lines)
    )

    send_message_sync(text, chat_id=CHAT_ID)
    logger.info("[hourly_report] Отчёт отправлен")
