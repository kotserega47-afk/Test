import time
import threading
from datetime import datetime, timedelta
from utils.logger import logger

from integrations.downloader import run_download
from integrations.downloader_wallets import run_wallet_cycle


# ------------ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------

def wait_until(hour: int) -> None:
    """Спит до указанного часа по Москве."""
    while True:
        now = datetime.now()
        if now.hour >= hour:
            return
        # Спим до следующей минуты
        time.sleep(60)


def run_every(interval_min: int, start_hour: int, end_hour: int, func, name: str):
    """Бесконечный цикл: выполнять func каждые interval_min минут в рабочем окне."""
    logger.info(f"🟢 Старт планировщика {name}: интервал {interval_min} мин, окно {start_hour}:00–{end_hour}:00")

    while True:
        now = datetime.now()

        # ВНЕ рабочего окна — спим до старта
        if not (start_hour <= now.hour <= end_hour):
            target = start_hour if now.hour < start_hour else 24 + start_hour
            logger.info(f"⏸ {name}: вне окна, сплю до {start_hour}:00")
            wait_until(start_hour)
            continue

        # Внутри рабочего окна — выполняем задачу
        try:
            logger.info(f"🚀 Запуск {name} ({now.strftime('%H:%M:%S')})")
            func()
            logger.info(f"✅ {name} завершён")
        except Exception as e:
            logger.exception(f"❌ Ошибка в {name}: {e}")

        # Спим ровно interval_min минут
        sleep_seconds = interval_min * 60
        logger.info(f"😴 {name}: сплю {sleep_seconds} сек ({interval_min} минут)")
        time.sleep(sleep_seconds)


# ------------ ЗАПУСК ДВУХ НЕЗАВИСИМЫХ ПОТОКОВ -----------------

def main():
    # Поток для основного downloader (каждые 30 минут, 08–00)
    t1 = threading.Thread(
        target=run_every,
        args=(30, 8, 24, run_download, "MainDownloader"),
        daemon=True
    )
    t1.start()

    # Поток для Wallet Downloader (каждые 5 минут, 08–00)
    t2 = threading.Thread(
        target=run_every,
        args=(5, 8, 24, run_wallet_cycle, "WalletDownloader"),
        daemon=True
    )
    t2.start()

    # Основной поток просто спит
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
