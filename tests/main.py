# tests/main.py
import os
import shutil
import logging
from utils.report_builder import build_report
from analyzers.selector import get_analyzer

# -------------------------------
# Настройка логов
# -------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# -------------------------------
# Пути
# -------------------------------
USE_TEST = True  # Заглушка Telegram и локальные файлы

INPUT_PATH = r"C:\Users\denis\PycharmProjects\analizis\tests\app\dropbox\input"
OUTPUT_PATH = r"C:\Users\denis\PycharmProjects\analizis\tests\app\dropbox\reports"
PROCESSED_PATH = r"C:\Users\denis\PycharmProjects\analizis\tests\app\dropbox\processed"

LOCAL_DATA = "data"
LOCAL_REPORTS = "reports"
os.makedirs(LOCAL_DATA, exist_ok=True)
os.makedirs(LOCAL_REPORTS, exist_ok=True)
os.makedirs(OUTPUT_PATH, exist_ok=True)
os.makedirs(PROCESSED_PATH, exist_ok=True)

# -------------------------------
# Заглушки Telegram
# -------------------------------
def send_message_sync(msg):
    if USE_TEST:
        logger.info(f"[TELEGRAM MESSAGE] {msg}")
    else:
        from integrations.telegram_bot import send_message_sync as real_send
        real_send(msg)

def send_file_sync(path):
    if USE_TEST:
        logger.info(f"[TELEGRAM FILE] {path}")
    else:
        from integrations.telegram_bot import send_file_sync as real_send
        real_send(path)

# -------------------------------
# Вспомогательные функции
# -------------------------------
def list_local_files(path):
    if not os.path.exists(path):
        logger.warning(f"Папка не найдена: {path}")
        return []
    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
    logger.info(f"Найдено файлов в {path}: {files}")
    return files

def copy_file(src, dst):
    logger.info(f"Копирование файла {src} -> {dst}")
    if not os.path.exists(src):
        logger.error(f"Файл не найден для копирования: {src}")
        return False
    try:
        shutil.copy2(src, dst)
        logger.info(f"Файл успешно скопирован: {dst}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при копировании {src} -> {dst}: {e}")
        return False

def move_file_local(src, dst):
    """Надёжное перемещение файла с проверкой и логами."""
    logger.info(f"Попытка перемещения файла {src} -> {dst}")
    if not os.path.exists(src):
        logger.warning(f"Файл не найден для перемещения: {src}")
        return False
    try:
        if os.path.exists(dst):
            os.remove(dst)
            logger.info(f"Старый файл удалён: {dst}")
        shutil.move(src, dst)
        if os.path.exists(dst) and not os.path.exists(src):
            logger.info(f"✅ Файл успешно перемещён: {dst}")
            return True
        else:
            logger.warning(f"⚠ Файл перемещён не полностью: {dst}")
            return False
    except PermissionError:
        logger.error(f"Файл {src} занят другим процессом и не может быть перемещён")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка при перемещении {src} -> {dst}: {e}")
        return False

# -------------------------------
# Обработка одного файла
# -------------------------------
def process_file(fname, all_files):
    logger.info(f"[{fname}] --- Начало обработки ---")
    input_file_path = os.path.join(INPUT_PATH, fname)
    local_file_path = os.path.join(LOCAL_DATA, fname)

    # Копируем файл в локальную папку
    if not copy_file(input_file_path, local_file_path):
        logger.error(f"[{fname}] Не удалось скопировать файл. Пропуск обработки.")
        return

    analyzer_func, config, requires_card = get_analyzer(fname)
    if not analyzer_func or not config:
        msg = f"❌ Не найден анализатор для файла {fname}"
        logger.warning(msg)
        send_message_sync(msg)
        return

    card_files = []
    try:
        if requires_card:
            # Находим карточные файлы, не перемещая их
            card_files = [
                os.path.join(INPUT_PATH, f) for f in all_files
                if "card" in f.lower() and os.path.exists(os.path.join(INPUT_PATH, f))
            ]
            logger.info(f"[{fname}] Найдено файлов для карты: {card_files}")

        result = analyzer_func(local_file_path, card_files, config.get("columns", {}))
        report_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")

        try:
            build_report(result, report_path)
            logger.info(f"[{fname}] Отчёт успешно сгенерирован: {report_path}")
        except Exception as e:
            logger.error(f"[{fname}] Ошибка при генерации отчёта: {e}")
            from openpyxl import Workbook
            wb = Workbook()
            wb.save(report_path)
            logger.info(f"[{fname}] Создан пустой отчёт: {report_path}")

        send_message_sync(f"✅ Отчёт по файлу {fname} готов")
        send_file_sync(report_path)

        # Копируем отчёт в OUTPUT_PATH
        copy_file(report_path, os.path.join(OUTPUT_PATH, f"report_{fname}.xlsx"))

    except Exception as e:
        logger.exception(f"[{fname}] Ошибка при обработке файла: {e}")
        send_message_sync(f"❌ Ошибка при обработке {fname}: {e}")

# -------------------------------
# Перемещение всех исходных файлов после обработки
# -------------------------------
def move_all_files_to_processed(files):
    for fname in files:
        input_file_path = os.path.join(INPUT_PATH, fname)
        if os.path.exists(input_file_path):
            move_file_local(input_file_path, os.path.join(PROCESSED_PATH, fname))
        local_file_path = os.path.join(LOCAL_DATA, fname)
        if os.path.exists(local_file_path):
            move_file_local(local_file_path, os.path.join(PROCESSED_PATH, fname))

# -------------------------------
# Основной цикл
# -------------------------------
def main_loop():
    logger.info("🔍 Запуск локального тестового пайплайна...")

    files = list_local_files(INPUT_PATH)
    if not files:
        logger.info("Нет файлов для обработки в тестовой папке.")
        return

    # Обрабатываем каждый файл
    for fname in files:
        process_file(fname, files)

    # Перемещаем все исходные файлы в PROCESSED_PATH после обработки
    move_all_files_to_processed(files)

    logger.info("✅ Локальная обработка завершена.")

if __name__ == "__main__":
    main_loop()
