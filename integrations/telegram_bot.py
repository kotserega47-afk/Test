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
#   HTTP-клиент Telegram с УВЕЛИЧЕННЫМ ПУЛОМ
# =====================================================
request = HTTPXRequest(
    connection_pool_size=100,     # раньше 20 → было мало!
    connect_timeout=20.0,
    read_timeout=40.0,
)

bot = Bot(token=TELEGRAM_TOKEN, request=request)


# =====================================================
#   СОЗДАЕМ ЕДИНЫЙ EVENT LOOP
# =====================================================
try:
    loop = asyncio.get_event_loop()
    if not loop.is_running():
        raise RuntimeError
except Exception:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


# =====================================================
#   ОЧЕРЕДЬ на отправку (ТОЛЬКО ПО ОДНОМУ платежу)
# =====================================================
queue: asyncio.Queue = asyncio.Queue()


async def _worker():
    """
    Фоновый воркер — берет задачи из очереди и отправляет их
    строго последовательно, предотвращая Pool timeout.
    """
    while True:
        func, args = await queue.get()
        try:
            await func(*args)
        except Exception as e:
            logger.error(f"❌ Ошибка async отправки: {e}")
        queue.task_done()

# запускаем воркер
loop.create_task(_worker())


# =====================================================
#   async-функции отправки
# =====================================================

async def _send_message(chat_id: str, text: str):
    await bot.send_message(chat_id=chat_id, text=text)


async def _send_file(chat_id: str, path: str, caption: str | None):
    with open(path, "rb") as f:
        await bot.send_document(chat_id=chat_id, document=InputFile(f), caption=caption)


# =====================================================
#   ПУБЛИЧНЫЕ СИНХРОННЫЕ ФУНКЦИИ
# =====================================================

def send_message_sync(content: str, chat_id: str | None = None):
    chat_id = chat_id or DEFAULT_CHAT_ID

    try:
        # отправляем в очередь
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (_send_message, (chat_id, content))
        )
        logger.info(f"📨 Добавлено в очередь сообщение ({chat_id}): {content[:60]}")

    except Exception as e:
        logger.error(f"❌ Ошибка постановки в очередь send_message: {e}")


def send_file_sync(file_path: str, caption: str | None = None, chat_id: str | None = None):
    chat_id = chat_id or DEFAULT_CHAT_ID

    try:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (_send_file, (chat_id, file_path, caption))
        )
        logger.info(f"📁 Файл поставлен в очередь на отправку: {file_path}")

    except Exception as e:
        logger.error(f"❌ Ошибка постановки в очередь send_file: {e}")
