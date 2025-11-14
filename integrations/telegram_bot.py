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


# =====================================================
#  Создаём ОДИН bot и ОДИН HTTP-клиент на весь модуль
# =====================================================
request = HTTPXRequest(
    connection_pool_size=20,
    connect_timeout=10.0,
    read_timeout=30.0,
)

bot = Bot(token=TELEGRAM_TOKEN, request=request)


# =====================================================
#  ВНУТРЕННИЕ async-функции
# =====================================================
async def _send_message(text: str, chat_id: str):
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error(f"❌ Ошибка async отправки сообщения: {e}")


async def _send_file(path: str, caption: str, chat_id: str):
    try:
        with open(path, "rb") as f:
            await bot.send_document(chat_id=chat_id, document=InputFile(f), caption=caption)
    except Exception as e:
        logger.error(f"❌ Ошибка async отправки файла: {e}")


# =====================================================
#  Универсальная функция безопасного вызова async
# =====================================================
def _run_async(coro):
    """
    Выполняет корутину в зависимости от состояния event loop.
    Это гарантирует:
    - отсутствие ошибок asyncio.run внутри работающего loop
    - отсутствие блокировок
    - минимальное потребление ресурсов
    """
    try:
        loop = asyncio.get_event_loop()

        if loop.is_running():
            # Уже есть event loop (Playwright, Scheduler)
            asyncio.ensure_future(coro)
        else:
            # Нет активного event loop — запускаем сами
            loop.run_until_complete(coro)

    except RuntimeError:
        # Если нет event loop вообще
        asyncio.run(coro)


# =====================================================
#  ПУБЛИЧНЫЕ функции отправки
# =====================================================

def send_message_sync(content: str, chat_id: str | None = None):
    chat_id = chat_id or DEFAULT_CHAT_ID
    if not chat_id:
        logger.warning("⚠️ CHAT_ID не указан — сообщение не отправлено.")
        return

    _run_async(_send_message(content, chat_id))

    logger.info(f"📨 Сообщение отправлено (chat_id={chat_id}): {content[:80]}")


def send_file_sync(file_path: str, caption: str | None = None, chat_id: str | None = None):
    chat_id = chat_id or DEFAULT_CHAT_ID
    if not chat_id:
        logger.warning("⚠️ CHAT_ID не указан — файл не отправлен.")
        return

    _run_async(_send_file(file_path, caption, chat_id))

    logger.info(f"📁 Файл отправлен: {file_path} (chat_id={chat_id})")
