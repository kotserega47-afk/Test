# integrations/telegram_bot.py
import os
import asyncio
from telegram import Bot, InputFile
from telegram.request import HTTPXRequest
from utils.logger import logger
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEFAULT_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not DEFAULT_CHAT_ID:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")


def _new_bot():
    """
    Создаёт новый Bot и новый HTTP-клиент на каждый вызов.
    Это полностью устраняет проблемы event loop в многопоточности.
    """
    request = HTTPXRequest(
        connection_pool_size=20,
        connect_timeout=10.0,
        read_timeout=30.0,
    )
    return Bot(token=TELEGRAM_TOKEN, request=request)


def send_message_sync(content: str, chat_id: str | None = None):
    """
    Синхронная отправка сообщения в Telegram.
    Полностью потокобезопасно, использует asyncio.run.
    """
    try:
        chat_id = chat_id or DEFAULT_CHAT_ID
        if not chat_id:
            logger.warning("⚠️ CHAT_ID не указан — сообщение не отправлено.")
            return

        bot = _new_bot()  # ← создаём новый bot
        asyncio.run(bot.send_message(chat_id=chat_id, text=content))

        logger.info(f"✅ Сообщение отправлено (chat_id={chat_id}): {content[:80]}")

    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения (chat_id={chat_id}): {e}")


def send_file_sync(file_path: str, caption: str = None, chat_id: str | None = None):
    """
    Потокобезопасная отправка файла.
    """
    try:
        chat_id = chat_id or DEFAULT_CHAT_ID
        if not chat_id:
            logger.warning("⚠️ CHAT_ID не указан — файл не отправлен.")
            return

        bot = _new_bot()  # ← новый bot для новой отправки

        with open(file_path, "rb") as f:
            asyncio.run(
                bot.send_document(
                    chat_id=chat_id,
                    document=InputFile(f),
                    caption=caption
                )
            )

        logger.info(f"📁 Файл отправлен: {file_path} (chat_id={chat_id})")

    except Exception as e:
        logger.error(f"❌ Не удалось отправить файл в Telegram: {e}")
