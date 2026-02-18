#  scheduler.py
import os
import threading
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import Application

from integrations.tg_commands import get_handlers, RULES
from integrations.raccoon_wallet_downloader import run_raccoon_wallet_cycle
from integrations.raccoon_hourly_downloader import run_hourly_raccoon_cycle
from analyzers.raccoon_hourly_report import run_hourly_report

def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)

MSK = ZoneInfo("Europe/Moscow")

import sys

# --- Restart control ---
RESTART_HOURS = [10, 13, 16, 19, 22, 1]  # MSK
RESTART_GRACE_MIN = int(os.getenv("RESTART_GRACE_MIN", "10"))

_active_jobs = 0
_active_lock = threading.Lock()


def job_start():
    global _active_jobs
    with _active_lock:
        _active_jobs += 1


def job_end():
    global _active_jobs
    with _active_lock:
        _active_jobs -= 1


def active_jobs():
    with _active_lock:
        return _active_jobs


def next_restart_time():
    now = datetime.now(MSK)
    today = now.date()

    candidates = []
    for h in RESTART_HOURS:
        target = datetime(today.year, today.month, today.day, h, 0, tzinfo=MSK)
        if target <= now:
            target += timedelta(days=1)
        candidates.append(target)

    return min(candidates)


def restart_worker():
    log.info(f"🔁 Restart scheduler active. Hours={RESTART_HOURS} (MSK)")

    while True:
        target = next_restart_time()
        sleep_sec = (target - datetime.now(MSK)).total_seconds()

        log.info(f"⏳ Next restart at {target.strftime('%d.%m %H:%M:%S')} MSK")
        time.sleep(max(1, sleep_sec))

        log.info("♻️ Restart window reached")

        # ждём завершения задач
        waited = 0
        while active_jobs() > 0 and waited < RESTART_GRACE_MIN * 60:
            log.info(f"⏸ Waiting for jobs to finish... active={active_jobs()}")
            time.sleep(5)
            waited += 5

        log.warning("🛑 Restarting process now")
        os._exit(1)  # Railway перезапустит контейнер


def _sleep(seconds: float):
    time.sleep(seconds)

def run_every_minutes(fn, minutes: int, name: str):
    log.info(f"⏱️ schedule: {name} every {minutes} min")
    while True:
        try:
            job_start()
            fn()
        except Exception as e:
            log.exception(f"❌ scheduled {name} failed: {e}")
        finally:
            job_end()

        _sleep(minutes * 60)

def run_hourly_at_minute(fn, minute: int, name: str):
    log.info(f"🕒 schedule: {name} every hour at :{minute:02d}")
    while True:
        now = datetime.now(MSK)
        target = now.replace(minute=minute, second=5, microsecond=0)
        if target <= now:
            target = target + timedelta(hours=1)

        _sleep(max(1, (target - now).total_seconds()))

        try:
            job_start()
            fn()
        except Exception as e:
            log.exception(f"❌ scheduled {name} failed: {e}")
        finally:
            job_end()

log = _mk("MAIN")
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан TG_BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()

    for h in get_handlers():
        app.add_handler(h)

    RULES.get_snapshot(force_sync=True)

    # run_raccoon: каждые N минут
    raccoon_every_min = int(os.getenv("RACCOON_EVERY_MIN", "5"))
    threading.Thread(
        target=run_every_minutes,
        args=(run_raccoon_wallet_cycle, raccoon_every_min, "run_raccoon_wallet"),
        daemon=True
    ).start()

    def _hourly_job():
        run_hourly_raccoon_cycle()
        run_hourly_report()

    # run_hourly_raccoon: каждые N минут (замена hourly)
    raccoon_hourly_every_min = int(os.getenv("RACCOON_HOURLY_EVERY_MIN", "5"))
    threading.Thread(
        target=run_every_minutes,
        args=(_hourly_job, raccoon_hourly_every_min, "run_hourly_raccoon"),
        daemon=True
    ).start()

    # --- restart scheduler ---
    threading.Thread(
        target=restart_worker,
        daemon=True
    ).start()

    log.info("🟢 Telegram scheduler started (polling + schedules)")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
