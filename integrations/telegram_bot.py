# integrations/telegram_bot.py
import asyncio
from telegram import Bot, InputFile
from telegram.request import HTTPXRequest
from utils.logger import logger
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")

# Настраиваем HTTP-клиент с увеличенным пулом
request = HTTPXRequest(
    connection_pool_size=20,   # увеличить пул
    connect_timeout=10.0,
    read_timeout=30.0,
)

bot = Bot(token=TELEGRAM_TOKEN, request=request)

# Один глобальный event loop
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)


def send_message_sync(content: str):
    """Отправка текста в Telegram (синхронно)"""
    try:
        loop.run_until_complete(bot.send_message(chat_id=CHAT_ID, text=content))
        logger.info("Сообщение отправлено в Telegram")
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение в Telegram: {e}")


def send_file_sync(file_path: str, caption: str = None):
    """Отправка файла в Telegram (синхронно)"""
    try:
        with open(file_path, "rb") as f:
            loop.run_until_complete(
                bot.send_document(chat_id=CHAT_ID, document=InputFile(f), caption=caption)
            )
        logger.info(f"Файл {file_path} отправлен в Telegram")
    except Exception as e:
        logger.error(f"Не удалось отправить файл в Telegram: {e}")
