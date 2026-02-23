# scheduler.py
import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import Application

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from core.schedules import load_schedules
from core.job_runner import request_job, Actor
from integrations.tg_commands import get_handlers, RULES

MSK = ZoneInfo("Europe/Moscow")


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("MAIN")
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()


def _parse_cron_min_hour(expr: str):
    """
    Поддержка минимальная: "M H * * *" где M=0..59, H=0..23 или '*'
    Для твоего кейса "0 * * * *" хватает.
    """
    parts = (expr or "").strip().split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 parts")
    m_s, h_s, _, _, _ = parts

    if m_s == "*":
        raise ValueError("minute '*' not supported (use explicit minute)")
    minute = int(m_s)

    hour_any = (h_s == "*")
    hour = None if hour_any else int(h_s)

    if not (0 <= minute <= 59):
        raise ValueError("cron minute out of range")
    if hour is not None and not (0 <= hour <= 23):
        raise ValueError("cron hour out of range")

    return minute, hour  # hour=None means '*'


def _next_cron_run(now: datetime, cron_expr: str) -> datetime:
    minute, hour = _parse_cron_min_hour(cron_expr)

    if hour is None:
        # every hour at :minute
        target = now.replace(minute=minute, second=5, microsecond=0)
        if target <= now:
            target = target + timedelta(hours=1)
        return target

    # every day at hour:minute
    target = now.replace(hour=hour, minute=minute, second=5, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target


def schedule_loop():
    """
    Каждые ~5-10 сек:
      - читаем schedules из rules.xlsx
      - вычисляем "пора ли"
      - триггерим request_job(job_type, actor="scheduler")
    """
    next_every = {}  # job_type -> ts
    next_cron = {}   # job_type -> datetime

    log.info("🕒 schedule loop started (rules.xlsx:schedules)")

    while True:
        try:
            schedules = load_schedules(force_sync=False)
            active = {s.job_type for s in schedules}
            for k in list(next_every.keys()):
                if k not in active: next_every.pop(k, None)
            for k in list(next_cron.keys()):
                if k not in active: next_cron.pop(k, None)
        except Exception as e:
            log.warning(f"⚠️ schedules load failed: {e}")
            time.sleep(10)
            continue

        now = datetime.now(MSK)

        for s in schedules:
            jt = s.job_type

            # every_seconds
            if s.schedule_type == "every_seconds":
                ts_now = time.time()
                ts_next = next_every.get(jt)
                if ts_next is None:
                    next_every[jt] = ts_now + s.every_seconds
                    continue

                if ts_now >= ts_next:
                    actor = Actor(kind="scheduler")
                    try:
                        request_job(jt, actor)
                    except Exception as e:
                        log.exception(f"❌ scheduled job failed: {jt}: {e}")
                    next_every[jt] = ts_now + s.every_seconds

            # cron
            elif s.schedule_type == "cron":
                dt_next = next_cron.get(jt)
                if dt_next is None:
                    try:
                        next_cron[jt] = _next_cron_run(now, s.cron)
                    except Exception as e:
                        log.warning(f"⚠️ bad cron for {jt}: {s.cron} err={e}")
                    continue

                if now >= dt_next:
                    actor = Actor(kind="scheduler")
                    try:
                        request_job(jt, actor)
                    except Exception as e:
                        log.exception(f"❌ scheduled job failed: {jt}: {e}")
                    try:
                        next_cron[jt] = _next_cron_run(datetime.now(MSK), s.cron)
                    except Exception:
                        next_cron.pop(jt, None)

        time.sleep(5)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан TG_BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()

    for h in get_handlers():
        app.add_handler(h)

    # прогреваем rules (fail-fast)
    RULES.get_snapshot(force_sync=True)

    threading.Thread(target=schedule_loop, daemon=True).start()

    log.info("🟢 Telegram started (polling + schedules)")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()