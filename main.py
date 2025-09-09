import os
import time
import datetime
from concurrent.futures import ThreadPoolExecutor
from analyzers.selector import get_analyzer
from utils.report_builder import build_report
from integrations.dropbox_watcher import list_files, download_file, upload_file, move_file
from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.logger import logger

# -----------------------------
# Настройки
# -----------------------------
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


def parse_time(s: str) -> datetime.time:
    return datetime.datetime.strptime(s, "%H:%M").time()


sleep_start = parse_time(SLEEP_START)
sleep_end = parse_time(SLEEP_END)


def in_sleep_time(now: datetime.time) -> bool:
    """Проверка ночного режима"""
    if sleep_start < sleep_end:
        return sleep_start <= now <= sleep_end
    return now >= sleep_start or now <= sleep_end


def get_card_files(all_files: list[str]):
    """Находим все файлы Card_*"""
    return [f for f in all_files if "card" in f.lower()]


def process_file(fname: str, all_files: list[str]):
    dropbox_file_path = f"{INPUT_PATH}/{fname}"
    local_file_path = os.path.join(LOCAL_DATA, fname)
    logger.info(f"[{fname}] Начинаем обработку")

    if not download_file(dropbox_file_path, local_file_path):
        msg = f"❌ [{fname}] Не удалось скачать файл"
        logger.error(msg)
        send_message_sync(msg)
        return

    analyzer_func, config, _ = get_analyzer(fname)
    if not analyzer_func or not config:
        msg = f"❌ [{fname}] Не найден анализатор"
        logger.warning(msg)
        send_message_sync(msg)
        return

    try:
        # Если conversion, ищем все файлы Card_*
        card_files = get_card_files(all_files) if config.get("file_pattern") == "conversion" else []
        local_card_paths = []
        for cf in card_files:
            local_path = os.path.join(LOCAL_DATA, cf)
            if not os.path.exists(local_path):
                if not download_file(f"{INPUT_PATH}/{cf}", local_path):
                    msg = f"❌ [{fname}] Не удалось скачать {cf}"
                    logger.error(msg)
                    send_message_sync(msg)
                    return
            local_card_paths.append(local_path)

        # Запуск анализатора
        if card_files:
            result = analyzer_func(local_file_path, local_card_paths, config.get("columns", {}))
        else:
            result = analyzer_func(local_file_path, config.get("columns", {}))

        # Сохраняем отчёт
        report_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")
        build_report(result, report_path)
        logger.info(f"[{fname}] Отчёт сохранён локально: {report_path}")

        # Отправка Telegram
        send_message_sync(f"✅ [{fname}] Отчёт готов")
        if "Отключить" in result.get("data_sheets", {}):
            temp_file = os.path.join(LOCAL_REPORTS, f"disable_{fname}.xlsx")
            build_report({"data_sheets": {"Отключить": result["data_sheets"]["Отключить"]}}, temp_file)
            send_file_sync(temp_file)
        send_file_sync(report_path)

        # Загрузка в Dropbox и перемещение исходника
        if upload_file(report_path, f"{OUTPUT_PATH}/report_{fname}.xlsx"):
            logger.info(f"[{fname}] Отчёт загружен в Dropbox")
        if move_file(dropbox_file_path, f"{PROCESSED_PATH}/{fname}"):
            logger.info(f"[{fname}] Файл перемещён в {PROCESSED_PATH}")

    except Exception as e:
        logger.exception(f"[{fname}] Ошибка при обработке: {e}")
        send_message_sync(f"❌ [{fname}] Ошибка при обработке: {e}")


def main_loop():
    logger.info("Запуск автоматического пайплайна...")
    processed_files = set()

    with ThreadPoolExecutor(max_workers=3) as executor:
        while True:
            now = datetime.datetime.now().time()
            if in_sleep_time(now):
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
                    executor.submit(process_file, fname, files)
                    processed_files.add(fname)

            except Exception as e:
                logger.exception(f"Ошибка при сканировании Dropbox: {e}")
                send_message_sync(f"❌ Ошибка при сканировании Dropbox: {e}")

            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main_loop()
