#  scheduler.py
import os
import threading
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
import threading
import time
from datetime import datetime
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

def _sleep(seconds: float):
    time.sleep(seconds)

def run_every_minutes(fn, minutes: int, name: str):
    log.info(f"⏱️ schedule: {name} every {minutes} min")
    while True:
        try:
            fn()
        except Exception as e:
            log.exception(f"❌ scheduled {name} failed: {e}")
        _sleep(minutes * 60)

def run_hourly_at_minute(fn, minute: int, name: str):
    log.info(f"🕒 schedule: {name} every hour at :{minute:02d}")
    while True:
        now = datetime.now(MSK)
        # следующий запуск: текущий час + minute
        target = now.replace(minute=minute, second=5, microsecond=0)
        if target <= now:
            # следующий час
            target = target.replace(hour=(now.hour + 1) % 24)
            if target.date() != now.date() and now.hour == 23:
                target = target  # datetime сам дату не сменит при hour=0, поэтому проще:
                # безопаснее пересчитать:
                target = (now.replace(minute=minute, second=5, microsecond=0) + timedelta(hours=1))

        sleep_sec = (target - now).total_seconds()
        _sleep(max(1, sleep_sec))

        try:
            fn()
        except Exception as e:
            log.exception(f"❌ scheduled {name} failed: {e}")

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

    # run_hourly: скачали hourly payin + отправили hourly report каждый час
    # минуту выбираешь env-ом, по умолчанию :02
    hourly_minute = int(os.getenv("RACCOON_HOURLY_MINUTE", "2"))

    def _hourly_job():
        run_hourly_raccoon_cycle()
        run_hourly_report()

    threading.Thread(
        target=run_hourly_at_minute,
        args=(_hourly_job, hourly_minute, "run_hourly_raccoon"),
        daemon=True
    ).start()

    log.info("🟢 Telegram scheduler (manual-only) started (polling)")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
