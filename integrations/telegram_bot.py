# integrations/telegram_bot.py
import os
import threading
import queue
import time
from telegram import Bot
from telegram.error import TelegramError
from utils.logger import logger

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    raise ValueError("Telegram токен или chat_id не найдены!")

bot = Bot(token=TOKEN)

# Очередь сообщений
_msg_queue = queue.Queue()

def _sender_loop():
    """Фоновый поток для отправки сообщений из очереди."""
    while True:
        item = _msg_queue.get()
        if item is None:
            break  # сигнал завершения
        msg_type, content = item
        try:
            if msg_type == "text":
                bot.send_message(chat_id=CHAT_ID, text=content)
            elif msg_type == "file":
                bot.send_document(chat_id=CHAT_ID, document=open(content, 'rb'))
            time.sleep(0.1)  # маленькая пауза, чтобы не перегружать Telegram
        except TelegramError as e:
            logger.error(f"Failed to send message/file: {e}")
        finally:
            _msg_queue.task_done()

# Запускаем поток
_thread = threading.Thread(target=_sender_loop, daemon=True)
_thread.start()

def send_message_sync(text: str):
    """Добавляем текстовое сообщение в очередь."""
    _msg_queue.put(("text", text))

def send_file_sync(file_path: str):
    """Добавляем файл в очередь на отправку."""
    _msg_queue.put(("file", file_path))

def shutdown():
    """Останавливаем фоновый поток (вызывать при завершении программы)."""
    _msg_queue.put(None)
    _thread.join()
