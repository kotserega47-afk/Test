#  scheduler.py
import os

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from telegram.ext import Application

from integrations.tg_commands import get_handlers, RULES


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("MAIN")
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан TG_BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()

    for h in get_handlers():
        app.add_handler(h)

    RULES.get_snapshot(force_sync=True)

    log.info("🟢 Telegram scheduler (manual-only) started (polling)")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
