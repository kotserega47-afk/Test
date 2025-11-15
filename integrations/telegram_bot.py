# integrations/telegram_bot.py

import os
import asyncio
import threading
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
#   HTTP client
# =====================================================
request = HTTPXRequest(
    connection_pool_size=100,
    connect_timeout=20.0,
    read_timeout=40.0,
)

bot = Bot(token=TELEGRAM_TOKEN, request=request)


# =====================================================
#   GLOBAL EVENT LOOP + BACKGROUND THREAD
# =====================================================

loop = asyncio.new_event_loop()
queue = asyncio.Queue()


def _loop_runner():
    """Фоновый поток, который крутит event loop постоянно."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=_loop_runner, daemon=True).start()


# =====================================================
#   Worker
# =====================================================

async def _worker():
    while True:
        func, args = await queue.get()
        try:
            await func(*args)

            # лог успешной отправки
            if func is _send_message:
                chat_id, text = args
                logger.info(f"📤 Отправлено сообщение (chat_id={chat_id}): {text[:80]}")
            else:
                chat_id, path, caption = args
                logger.info(f"📁 Отправлен файл (chat_id={chat_id}): {path}")

        except Exception as e:
            logger.error(f"❌ Ошибка async отправки: {e}")
        queue.task_done()


# запускаем async worker внутри event loop
loop.call_soon_threadsafe(loop.create_task, _worker())


# =====================================================
#   async send funcs
# =====================================================

async def _send_message(chat_id: str, text: str):
    await bot.send_message(chat_id=chat_id, text=text)


async def _send_file(chat_id: str, path: str, caption: str | None):
    with open(path, "rb") as f:
        await bot.send_document(chat_id=chat_id, document=InputFile(f), caption=caption)


# =====================================================
#   PUBLIC sync API
# =====================================================

def send_message_sync(content: str, chat_id: str | None = None):
    chat_id = chat_id or DEFAULT_CHAT_ID

    try:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (_send_message, (chat_id, content))
        )
        logger.info(f"📨 Добавлено в очередь сообщение ({chat_id}): {content[:60]}")

    except Exception as e:
        logger.error(f"❌ Ошибка постановки в очередь send_message: {e}")


def send_file_sync(path: str, caption: str | None = None, chat_id: str | None = None):
    chat_id = chat_id or DEFAULT_CHAT_ID

    try:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (_send_file, (chat_id, path, caption))
        )
        logger.info(f"📨 Файл поставлен в очередь: {path}")

    except Exception as e:
        logger.error(f"❌ Ошибка постановки в очередь send_file: {e}")
