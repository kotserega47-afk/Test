import os
import time
import datetime
from utils.report_builder import build_report
from integrations.dropbox_watcher import list_files, download_file, upload_file, move_file
from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.logger import logger
from analyzers.selector import get_analyzer  # функция выбора анализатора по имени файла


SLEEP_START = os.getenv("SLEEP_START", "01:00")
SLEEP_END = os.getenv("SLEEP_END", "07:00")


def parse_time(s: str) -> datetime.time:
    return datetime.datetime.strptime(s, "%H:%M").time()


sleep_start = parse_time(SLEEP_START)
sleep_end = parse_time(SLEEP_END)

# -----------------------------
# Проверка токенов
# -----------------------------
DROPBOX_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN") or os.getenv("DROPBOX_REFRESH_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")

if not DROPBOX_TOKEN:
    raise ValueError(
        "Dropbox токен не найден! Задайте DROPBOX_ACCESS_TOKEN или DROPBOX_REFRESH_TOKEN + APP_KEY + APP_SECRET"
    )

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
    raise ValueError(
        "Telegram токен или chat_id не найдены! Задайте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID"
    )

# -----------------------------
# Пути Dropbox
# -----------------------------
INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
OUTPUT_PATH = os.getenv("DROPBOX_OUTPUT_PATH")
PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")

# Локальные папки
LOCAL_DATA = "data"
LOCAL_REPORTS = "reports"
os.makedirs(LOCAL_DATA, exist_ok=True)
os.makedirs(LOCAL_REPORTS, exist_ok=True)

# Интервал проверки новых файлов
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))


def process_file(fname: str):
    dropbox_file_path = f"{INPUT_PATH}/{fname}"
    local_file_path = os.path.join(LOCAL_DATA, fname)

    logger.info(f"Начинаем обработку файла: {fname}")

    if not download_file(dropbox_file_path, local_file_path):
        logger.error(f"Не удалось скачать {fname}")
        return

    res = get_analyzer(fname)
    if res is None:
        msg = f"❌ Не найден анализатор для файла {fname}"
        logger.warning(msg)
        send_message_sync(msg)
        return

    analyzer_func, config = res

    try:
        # Передаём путь к файлу и конфигурацию колонок
        result = analyzer_func(local_file_path, config.get("columns"))
        if "error" in result:
            raise ValueError(result["error"])

        logger.info(f"Анализ завершён для {fname}")

        report_local_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")
        build_report(result, report_local_path)
        logger.info(f"Отчёт сохранён локально: {report_local_path}")

        send_message_sync(f"✅ Отчёт по файлу {fname} готов")
        send_file_sync(report_local_path)
        logger.info(f"Отчёт отправлен в Telegram")

        if upload_file(report_local_path, f"{OUTPUT_PATH}/report_{fname}.xlsx"):
            logger.info(f"Отчёт загружен в Dropbox: {OUTPUT_PATH}/report_{fname}.xlsx")

        if move_file(dropbox_file_path, f"{PROCESSED_PATH}/{fname}"):
            logger.info(f"Файл {fname} перемещён в {PROCESSED_PATH}")

    except Exception as e:
        logger.exception(f"Ошибка при обработке {fname}: {e}")
        send_message_sync(f"❌ Ошибка при обработке {fname}: {e}")


def in_sleep_time(now: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    """
    Проверяет, находится ли текущее время в интервале сна.
    Поддерживает диапазоны через полночь (например, 23:00–07:00).
    """
    if start < end:  # обычный интервал (01:00–07:00)
        return start <= now <= end
    else:  # интервал через полночь (например, 23:00–07:00)
        return now >= start or now <= end


def main_loop():
    logger.info("Запуск автоматического пайплайна...")
    processed_files = set()

    while True:
        now = datetime.datetime.now().time()

        # Проверяем ночное время
        if in_sleep_time(now, sleep_start, sleep_end):
            today = datetime.date.today()
            tomorrow = today + datetime.timedelta(days=1)

            # вычисляем момент "просыпания"
            if sleep_start < sleep_end:
                wake_time = datetime.datetime.combine(today, sleep_end)
                if now > sleep_end:
                    wake_time = datetime.datetime.combine(tomorrow, sleep_end)
            else:
                # диапазон через полночь: например 23:00–07:00
                if now >= sleep_start:  # сегодня ещё спим
                    wake_time = datetime.datetime.combine(tomorrow, sleep_end)
                else:  # уже после полуночи
                    wake_time = datetime.datetime.combine(today, sleep_end)

            sleep_seconds = int((wake_time - datetime.datetime.now()).total_seconds())
            logger.info(f"Ночной режим: пауза до {wake_time.strftime('%Y-%m-%d %H:%M:%S')}")
            time.sleep(max(sleep_seconds, 1))
            continue

        try:
            files = list_files(INPUT_PATH)
            new_files = [f for f in files if f not in processed_files]

            if not new_files:
                logger.info("Нет новых файлов для обработки")
            else:
                for fname in new_files:
                    process_file(fname)
                    processed_files.add(fname)

        except Exception as e:
            logger.exception(f"Ошибка при сканировании Dropbox: {e}")
            send_message_sync(f"❌ Ошибка при сканировании Dropbox: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main_loop()
