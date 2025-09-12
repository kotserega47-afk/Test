import os
import logging
from utils.report_builder import build_report
from analyzers.selector import get_analyzer
from integrations.dropbox_watcher import list_files, download_file, upload_file, move_file
from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.logger import logger

# -------------------------------
# Пути
# -------------------------------
LOCAL_DATA = "data"
LOCAL_REPORTS = "reports"
os.makedirs(LOCAL_DATA, exist_ok=True)
os.makedirs(LOCAL_REPORTS, exist_ok=True)

INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
OUTPUT_PATH = os.getenv("DROPBOX_OUTPUT_PATH")
PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")

# -------------------------------
# Вспомогательные функции
# -------------------------------
def download_to_local(fname: str) -> str | None:
    """Скачивает файл из Dropbox в локальную папку"""
    dropbox_file_path = f"{INPUT_PATH}/{fname}"
    local_file_path = os.path.join(LOCAL_DATA, fname)
    if download_file(dropbox_file_path, local_file_path):
        logger.info(f"Файл скачан: {fname}")
        return local_file_path
    else:
        logger.error(f"Не удалось скачать {fname}")
        return None


def upload_report(local_path: str, fname: str):
    """Загружает готовый отчёт в Dropbox"""
    try:
        dropbox_report_path = f"{OUTPUT_PATH}/report_{fname}.xlsx"
        upload_file(local_path, dropbox_report_path)
        logger.info(f"Отчёт загружен в Dropbox: {dropbox_report_path}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке отчёта {fname}: {e}")


def move_to_processed(fname: str):
    """Переносит файл в PROCESSED в Dropbox"""
    try:
        move_file(f"{INPUT_PATH}/{fname}", f"{PROCESSED_PATH}/{fname}")
        logger.info(f"Файл {fname} перемещён в {PROCESSED_PATH}")
    except Exception as e:
        logger.error(f"Ошибка при переносе {fname}: {e}")

# -------------------------------
# Обработка одного файла
# -------------------------------
def process_file(fname: str, all_files: list[str]):
    logger.info(f"[{fname}] --- Начало обработки ---")

    # Скачиваем основной файл
    local_file_path = download_to_local(fname)
    if not local_file_path:
        send_message_sync(f"❌ Не удалось скачать {fname}")
        return

    # Получаем анализатор
    analyzer_func, config, requires_card = get_analyzer(fname)
    if not analyzer_func or not config:
        msg = f"❌ Не найден анализатор для файла {fname}"
        logger.warning(msg)
        send_message_sync(msg)
        return

    # Скачиваем карточные файлы, если требуется
    card_files = []
    try:
        if requires_card:
            for f in all_files:
                if "card" in f.lower():
                    local_card = download_to_local(f)
                    if local_card:
                        card_files.append(local_card)
            logger.info(f"[{fname}] Найдено файлов для карты: {card_files}")

        # Запуск анализатора
        result = analyzer_func(local_file_path, card_files, config.get("columns", {}))
        report_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")

        # Генерация отчёта
        try:
            build_report(result, report_path)
            logger.info(f"[{fname}] Отчёт успешно сгенерирован: {report_path}")
        except Exception as e:
            logger.error(f"[{fname}] Ошибка при генерации отчёта: {e}")
            from openpyxl import Workbook
            wb = Workbook()
            wb.save(report_path)
            logger.info(f"[{fname}] Создан пустой отчёт: {report_path}")

        # Отправка отчёта в Telegram
        send_message_sync(f"✅ Отчёт по файлу {fname} готов")
        send_file_sync(report_path)

        # Отправка листа "Отключить" в Telegram, если есть проблемные карты
        problem_cards_df = result.get("problem_cards")
        if problem_cards_df is not None and not problem_cards_df.empty:
            from openpyxl import Workbook
            from openpyxl.utils.dataframe import dataframe_to_rows

            disable_path = os.path.join(LOCAL_REPORTS, f"Отключить_{fname}.xlsx")
            wb_disable = Workbook()
            ws_disable = wb_disable.active
            ws_disable.title = "Отключить"
            for r in dataframe_to_rows(problem_cards_df, index=False, header=True):
                ws_disable.append(r)
            wb_disable.save(disable_path)

            send_message_sync(f"📢 Карты на отключение для файла {fname}")
            send_file_sync(disable_path)

        # Загрузка отчёта в Dropbox
        upload_report(report_path, fname)

    except Exception as e:
        logger.exception(f"[{fname}] Ошибка при обработке файла: {e}")
        send_message_sync(f"❌ Ошибка при обработке {fname}: {e}")

# -------------------------------
# Основной цикл
# -------------------------------
def main_loop():
    logger.info("🔍 Запуск боевого пайплайна (Dropbox)...")

    try:
        files = list_files(INPUT_PATH)
        if not files:
            logger.info("Нет файлов для обработки в Dropbox.")
            return

        # Обрабатываем каждый файл
        for fname in files:
            process_file(fname, files)

        # Перемещаем все исходные файлы в PROCESSED
        move_all_files_to_processed(files)

        logger.info("✅ Обработка завершена.")

    except Exception as e:
        logger.exception(f"❌ Ошибка при сканировании Dropbox: {e}")
        send_message_sync(f"❌ Ошибка при сканировании Dropbox: {e}")


if __name__ == "__main__":
    import time
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL"))  # или оставить существующее значение

    while True:
        main_loop()
        time.sleep(CHECK_INTERVAL)