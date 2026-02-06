# scheduler.py
import os
import asyncio
import traceback
from datetime import datetime
from typing import Callable, Dict, Optional

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

# === Импорты задач (одиночные запуски) ===
from integrations.downloader import run_download                 # ~40 минут
from integrations.hourly_downloader import run_hourly_cycle      # один прогон
from analyzers.hourly_report import run_hourly_report            # один прогон
from integrations.downloader_wallets import run_wallet_cycle     # один прогон
from integrations.bakai_monitor_playwright import run_rate_monitor_safe  # один прогон


# === Telegram (polling) ===
# pip install python-telegram-bot==21.6
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("MAIN")

BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
ALLOWED_USERS = {
    int(x) for x in os.getenv("TG_ALLOWED_USERS", "").split(",")
    if x.strip().isdigit()
}

# Лок, чтобы не запускать 2 задачи одновременно
_running_lock = asyncio.Lock()
_current_job: Optional[str] = None
_started_at: Optional[datetime] = None


# === JOBS: одна команда -> одна функция -> один запуск ===
def job_conversion():
    run_download()


def job_wallet():
    run_wallet_cycle()


def job_rate():
    run_rate_monitor_safe()


def job_hourly():
    run_hourly_cycle()
    run_hourly_report()


JOBS: Dict[str, Callable[[], None]] = {
    "conversion": job_conversion,
    "wallet": job_wallet,
    "rate": job_rate,
    "hourly": job_hourly,
}


def _is_allowed(user_id: int) -> bool:
    # Если список разрешённых пуст — разрешаем всем (не рекомендую)
    return (not ALLOWED_USERS) or (user_id in ALLOWED_USERS)


def _help_text() -> str:
    jobs_list = ", ".join(JOBS.keys())
    return (
        "Команды:\n"
        "/run <job>\n"
        "/status\n"
        "/help\n\n"
        f"Доступные job: {jobs_list}\n"
        "Пример: /run wallet"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    await update.message.reply_text("Ок. Я готов.\n\n" + _help_text())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(_help_text())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    if _current_job is None:
        await update.message.reply_text("🟢 Сейчас ничего не выполняется.")
        return

    started = _started_at.strftime("%Y-%m-%d %H:%M:%S") if _started_at else "?"
    await update.message.reply_text(
        f"🟡 Выполняется: {_current_job}\n"
        f"⏱ Старт: {started}"
    )


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _current_job, _started_at

    if not _is_allowed(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Нужно указать job. Пример: /run wallet")
        return

    job_key = context.args[0].strip().lower()
    job = JOBS.get(job_key)
    if not job:
        await update.message.reply_text(f"Не знаю job='{job_key}'.\n\n" + _help_text())
        return

    if _running_lock.locked():
        await update.message.reply_text("⛔ Уже выполняется другая задача. /status")
        return

    async with _running_lock:
        _current_job = job_key
        _started_at = datetime.now()

        log.info(f"🚀 TG run: {job_key}")
        await update.message.reply_text(f"🚀 Запускаю: {job_key}")

        loop = asyncio.get_running_loop()
        try:
            # Запускаем синхронную задачу в executor, чтобы polling не зависал.
            await loop.run_in_executor(None, job)

            log.info(f"✅ TG job done: {job_key}")
            await update.message.reply_text(f"✅ Готово: {job_key}")

        except Exception:
            err = traceback.format_exc()
            log.exception(f"❌ TG job error: {job_key}")

            # Не отправляем гигантский трейс целиком — только хвост.
            tail = err[-3500:]
            await update.message.reply_text("❌ Ошибка при выполнении. Хвост трейса:")
            await update.message.reply_text(tail)

        finally:
            _current_job = None
            _started_at = None


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан TG_BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))

    log.info("🟢 Telegram scheduler (manual-only) started (polling)")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
