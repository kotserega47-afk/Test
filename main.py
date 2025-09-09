import os
import time
import datetime
from utils.report_builder import build_report
from integrations.dropbox_watcher import list_files, download_file, upload_file, move_file
from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.logger import logger
from analyzers.selector import get_analyzer


logger.info("🔍 Проверка файлов конфигурации перед стартом")

cwd = os.getcwd()
logger.info(f"Текущая рабочая директория: {cwd}")

config_dir = os.path.join(cwd, "config")
if os.path.exists(config_dir):
    files = os.listdir(config_dir)
    logger.info(f"Содержимое /config: {files}")
    # Проверяем конкретные файлы
    for fname in ["conversion_config.yaml", "analysis_map.yaml"]:
        path = os.path.join(config_dir, fname)
        if os.path.exists(path):
            logger.info(f"✅ Файл найден: {fname}")
        else:
            logger.warning(f"❌ Файл отсутствует: {fname}")
else:
    logger.warning("❌ Папка /config не найдена в контейнере")

LOCAL_DATA = "data"
LOCAL_REPORTS = "reports"
os.makedirs(LOCAL_DATA, exist_ok=True)
os.makedirs(LOCAL_REPORTS, exist_ok=True)

INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
OUTPUT_PATH = os.getenv("DROPBOX_OUTPUT_PATH")
PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))

def process_file(fname: str, all_files: list[str]):
    dropbox_file_path = f"{INPUT_PATH}/{fname}"
    local_file_path = os.path.join(LOCAL_DATA, fname)

    logger.info(f"[{fname}] Начинаем обработку файла")
    if not download_file(dropbox_file_path, local_file_path):
        msg = f"❌ Не удалось скачать {fname}"
        logger.error(msg)
        send_message_sync(msg)
        return

    analyzer_func, config, requires_card = get_analyzer(fname)
    if not analyzer_func or not config:
        msg = f"❌ Не найден анализатор для файла {fname}"
        logger.warning(msg)
        send_message_sync(msg)
        return

    try:
        card_files = []
        if requires_card:
            card_files = [os.path.join(LOCAL_DATA, f) for f in all_files if "card" in f.lower()]
            for cf in card_files:
                if not os.path.exists(cf):
                    if not download_file(f"{INPUT_PATH}/{os.path.basename(cf)}", cf):
                        msg = f"❌ Не удалось скачать {os.path.basename(cf)}"
                        logger.error(msg)
                        send_message_sync(msg)
                        return

        result = analyzer_func(local_file_path, card_files, config.get("columns", {}))
        report_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")
        build_report(result, report_path)
        logger.info(f"[{fname}] Отчёт сохранён локально: {report_path}")

        # Отправка в Telegram по очереди
        send_message_sync(f"✅ Отчёт по файлу {fname} готов")
        send_file_sync(report_path)

        upload_file(report_path, f"{OUTPUT_PATH}/report_{fname}.xlsx")
        move_file(dropbox_file_path, f"{PROCESSED_PATH}/{fname}")

    except Exception as e:
        logger.exception(f"[{fname}] Ошибка при обработке: {e}")
        send_message_sync(f"❌ Ошибка при обработке {fname}: {e}")

def main_loop():
    logger.info("Запуск автоматического пайплайна...")
    processed_files = set()

    while True:
        try:
            files = list_files(INPUT_PATH)
            new_files = [f for f in files if f not in processed_files]

            if new_files:
                for fname in new_files:
                    process_file(fname, files)
                    processed_files.add(fname)
            else:
                logger.info("Нет новых файлов для обработки")
        except Exception as e:
            logger.exception(f"Ошибка при сканировании Dropbox: {e}")
            send_message_sync(f"❌ Ошибка при сканировании Dropbox: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main_loop()
