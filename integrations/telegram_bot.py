# integrations/telegram_bot.py
import os
import asyncio
import logging
from telegram import Bot
from telegram.error import TelegramError

# Загружаем токен и чат
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = Bot(token=TOKEN)

# --- Настройка логирования ---
logging.basicConfig(
    filename="logs/telegram.log",  # файл логов
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


async def send_message(text: str):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text)
        logging.info(f"Message sent: {text}")
    except TelegramError as e:
        logging.error(f"Failed to send message: {e}")
        # Дублируем ошибку в Telegram
        try:
            await bot.send_message(chat_id=CHAT_ID, text=f"❌ Ошибка при отправке сообщения: {e}")
        except:
            pass


def send_message_sync(text: str):
    asyncio.run(send_message(text))


async def send_file(file_path: str):
    try:
        with open(file_path, "rb") as f:
            await bot.send_document(chat_id=CHAT_ID, document=f)
        logging.info(f"File sent: {file_path}")
    except TelegramError as e:
        logging.error(f"Failed to send file {file_path}: {e}")
        try:
            await bot.send_message(chat_id=CHAT_ID, text=f"❌ Ошибка при отправке файла: {e}")
        except:
            pass


def send_file_sync(file_path: str):
    asyncio.run(send_file(file_path))
