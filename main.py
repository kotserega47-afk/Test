import os
import time
import datetime
from utils.report_builder import build_report
from integrations.dropbox_watcher import list_files, download_file, upload_file, move_file
from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.logger import logger
from analyzers.selector import get_analyzer

SLEEP_START = os.getenv("SLEEP_START", "01:00")
SLEEP_END = os.getenv("SLEEP_END", "07:00")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))

LOCAL_DATA = "data"
LOCAL_REPORTS = "reports"
os.makedirs(LOCAL_DATA, exist_ok=True)
os.makedirs(LOCAL_REPORTS, exist_ok=True)

INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
OUTPUT_PATH = os.getenv("DROPBOX_OUTPUT_PATH")
PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")

DROPBOX_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN") or os.getenv("DROPBOX_REFRESH_TOKEN")
if not DROPBOX_TOKEN:
    raise ValueError(
        "Dropbox токен не найден! Задайте DROPBOX_ACCESS_TOKEN или DROPBOX_REFRESH_TOKEN + APP_KEY + APP_SECRET"
    )


def parse_time(s: str) -> datetime.time:
    return datetime.datetime.strptime(s, "%H:%M").time()


sleep_start = parse_time(SLEEP_START)
sleep_end = parse_time(SLEEP_END)


def in_sleep_time(now: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    if start < end:
        return start <= now <= end
    return now >= start or now <= end


def process_file(fname: str):
    dropbox_file_path = f"{INPUT_PATH}/{fname}"
    local_file_path = os.path.join(LOCAL_DATA, fname)

    logger.info(f"Начинаем обработку файла: {fname}")

    if not download_file(dropbox_file_path, local_file_path):
        msg = f"❌ Не удалось скачать {fname}"
        logger.error(msg)
        send_message_sync(msg)
        return

    analyzer_func, config, requires = get_analyzer(fname)
    if not analyzer_func:
        msg = f"❌ Не найден анализатор для файла {fname}"
        logger.warning(msg)
        send_message_sync(msg)
        return

    columns = config.get("columns")
    if not columns:
        msg = f"❌ В конфиге нет 'columns' для {fname}"
        logger.error(msg)
        send_message_sync(msg)
        return

    # Загружаем обязательные файлы, если они указаны
    required_local_files = {}
    if requires:
        for req in requires:
            req_path = f"{INPUT_PATH}/{req}"
            local_req_path = os.path.join(LOCAL_DATA, req)
            if download_file(req_path, local_req_path):
                required_local_files[req] = local_req_path
                logger.info(f"Загружен обязательный файл {req}")
            else:
                msg = f"❌ Не удалось загрузить обязательный файл {req}"
                logger.error(msg)
                send_message_sync(msg)
                return

    try:
        # Передаём зависимости в анализатор (если нужны)
        if required_local_files:
            result = analyzer_func(local_file_path, columns, required_local_files)
        else:
            result = analyzer_func(local_file_path, columns)

        if "error" in result:
            raise ValueError(result["error"])

        report_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")
        build_report(result, report_path)
        logger.info(f"Отчёт сохранён локально: {report_path}")

        send_message_sync(f"✅ Отчёт по файлу {fname} готов")
        send_file_sync(report_path)

        if upload_file(report_path, f"{OUTPUT_PATH}/report_{fname}.xlsx"):
            logger.info(f"Отчёт загружен в Dropbox: {OUTPUT_PATH}/report_{fname}.xlsx")
        if move_file(dropbox_file_path, f"{PROCESSED_PATH}/{fname}"):
            logger.info(f"Файл {fname} перемещён в {PROCESSED_PATH}")

    except Exception as e:
        logger.exception(f"Ошибка при обработке {fname}: {e}")
        send_message_sync(f"❌ Ошибка при обработке {fname}: {e}")


def main_loop():
    logger.info("Запуск автоматического пайплайна...")
    processed_files = set()

    while True:
        now = datetime.datetime.now().time()
        if in_sleep_time(now, sleep_start, sleep_end):
            logger.info(f"Ночной режим: пауза до {SLEEP_END}")
            today = datetime.date.today()
            tomorrow = today + datetime.timedelta(days=1)
            wake_time = datetime.datetime.combine(today, sleep_end)
            if sleep_start > sleep_end and now >= sleep_start:
                wake_time = datetime.datetime.combine(tomorrow, sleep_end)
            elif now > sleep_end:
                wake_time = datetime.datetime.combine(tomorrow, sleep_end)
            time.sleep(max(0, (wake_time - datetime.datetime.now()).seconds))
            continue

        try:
            files = list_files(INPUT_PATH)
            new_files = [f for f in files if f not in processed_files]

            if not new_files:
                logger.info("Нет новых файлов для обработки")
            for fname in new_files:
                process_file(fname, files)
                processed_files.add(fname)

        except Exception as e:
            logger.exception(f"Ошибка при сканировании Dropbox: {e}")
            send_message_sync(f"❌ Ошибка при сканировании Dropbox: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main_loop()
