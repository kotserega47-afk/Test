import time
import threading
from datetime import datetime, timedelta
import pytz

from utils.logger import logger
from integrations.downloader import run_download
from integrations.downloader_wallets import run_wallet_cycle


# Московский TZ
MSK = pytz.timezone("Europe/Moscow")


def now_msk():
    """Текущее время по Москве."""
    return datetime.now(MSK)


# ------------ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------

def wait_until(hour: int) -> None:
    """Спит до указанного часа по Москве."""
    while True:
        now = now_msk()
        if now.hour >= hour:
            return
        time.sleep(60)


def run_every(interval_min: int, start_hour: int, end_hour: int, func, name: str):
    """Бесконечный цикл: выполнять func каждые interval_min минут в рабочем окне."""
    logger.info(
        f"🟢 Старт планировщика {name}: интервал {interval_min} мин, окно {start_hour}:00–{end_hour}:00 (MSK)"
    )

    while True:
        now = now_msk()

        # ВНЕ рабочего окна — спим до старта
        if not (start_hour <= now.hour <= end_hour):
            logger.info(f"⏸ {name}: вне окна, ждём {start_hour}:00 (MSK)")
            wait_until(start_hour)
            continue

        # В рабочем окне — запускаем задачу
        try:
            logger.info(f"🚀 Запуск {name} (MSK {now.strftime('%H:%M:%S')})")
            func()
            logger.info(f"✅ {name} завершён")
        except Exception as e:
            logger.exception(f"❌ Ошибка в {name}: {e}")

        # Спим ровно interval_min минут
        sleep_seconds = interval_min * 60
        logger.info(f"😴 {name}: пауза {interval_min} мин")
        time.sleep(sleep_seconds)


# ------------ ЗАПУСК ПОТОКОВ -----------------

def main():
    # Основной downloader (30 мин, 08–24)
    t1 = threading.Thread(
        target=run_every,
        args=(30, 8, 24, run_download, "MainDownloader"),
        daemon=True
    )
    t1.start()

    # Wallet Downloader (5 мин, 08–24)
    t2 = threading.Thread(
        target=run_every,
        args=(5, 8, 24, run_wallet_cycle, "WalletDownloader"),
        daemon=True
    )
    t2.start()

    # Основной поток просто живёт
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
