# main.py
"""
ОПТИМИЗИРОВАННЫЙ главный модуль обработки новых файлов:
- Добавлен мониторинг памяти
- Оптимизирована работа с временными файлами
- Улучшено управление памятью
"""

import os
import pandas as pd
import gc
import psutil
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


def log_memory(step: str):
    """Логирование использования памяти"""
    process = psutil.Process(os.getpid())
    mb = process.memory_info().rss / 1024 / 1024
    logger.info(f"🧠 {step}: {mb:.1f} MB")


def cleanup_temp_files(*file_paths):
    """Очистка временных файлов и освобождение памяти"""
    for file_path in file_paths:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.debug(f"🗑️ Удалён временный файл: {os.path.basename(file_path)}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось удалить {file_path}: {e}")

    # Принудительная сборка мусора
    gc.collect()


def process_file(filename: str, aux_filename: str | None = None) -> None:
    """ОПТИМИЗИРОВАННАЯ обработка одного файла из Dropbox"""
    global last_card_path

    logger.info(f"=== Обработка файла {filename} ===")
    log_memory(f"Начало обработки {filename}")

    analyzer_func, config, requires_card = get_analyzer(filename)
    if not analyzer_func:
        logger.warning(f"⚠️ Не найден анализатор для {filename}")
        return

    local_path = os.path.join(LOCAL_TMP_PATH, filename)
    dropbox_path = f"{DROPBOX_INPUT_PATH}/{filename}"

    aux_local_path = None
    if aux_filename:
        aux_dropbox_path = f"{DROPBOX_INPUT_PATH}/{aux_filename}"
        aux_local_path = os.path.join(LOCAL_TMP_PATH, aux_filename)

        # 🔧 ОПТИМИЗАЦИЯ: Проверяем существование файла перед скачиванием
        if not os.path.exists(aux_local_path):
            if download_file(aux_dropbox_path, aux_local_path):
                logger.info(f"🧩 Вспомогательный файл скачан: {aux_filename}")

                # 🔧 ОПТИМИЗАЦИЯ: Немедленное перемещение вспомогательного файла
                try:
                    current_date = datetime.now().strftime("%d.%m.%Y")
                    aux_processed_name = f"{os.path.splitext(aux_filename)[0]}_({current_date}).xlsx"
                    aux_processed_path = f"{DROPBOX_PROCESSED_PATH}/{aux_processed_name}"

                    move_file(aux_dropbox_path, aux_processed_path)
                    logger.info(f"✅ Вспомогательный файл {aux_filename} перемещён в /processed.")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось переместить вспомогательный файл {aux_filename}: {e}")
            else:
                logger.warning(f"⚠️ Не удалось скачать вспомогательный файл {aux_filename}")
                aux_local_path = None

    # 🔧 ОПТИМИЗАЦИЯ: Формируем новое имя с датой ДО скачивания
    current_date = datetime.now().strftime("%d.%m.%Y")
    name, ext = os.path.splitext(filename)
    filename_with_date = f"{name}_({current_date}){ext}"

    # 1️⃣ Скачиваем основной файл
    if not download_file(dropbox_path, local_path):
        msg = f"❌ Не удалось скачать файл {filename} из Dropbox."
        logger.error(msg)
        send_message_sync(msg)

        # 🔧 ОПТИМИЗАЦИЯ: Очистка временных файлов при ошибке
        cleanup_temp_files(aux_local_path)
        return

    log_memory(f"После скачивания {filename}")

    is_card_file = any(tag in filename.lower() for tag in ["card", "cd"])

    # 2️⃣ Если это card-файл — просто сохраняем путь и выходим
    if is_card_file:
        # 🔧 ОПТИМИЗАЦИЯ: Очищаем предыдущий card-файл из памяти
        if last_card_path and last_card_path != local_path:
            cleanup_temp_files(last_card_path)

        last_card_path = local_path
        logger.info(f"🧩 Card/CD-файл загружен и сохранён: {filename}")

        try:
            move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename_with_date}")
            logger.info(f"✅ Файл {filename} перемещён в /processed.")
        except Exception as e:
            logger.error(f"⚠️ Ошибка при перемещении {filename}: {e}")

        log_memory(f"После обработки card-файла {filename}")
        return

    # 3️⃣ Определяем и запускаем нужный анализатор
    try:
        logger.info(f"🚀 Запуск анализа {analyzer_func.__module__}.run()...")

        # Определяем имя основного аргумента
        arg_name = "conv_file" if "conversion" in analyzer_func.__module__ else "payout_file"

        # 🔧 ОПТИМИЗАЦИЯ: если пришёл вспомогательный файл — используем его; если нет — fallback на last_card_path
        pair_path = aux_local_path or last_card_path

        if requires_card and not pair_path:
            msg = f"⚠️ Для {filename} не найден вспомогательный файл (card/cd). Анализ пропущен."
            logger.warning(msg)
            send_message_sync(msg)

            # 🔧 ОПТИМИЗАЦИЯ: Очистка временных файлов
            cleanup_temp_files(local_path, aux_local_path)
            return

        # 🔧 ОПТИМИЗАЦИЯ: Формируем kwargs с оптимизированными параметрами
        kwargs = {
            arg_name: local_path,
            "card_files": [pair_path] if requires_card else [],
            "generate_excel": True,
            "send_telegram": True
        }

        # если анализатор conversion — добавляем col_mapping
        if "conversion" in analyzer_func.__module__:
            from analyzers import conversion
            kwargs["col_mapping"] = conversion.COLUMNS

        log_memory(f"Перед запуском анализатора {analyzer_func.__module__}")
        result = analyzer_func(**kwargs)
        log_memory(f"После запуска анализатора {analyzer_func.__module__}")

        summary = result.get("summary", {})
        logger.info(f"✅ Анализ завершён: {summary}")

    except Exception as e:
        msg = f"❌ Ошибка в анализаторе {analyzer_func.__module__} для {filename}: {e}"
        logger.exception(msg)
        send_message_sync(msg)

        # 🔧 ОПТИМИЗАЦИЯ: Очистка при ошибке
        cleanup_temp_files(local_path, aux_local_path)
        return

    # 4️⃣ Перемещаем обработанный файл
    try:
        move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename_with_date}")
        logger.info(f"✅ Файл {filename} перемещён в /processed.")
    except Exception as e:
        logger.error(f"⚠️ Ошибка при перемещении {filename}: {e}")
        send_message_sync(f"⚠️ Ошибка при перемещении {filename}: {e}")

    # 🔧 ОПТИМИЗАЦИЯ: ФИНАЛЬНАЯ ОЧИСТКА ПАМЯТИ
    cleanup_temp_files(local_path, aux_local_path)

    # 🔧 ОПТИМИЗАЦИЯ: Очистка результатов анализа если они большие
    if 'result' in locals():
        if 'workbook' in result:
            del result['workbook']
        if 'problem_cards' in result and hasattr(result['problem_cards'], 'memory_usage'):
            del result['problem_cards']

    log_memory(f"Конец обработки {filename}")


# -----------------------------
# Точка входа
# -----------------------------
if __name__ == "__main__":
    import sys

    # 🔧 ОПТИМИЗАЦИЯ: Логирование памяти при старте
    log_memory("START main.py")

    if len(sys.argv) < 2:
        print("Использование: python main.py <имя_файла>")
        sys.exit(0)

    if not acquire_lock(timeout=600):
        logger.warning("🔒 Не удалось получить блокировку, выход")
        sys.exit(0)

    try:
        filename = sys.argv[1]
        process_file(filename)
    except Exception as e:
        logger.exception(f"❌ Критическая ошибка в main.py: {e}")
        send_message_sync(f"❌ Критическая ошибка в main.py: {e}")
    finally:
        release_lock()
        log_memory("END main.py")

        # 🔧 ОПТИМИЗАЦИЯ: Финальная очистка памяти
        gc.collect()