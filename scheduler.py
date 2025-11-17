# integrations/scheduler.py
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from utils.logger import logger
from integrations.downloader import run_download
from integrations.downloader_wallets import run_wallet_cycle
from integrations.bakai_monitor_playwright import check_bakai_rate

# === NEW ===
from integrations.hourly_downloader import run_hourly_report
from analyzers.hourly_report import run_hourly_report

# Московский TZ
MSK = ZoneInfo("Europe/Moscow")

def now_msk():
    """Текущее время по Москве."""
    return datetime.now(MSK)


# ------------ ВСПОМОГАЮЩИЕ ФУНКЦИИ -----------------

def wait_until_hour(hour: int) -> None:
    """Спит до указанного часа по Москве."""
    while True:
        now = now_msk()
        if now.hour >= hour:
            return
        time.sleep(30)


def run_every(interval_min: int, start_hour: int, end_hour: int, func, name: str):
    """Бесконечный цикл: выполнять func каждые interval_min минут в рабочем окне."""
    logger.info(
        f"🟢 Старт планировщика {name}: интервал {interval_min} мин, окно {start_hour}:00–{end_hour}:00 (MSK)"
    )

    while True:
        now = now_msk()

        if not (start_hour <= now.hour <= end_hour):
            logger.info(f"⏸ {name}: вне окна, ждём {start_hour}:00 (MSK)")
            wait_until_hour(start_hour)
            continue

        try:
            logger.info(f"🚀 Запуск {name} (MSK {now.strftime('%H:%M:%S')})")
            func()
            logger.info(f"✅ {name} завершён")
        except Exception as e:
            logger.exception(f"❌ Ошибка в {name}: {e}")

        time.sleep(interval_min * 60)


# ------------ Rate Monitor (курс RUB) -----------------

def run_rate_monitor():
    logger.info("🟢 Старт планировщика RateMonitor: каждые 5 мин, окно 08:55–10:30 (MSK)")

    while True:
        now = now_msk()
        hour = now.hour
        minute = now.minute

        in_window = (
            (hour == 8 and minute >= 55) or
            (9 <= hour < 11) or
            (hour == 10 and minute == 30)
        )

        if not in_window:
            logger.info("⏸ RateMonitor: вне окна, ждём 08:55 (MSK)")
            time.sleep(300)
            continue

        try:
            logger.info(f"🚀 Запуск RateMonitor (MSK {now.strftime('%H:%M:%S')})")
            check_bakai_rate()
            logger.info("✅ RateMonitor завершён")
        except Exception as e:
            logger.exception(f"❌ Ошибка в RateMonitor: {e}")

        time.sleep(5 * 60)


# ------------ NEW: Hourly report (09:00–00:00) -----------------

def run_hourly_loop():
    logger.info("🟢 Старт HourlyReporter: каждый час, окно 09:00–00:00 (MSK)")

    while True:
        now = now_msk()

        # окно работы: 09–23 + 00
        in_window = (9 <= now.hour <= 23) or (now.hour == 0)

        if not in_window:
            logger.info("⏸ HourlyReporter: вне окна, ждём 09:00 (MSK)")
            wait_until_hour(9)
            continue

        # запускаем ровно в 00 минут
        if now.minute == 0:
            try:
                logger.info(f"🚀 HourlyDownloader (MSK {now.strftime('%H:%M:%S')})")
                run_hourly_report()

                logger.info(f"🚀 HourlyReport (MSK {now.strftime('%H:%M:%S')})")
                run_hourly_report()

                logger.info("✅ HourlyReporter завершён")
            except Exception as e:
                logger.exception(f"❌ Ошибка в HourlyReporter: {e}")

            # ждём 60 секунд, чтобы не запустить два раза в одну минуту
            time.sleep(60)

        time.sleep(5)


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

    # RateMonitor (курс RUB)
    t3 = threading.Thread(target=run_rate_monitor, daemon=True)
    t3.start()

    # NEW — HourlyDownloader + HourlyReport
    t4 = threading.Thread(target=run_hourly_loop, daemon=True)
    t4.start()

    # основной поток
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
