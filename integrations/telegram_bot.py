# integrations/telegram_bot.py

import os
import asyncio
import threading
from telegram import Bot, InputFile
from telegram.request import HTTPXRequest
from utils.logger import logger
from dotenv import load_dotenv
import requests

# Загружаем .env (для CLI и прямых запусков)
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

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
#   PUBLIC sync API — chat_id ОБЯЗАТЕЛЕН
# =====================================================

def send_message_sync(text: str, chat_id: str):
    """
    Отправка текста в очередь.
    chat_id должен передаваться ЯВНО!
    """
    if not chat_id:
        raise ValueError("chat_id обязателен для send_message_sync")

    try:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (_send_message, (chat_id, text))
        )
        logger.info(f"📨 Добавлено в очередь сообщение ({chat_id}): {text[:60]}")

    except Exception as e:
        logger.error(f"❌ Ошибка постановки в очередь send_message: {e}")

def send_photo_sync(photo_path: str, caption: str, chat_id: str):
    """Отправляет фото (например, скриншот) с подписью"""
    try:
        with open(photo_path, "rb") as photo:
            requests.post(
                f"{BASE_URL}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": photo},
                timeout=30
            )
    except Exception as e:
        print(f"[telegram] Ошибка отправки фото: {e}")
        # резервный вариант — если файл не открылся
        send_message_sync(f"⚠️ Ошибка при отправке скриншота: {e}", chat_id)


def send_file_sync(path: str, caption: str | None, chat_id: str):
    """
    Отправка файла в очередь.
    chat_id должен передаваться ЯВНО!
    """
    if not chat_id:
        raise ValueError("chat_id обязателен для send_file_sync")

    try:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (_send_file, (chat_id, path, caption))
        )
        logger.info(f"📨 Файл поставлен в очередь ({chat_id}): {path}")

    except Exception as e:
        logger.error(f"❌ Ошибка постановки в очередь send_file: {e}")


# =====================================================
#   DIRECT SEND (используется только тестами)
# =====================================================

def send_message_direct(text: str, chat_id: str):
    """
    Прямая отправка без очереди — chat_id обязателен.
    """
    if not chat_id:
        raise ValueError("chat_id обязателен для send_message_direct")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text
    }, timeout=10)

    resp.raise_for_status()
    return resp.json()
