# scheduler.py

import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

# === Импорты задач ===
from integrations.downloader import run_download                 # 40 минут
from integrations.hourly_downloader import run_hourly_cycle      # каждый час
from analyzers.hourly_report import run_hourly_report            # отчёт после hourly
from integrations.downloader_wallets import run_wallet_cycle     # 5 минут
from integrations.bakai_monitor_playwright import run_rate_monitor_safe

def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)

log_main = _mk("MAIN")
log_wallet = _mk("WALLET")
log_hourly = _mk("HOURLY")
log_rate = _mk("RATE")

MSK = ZoneInfo("Europe/Moscow")


def now_msk():
    return datetime.now(MSK)


# ================= ВСПОМОГАТЕЛЬНЫЕ =================

def wait_until_hour(hour: int):
    while True:
        if now_msk().hour >= hour:
            return
        time.sleep(30)


def run_every(interval_min: int, start_hour: int, end_hour: int, func, name: str, log):
    log.info(
        f"🟢 Старт планировщика {name}: интервал {interval_min} мин, окно {start_hour}:00–{end_hour}:00 (MSK)"
    )

    while True:
        now = now_msk()

        if not (start_hour <= now.hour <= end_hour):
            log.info(f"⏸ {name}: вне окна, ждём {start_hour}:00 (MSK)")
            wait_until_hour(start_hour)
            continue

        try:
            log.info(f"🚀 Запуск {name} (MSK {now.strftime('%H:%M:%S')})")
            func()
            log.info(f"✅ {name} завершён")
        except Exception as e:
            log.exception(f"❌ Ошибка в {name}: {e}")

        time.sleep(interval_min * 60)


# ================= Rate Monitor =================

def run_rate_monitor():
    log_rate.info("🟢 Старт RateMonitor: каждые 10 мин, окно 06:00–23:55 (MSK)")

    while True:
        now = now_msk()
        h, m = now.hour, now.minute

        in_window = (h > 6 or (h == 6 and m >= 0)) and (h < 23 or (h == 23 and m <= 55))

        if not in_window:
            # логируем только раз в 10 минут, чтобы не шуметь
            if now.minute % 10 == 0:
                log_rate.info("⏸ RateMonitor: вне окна 06:00–23:55 (MSK)")
            time.sleep(60)
            continue

        try:
            log_rate.info(f"🚀 RateMonitor (MSK {now.strftime('%H:%M:%S')})")
            run_rate_monitor_safe()
            log_rate.info("✅ RateMonitor завершён")
        except Exception as e:
            log_rate.exception(f"❌ Ошибка в RateMonitor: {e}")

        time.sleep(10 * 60)


# ================= HOURLY (HourlyDownloader → HourlyReport) =================

def run_hourly_loop():
    log_hourly.info("🟢 Старт HourlyReporter: каждый час, окно 09:00–00:00 (MSK)")

    # Стартуем с текущего часа, чтобы не стрелять сразу при старте посреди часа
    last_run_hour = now_msk().hour

    while True:
        now = now_msk()
        hour = now.hour

        in_window = (9 <= hour <= 23) or (hour == 0)

        if not in_window:
            log_hourly.info("⏸ HourlyReporter: вне окна, ждём 09:00 (MSK)")
            wait_until_hour(9)
            # После выхода из wait_until_hour снова проверим in_window и hour != last_run_hour
            continue

        # Запускаем при смене часа (edge: при старте посреди часа не стреляем,
        # первый запуск будет при переходе на следующий час).
        if hour != last_run_hour:
            last_run_hour = hour

            try:
                log_hourly.info(f"🚀 HourlyDownloader (MSK {now.strftime('%H:%M:%S')})")
                run_hourly_cycle()

                log_hourly.info(f"🚀 HourlyReport (MSK {now.strftime('%H:%M:%S')})")
                run_hourly_report()

                log_hourly.info("✅ HourlyReporter завершён")
            except Exception as e:
                log_hourly.exception(f"❌ Ошибка в HourlyReporter: {e}")

            # Небольшая пауза, чтобы в первый момент часа не отстрелиться несколько раз
            time.sleep(60)

        time.sleep(5)


# ================= START THREADS =================

def main():
    from datetime import datetime
    print(">>> SYSTEM LOCAL:", datetime.now())
    print(">>> NOW_MSK:", now_msk())

    # MainDownloader — каждые 40 минут
    threading.Thread(
        target=run_every,
        args=(40, 8, 24, run_download, "MainDownloader", log_main),
        daemon=True
    ).start()

    # WalletDownloader — каждые 3 минут
    threading.Thread(
        target=run_every,
        args=(3, 8, 24, run_wallet_cycle, "WalletDownloader", log_wallet),
        daemon=True
    ).start()

    # RateMonitor — как было
    threading.Thread(target=run_rate_monitor, daemon=True).start()

    # HourlyDownloader + HourlyReport — каждый час
    threading.Thread(target=run_hourly_loop, daemon=True).start()

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
