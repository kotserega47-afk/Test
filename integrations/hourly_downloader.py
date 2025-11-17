import os
import yaml
import pandas as pd
from datetime import datetime
from pytz import timezone

from dotenv import load_dotenv
from utils.logger import logger
from integrations.telegram_bot import send_message_sync
from load_data import normalize_partner_name


# ============================================================
#  INIT
# ============================================================

load_dotenv()

MSK = timezone("Europe/Moscow")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_HOURLY")

BASE_DIR = "/tmp/hourly"
CONFIG_PATH = "config/hourly_report.yaml"


# ============================================================
#  CONFIG LOADER
# ============================================================

def load_cfg():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ============================================================
#  DATE FILTER
# ============================================================

def filter_today(df, date_col):
    """Фильтруем по сегодняшнему дню с 00:00 до текущего часа."""
    now = datetime.now(MSK)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_cut = now.replace(minute=0, second=0, microsecond=0)

    dt = pd.to_datetime(df[date_col], dayfirst=True, errors="coerce")
    dt = dt.dt.tz_localize(MSK, nonexistent="shift_forward")

    return df[(dt >= day_start) & (dt < hour_cut)]


# ============================================================
#  MAIN REPORT LOGIC
# ============================================================

def run_hourly_report():
    cfg = load_cfg()

    # -------------------------------
    # Load data
    # -------------------------------
    payin_path = f"{BASE_DIR}/payin.xlsx"
    payout_path = f"{BASE_DIR}/payout.xlsx"

    logger.info(f"[DEBUG] Loading hourly XLSX files:")
    logger.info(f"[DEBUG]  PayIn:  {payin_path}")
    logger.info(f"[DEBUG]  PayOut: {payout_path}")

    df_payin = pd.read_excel(payin_path, dtype=str)
    df_payout = pd.read_excel(payout_path, dtype=str)

    logger.info(f"[DEBUG] RAW PayIn rows:  {len(df_payin)}")
    logger.info(f"[DEBUG] RAW PayOut rows: {len(df_payout)}")

    # -------------------------------
    # Normalize partner for comparison
    # -------------------------------
    df_payin["partner_norm"] = df_payin["Партнер"].astype(str).apply(normalize_partner_name)
    df_payout["partner_norm"] = df_payout["Партнер"].astype(str).apply(normalize_partner_name)

    # -------------------------------
    # Filter by today's date
    # -------------------------------
    df_payin = filter_today(df_payin, "Дата/Время создания")
    df_payout = filter_today(df_payout, "Дата/Время создания")

    # -------------------------------
    # Convert amount
    # -------------------------------
    df_payin["Сумма"] = pd.to_numeric(df_payin["Сумма"], errors="coerce").fillna(0)
    df_payout["Сумма"] = pd.to_numeric(df_payout["Сумма"], errors="coerce").fillna(0)

    # -------------------------------
    # Keep only "Оплачен"
    # -------------------------------
    df_payin = df_payin[df_payin["Статус"].str.lower() == "оплачен"]
    df_payout = df_payout[df_payout["Статус"].str.lower() == "оплачен"]

    logger.info(f"[DEBUG] PayIn 'Оплачен':  {len(df_payin)}")
    logger.info(f"[DEBUG] PayOut 'Оплачен': {len(df_payout)}")

    # ============================================================
    #  PAYOUT (ВЫПЛАТЫ)
    # ============================================================

    payout_lines = []
    payout_cfg = cfg.get("payout")

    for partner_name, methods in payout_cfg.items():
        payout_lines.append(f"{partner_name}:")
        p_norm = normalize_partner_name(partner_name)

        # Фильтруем строки PayOut по партнеру
        df_partner = df_payout[df_payout["partner_norm"] == p_norm]

        for method, mdata in methods.items():

            # Фильтр по enum методу
            df_method = df_partner[
                df_partner["enum метод"].astype(str).str.upper() == method.upper()
            ]

            amount = df_method["Сумма"].sum()

            payout_lines.append(
                f" - {mdata['title']} - {amount:,.2f} "
                f"(Лимит {mdata['limit']:,}) ВИЛКА {mdata['vilka']}"
            )

        payout_lines.append("")  # пустая строка

    # ============================================================
    #  PAYIN (ПОСТУПЛЕНИЯ)
    # ============================================================

    payin_lines = []
    payin_cfg = cfg.get("payin")

    for block_name, groups in payin_cfg.items():

        payin_lines.append(f"{block_name}:")

        for gcode, gdata in groups.items():

            # ---------------------- обычная группа ----------------------
            if "partners" in gdata:

                partner_norms = [
                    normalize_partner_name(p) for p in gdata["partners"]
                ]

                df_m = df_payin[
                    df_payin["partner_norm"].isin(partner_norms)
                ]

                amount = df_m["Сумма"].sum()

                limit = gdata.get("limit")
                vilka = gdata.get("vilka")

                suffix = ""
                if limit is not None:
                    suffix += f" (Лимит {limit:,})"
                if vilka:
                    suffix += f" ВИЛКА {vilka}"

                payin_lines.append(f" - {gdata['title']} - {amount:,.2f}{suffix}")

            # ---------------------- комбинированная группа ----------------------
            elif "combine" in gdata:

                total = 0
                for subcode in gdata["combine"]:
                    sub = groups[subcode]
                    partner_norms = [
                        normalize_partner_name(p) for p in sub["partners"]
                    ]
                    df_sub = df_payin[
                        df_payin["partner_norm"].isin(partner_norms)
                    ]
                    total += df_sub["Сумма"].sum()

                limit = gdata.get("limit")
                vilka = gdata.get("vilka")

                suffix = ""
                if limit is not None:
                    suffix += f" (Лимит {limit:,})"
                if vilka:
                    suffix += f" ВИЛКА {vilka}"

                payin_lines.append(f" - {gdata['title']} - {total:,.2f}{suffix}")

        payin_lines.append("")

    # ============================================================
    #  BUILD FINAL MESSAGE
    # ============================================================

    now = datetime.now(MSK)
    header = f"Данные на {now.strftime('%d.%m')} с 00:00 по {now.strftime('%H:00')}"

    text = (
        header + "\n\n"
        "Выплаты:\n" + "\n".join(payout_lines) +
        "\nПоступления:\n" + "\n".join(payin_lines)
    )

    # ============================================================
    #  SEND
    # ============================================================

    direct = os.getenv("HOURLY_DIRECT_SEND", "0").lower() == "1"

    if direct:
        import asyncio
        from integrations.telegram_bot import bot
        print(">>> DIRECT SEND ENABLED (LOCAL)")
        asyncio.run(bot.send_message(chat_id=CHAT_ID, text=text))
    else:
        send_message_sync(text, chat_id=CHAT_ID)

    logger.info("[hourly_report] Report sent")


# ============================================================
#  LOCAL RUN
# ============================================================

if __name__ == "__main__":
    print(">>> LOCAL RUN hourly_report.py")
    run_hourly_report()
