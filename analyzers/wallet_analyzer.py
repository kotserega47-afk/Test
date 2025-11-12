# analyzers/wallet_analyzer.py
import os
import pandas as pd
from integrations.telegram_bot import send_message_sync
from utils.logger import logger


CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_WALLET")


def analyze_wallets(wallet_path: str, payin_path: str):
    """Простейший анализ выгрузок"""
    try:
        wallet = pd.read_excel(wallet_path)
        payin = pd.read_excel(payin_path)
    except Exception as e:
        send_message_sync(f"⚠️ Не удалось прочитать файлы: {e}")
        return

    total_balance = (
        pd.to_numeric(wallet.get("Баланс", pd.Series()), errors="coerce").sum()
        if "Баланс" in wallet
        else 0
    )
    payin_count = len(payin)
    payin_sum = (
        pd.to_numeric(payin.get("Сумма", pd.Series()), errors="coerce").sum()
        if "Сумма" in payin
        else 0
    )

    msg = (
        f"📅 Анализ кошельков за последние сутки\n"
        f"💰 Общий баланс: {total_balance:,.2f}\n"
        f"📥 Поступлений: {payin_count} (на сумму {payin_sum:,.2f})"
    )

    logger.info(msg)
    send_message_sync(msg, chat_id=CHAT_ID)
