# integrations/telegram_bot.py
import asyncio
from telegram import Bot, InputFile
from telegram.request import HTTPXRequest
from utils.logger import logger
import os
from asyncio import get_event_loop, new_event_loop, set_event_loop

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


def send_message_sync(content: str, chat_id: str | None = None):
    """
    Отправка текста в Telegram (синхронно).

    Аргументы:
        content : str  — текст сообщения
        chat_id : str | None  — ID чата; если не указан, берётся из TELEGRAM_CHAT_ID в .env
    """
    try:
        # fallback на chat_id из окружения
        if chat_id is None:
            chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not chat_id:
            logger.warning("⚠️ CHAT_ID не указан — сообщение не отправлено.")
            return

        # Получаем или создаём event loop (на Railway иногда нет активного)
        try:
            loop = get_event_loop()
        except RuntimeError:
            loop = new_event_loop()
            set_event_loop(loop)

        # Отправляем сообщение
        loop.run_until_complete(bot.send_message(chat_id=chat_id, text=content))
        logger.info(f"✅ Сообщение отправлено в Telegram (chat_id={chat_id}): {content[:80]}...")

    except Exception as e:
        logger.error(f"❌ Не удалось отправить сообщение в Telegram (chat_id={chat_id}): {e}")

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
