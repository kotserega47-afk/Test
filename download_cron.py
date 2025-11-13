# download_cron.py
import time
from datetime import datetime
from utils.logger import logger
from integrations.downloader import run_download


def _sleep_five_minutes(start_time: datetime):
    """Ждёт до следующего 5-минутного интервала, учитывая время выполнения"""
    elapsed = (datetime.now() - start_time).total_seconds()
    sleep_time = max(0, 1800 - elapsed)  # 300 секунд = 5 минут
    logger.info(f"⏸ Сплю {int(sleep_time)} сек до следующего запуска")
    time.sleep(sleep_time)


def main():
    """Бесконечно запускает загрузку каждые 5 минут"""
    while True:
        start_time = datetime.now()
        logger.info(f"🚀 Запуск задачи загрузки: {start_time.strftime('%H:%M:%S')}")

        try:
            run_download()  # основной код загрузки
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке: {e}")

        _sleep_five_minutes(start_time)


if __name__ == "__main__":
    main()
