# integrations/scheduler.py
import time
import threading
from datetime import datetime, timedelta
import pytz

from utils.logger import logger
from integrations.downloader import run_download
from integrations.downloader_wallets import run_wallet_cycle
from integrations.rate_monitor_playwright import check_bakai_rate

# Московский TZ
MSK = pytz.timezone("Europe/Moscow")


def now_msk():
    """Текущее время по Москве."""
    return datetime.now(MSK)


# ------------ ВСПОМОГАЮЩИЕ ФУНКЦИИ -----------------

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


# ------------ Rate Monitor (курс RUB) -----------------

def run_rate_monitor():
    """
    Проверяет курс каждые 5 минут в окне 08:55–10:30.
    Логику отправки сообщений контролирует сам модуль rate_monitor_playwright.py.
    Здесь только расписание.
    """
    logger.info("🟢 Старт планировщика RateMonitor: каждые 5 мин, окно 08:55–10:30 (MSK)")

    while True:
        now = now_msk()
        hour = now.hour
        minute = now.minute

        # ВНЕ окна
        in_window = (
            (hour == 8 and minute >= 55) or
            (9 <= hour < 11) or
            (hour == 10 and minute == 30)
        )

        if not in_window:
            logger.info("⏸ RateMonitor: вне окна, ждём 08:55 (MSK)")
            time.sleep(60)
            continue

        # Внутри окна запускаем проверку
        try:
            logger.info(f"🚀 Запуск RateMonitor (MSK {now.strftime('%H:%M:%S')})")
            check_bakai_rate()
            logger.info("✅ RateMonitor завершён")
        except Exception as e:
            logger.exception(f"❌ Ошибка в RateMonitor: {e}")

        logger.info("😴 RateMonitor: пауза 5 мин")
        time.sleep(5 * 60)


# ------------ ЗАПУСК ПОТОКОВ -----------------

def main():
    # MainDownloader (40 мин, 08–24)
    t1 = threading.Thread(
        target=run_every,
        args=(40, 8, 24, run_download, "MainDownloader"),
        daemon=True
    )
    t1.start()

    # WalletDownloader (5 мин, 08–24)
    t2 = threading.Thread(
        target=run_every,
        args=(5, 8, 24, run_wallet_cycle, "WalletDownloader"),
        daemon=True
    )
    t2.start()

    # RateMonitor (курс RUB) — каждые 5 мин, 08:55–10:30
    t3 = threading.Thread(
        target=run_rate_monitor,
        daemon=True
    )
    t3.start()

    # Основной поток просто живёт
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
