# integrations/telegram_bot.py
import asyncio
from telegram import Bot, InputFile
from utils.logger import logger
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")

bot = Bot(token=TELEGRAM_TOKEN)


# -------------------------------
# Синхронные обёртки для отправки сообщений
# -------------------------------
def send_message_sync(content: str):
    """Отправка текста в Telegram (синхронно)"""
    try:
        asyncio.run(bot.send_message(chat_id=CHAT_ID, text=content))
        logger.info("Сообщение отправлено в Telegram")
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение в Telegram: {e}")


def send_file_sync(file_path: str, caption: str = None):
    """Отправка файла в Telegram (синхронно)"""
    try:
        with open(file_path, "rb") as f:
            asyncio.run(bot.send_document(chat_id=CHAT_ID, document=InputFile(f), caption=caption))
        logger.info(f"Файл {file_path} отправлен в Telegram")
    except Exception as e:
        logger.error(f"Не удалось отправить файл в Telegram: {e}")
