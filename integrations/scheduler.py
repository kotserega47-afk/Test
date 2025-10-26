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
import re
from collections import defaultdict

def group_files_by_prefix(file_list):
    """
    Группирует файлы по префиксу: card_13.46.xlsx → префикс=13.46
    Возвращает dict: { '13.46': {'card': ..., 'conversion': ...}, ... }
    """
    pattern = re.compile(r"(card|cd|conversion|payout)[_\-]?(.+?)\.xlsx", re.IGNORECASE)
    grouped = defaultdict(dict)

    for fname in file_list:
        match = pattern.match(fname)
        if match:
            kind, prefix = match.groups()
            grouped[prefix.strip()][kind.lower()] = fname

    return grouped
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
            if not acquire_lock(timeout=600):
                logger.info("⏳ Предыдущий анализ ещё идёт, пропуск итерации.")
                time.sleep(CHECK_INTERVAL)
                continue

            try:
                logger.info(f"🔍 Проверка новых файлов в {DROPBOX_INPUT_PATH}...")
                print(os.getenv("DROPBOX_INPUT_PATH"))
                files = list_files(DROPBOX_INPUT_PATH)
                if not files:
                    logger.info("📂 Новых файлов не найдено.")

                grouped = group_files_by_prefix(files)

                for prefix, pair in grouped.items():
                    # card/conversion
                    card = pair.get("card")
                    conv = pair.get("conversion")
                    # cd/payout
                    cd = pair.get("cd")
                    payout = pair.get("payout")

                    # --- обработка conversion (старое поведение)
                    if card and conv:
                        if card not in processed:
                            logger.info(f"🧩 Загружаем card-файл: {card}")
                            process_file(card)
                            processed.add(card)
                        if conv not in processed:
                            logger.info(f"🚀 Обработка conversion-файла: {conv} (пара с {card})")
                            process_file(conv, aux_filename=card)
                            processed.add(conv)

                    # --- обработка payout (новое поведение)
                    elif cd and payout:
                        # Сначала обрабатываем cd (вспомогательный)
                        if cd not in processed:
                            logger.info(f"🧩 Загружаем cd-файл: {cd}")
                            process_file(cd)
                            processed.add(cd)

                        # Затем основной payout, передавая вспомогательный файл
                        if payout not in processed:
                            logger.info(f"🚀 Обработка payout-файла: {payout} (пара с {cd})")
                            process_file(payout, aux_filename=cd)
                            processed.add(payout)

                    else:
                        logger.info(
                            f"⏳ Пропуск пары {prefix}: не хватает "
                            f"{'card/cd' if not (card or cd) else 'conversion/payout'}"
                        )

            finally:
                release_lock()

        except Exception as e:
            logger.exception(f"Ошибка планировщика: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_scheduler()
