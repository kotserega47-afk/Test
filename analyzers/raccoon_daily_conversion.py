import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from integrations.telegram_bot import send_message_sync
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["ANALYZER"]
logger = get_logger(name, icon)

MSK = ZoneInfo("Europe/Moscow")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_RACCOON_WALLET")

SUCCESS_STATUSES = {"оплачен"}
COUNTABLE_STATUSES = {"оплачен", "ошибка"}


def _norm(s):
    return str(s).strip().lower()


def _to_float(x):
    s = str(x).replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0


def _money(v):
    return f"{int(round(v)):,}".replace(",", ",")


def run_daily_conversion_report(payin_path: str):

    now = datetime.now(MSK)
    day = (now - timedelta(days=1)).date()

    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=MSK)
    end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=MSK)

    df = pd.read_excel(payin_path)

    df["Дата/Время создания"] = pd.to_datetime(df["Дата/Время создания"], dayfirst=True, errors="coerce")
    df["Дата/Время создания"] = df["Дата/Время создания"].dt.tz_localize(MSK)

    df = df[(df["Дата/Время создания"] >= start) & (df["Дата/Время создания"] <= end)]

    df["_status"] = df["Статус"].apply(_norm)
    df["_amount"] = df["Сумма"].apply(_to_float)

    df_countable = df[df["_status"].isin(COUNTABLE_STATUSES)]

    if df_countable.empty:
        logger.info("Daily conversion: empty day")
        return None

    # === По партнёрам ===
    g_partner = (
        df_countable.groupby("Партнер")
        .agg(
            total=("_status", "size"),
            success=("_status", lambda s: (s == "оплачен").sum()),
            amount=("_amount", lambda s: float(s[df_countable.loc[s.index, "_status"] == "оплачен"].sum())),
        )
        .reset_index()
    )

    g_partner["conv"] = (g_partner["success"] / g_partner["total"] * 100).round(1)

    # === По методам (в конце) ===
    g_method = (
        df_countable.groupby("Метод пополнения")
        .agg(
            total=("_status", "size"),
            success=("_status", lambda s: (s == "оплачен").sum()),
        )
        .reset_index()
    )

    g_method["conv"] = (g_method["success"] / g_method["total"] * 100).round(1)

    # === Формирование текста ===
    lines = []
    lines.append(f"📈 Суточная конверсия ({day.strftime('%d.%m.%Y')})")
    lines.append("")

    g_partner = g_partner.sort_values("amount", ascending=False)

    for _, r in g_partner.iterrows():
        lines.append(r["Партнер"])
        lines.append(f"  Всего операций: {int(r['total'])}")
        lines.append(f"  Успешных: {int(r['success'])}")
        lines.append(f"  Конверсия: {r['conv']:.1f}%")
        lines.append(f"  Поступления: {_money(r['amount'])}")
        lines.append("")

    lines.append("Итого по методам:")
    for _, r in g_method.iterrows():
        lines.append(f"  • {r['Метод пополнения']} — ({r['conv']:.1f}%)")

    text = "\n".join(lines)

    send_message_sync(text, chat_id=CHAT_ID)
    logger.info("Daily conversion sent")

    return text
