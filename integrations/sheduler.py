# integrations/scheduler.py
"""
Автоматический планировщик анализа Dropbox:
- проверяет входящую папку каждые N секунд (CHECK_INTERVAL);
- запускает process_file() из main.py для новых файлов;
- использует run_once_guard, чтобы исключить пересечения запусков.
"""

import os
import time
from integrations.dropbox_watcher import list_files
from main import process_file
from run_once_guard import acquire_lock, release_lock
from utils.logger import logger

# Параметры из окружения
DROPBOX_INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))

# Расширения файлов, которые обрабатываем
VALID_EXT = (".xlsx", ".xls", ".csv")

def run_scheduler():
    logger.info(f"🕒 Планировщик запущен. Интервал проверки: {CHECK_INTERVAL} сек.")
    processed = set()  # чтобы не обрабатывать один и тот же файл повторно

    while True:
        try:
            # --- Проверяем, можно ли запускать пайплайн ---
            if not acquire_lock(timeout=600):
                logger.info("⏳ Предыдущий анализ ещё идёт, пропуск итерации.")
                time.sleep(CHECK_INTERVAL)
                continue

            try:
                # --- Сканируем Dropbox ---
                logger.info(f"🔍 Проверка новых файлов в {DROPBOX_INPUT_PATH}...")
                files = list_files(DROPBOX_INPUT_PATH)
                if not files:
                    logger.info("📂 Новых файлов не найдено.")

                for filename in files:
                    if filename.endswith(VALID_EXT) and filename not in processed:
                        logger.info(f"🆕 Найден новый файл: {filename}")
                        process_file(filename)
                        processed.add(filename)

            finally:
                release_lock()

        except Exception as e:
            logger.exception(f"Ошибка планировщика: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_scheduler()
