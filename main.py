# main.py
"""
Главный модуль обработки новых файлов:
- анализирует conversion-файлы с помощью analyzers/conversion.py;
- использует последний card-файл как справочник;
- запускает анализ, Telegram и формирование отчёта;
- перемещает обработанные файлы в Dropbox /processed.
"""

import os
import pandas as pd
from utils.logger import logger
from integrations.telegram_bot import send_message_sync
from integrations.dropbox_watcher import download_file, move_file
from analyzers import conversion
from run_once_guard import acquire_lock, release_lock
from datetime import datetime
from analyzers.selector import get_analyzer

DROPBOX_INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
DROPBOX_PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")
LOCAL_TMP_PATH = "/tmp"

# 🧩 сохраняем путь к последнему card-файлу
last_card_path = None


def process_file(filename: str) -> None:
    """Обработка одного файла из Dropbox"""
    global last_card_path

    logger.info(f"=== Обработка файла {filename} ===")
    analyzer_func, config, requires_card = get_analyzer(filename)
    if not analyzer_func:
        logger.warning(f"⚠️ Не найден анализатор для {filename}")
        return
    local_path = os.path.join(LOCAL_TMP_PATH, filename)
    dropbox_path = f"{DROPBOX_INPUT_PATH}/{filename}"

    # Формируем новое имя с датой в формате (ДД.ММ.ГГГГ)
    current_date = datetime.now().strftime("%d.%m.%Y")
    name, ext = os.path.splitext(filename)
    filename_with_date = f"{name}_({current_date}){ext}"

    # 1️⃣ Скачиваем файл
    if not download_file(dropbox_path, local_path):
        msg = f"❌ Не удалось скачать файл {filename} из Dropbox."
        logger.error(msg)
        send_message_sync(msg)
        return

    is_card_file = any(tag in filename.lower() for tag in ["card", "cd"])

    # 2️⃣ Если это card-файл — просто сохраняем путь
    if is_card_file:
        last_card_path = local_path
        logger.info(f"🧩 Card/CD-файл загружен и сохранён: {filename}")
        move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename}")
        logger.info(f"✅ Файл {filename} перемещён в /processed.")
        return

    # 3️⃣ Определяем и запускаем нужный анализатор

    try:
        logger.info(f"🚀 Запуск анализа {analyzer_func.__module__}.run()...")
        # Определяем имя основного аргумента
        arg_name = "conv_file" if "conversion" in analyzer_func.__module__ else "payout_file"

        kwargs = {
            arg_name: local_path,
            "card_files": [last_card_path] if requires_card else []
        }
        if requires_card and not last_card_path:
            msg = f"⚠️ Для {filename} не найден cd/card-файл. Анализ пропущен."
            logger.warning(msg)
            send_message_sync(msg)
            return

        result = analyzer_func(**kwargs)

        summary = result.get("summary", {})
        logger.info(f"✅ Анализ завершён: {summary}")
    except Exception as e:
        msg = f"❌ Ошибка в анализаторе {analyzer_func.__module__} для {filename}: {e}"
        logger.exception(msg)
        send_message_sync(msg)
        return

    # 4️⃣ Перемещаем обработанный файл
    try:
        move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename_with_date}")
        logger.info(f"✅ Файл {filename} перемещён в /processed.")
    except Exception as e:
        logger.error(f"⚠️ Ошибка при перемещении {filename}: {e}")
        send_message_sync(f"⚠️ Ошибка при перемещении {filename}: {e}")


# -----------------------------
# Точка входа
# -----------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Использование: python main.py <имя_файла>")
        sys.exit(0)

    if not acquire_lock(timeout=600):
        sys.exit(0)

    try:
        filename = sys.argv[1]
        process_file(filename)
    finally:
        release_lock()
