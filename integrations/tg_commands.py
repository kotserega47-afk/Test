# integrations/tg_commands.py
import os
import asyncio
import traceback
from datetime import datetime
from typing import Callable, Optional

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from core.access_rules import AccessRules
from core.access_guard import AccessContext, check_access, deny_message

from integrations.downloader_wallets import run_wallet_cycle
from integrations.bakai_monitor_playwright import run_rate_monitor_safe


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("MAIN")

RULES = AccessRules(os.getenv("RULES_XLSX_PATH", "").strip())

_running_lock = asyncio.Lock()
_current_job: Optional[str] = None
_started_at: Optional[datetime] = None


def _ctx(update: Update) -> AccessContext:
    chat = update.effective_chat
    user = update.effective_user
    return AccessContext(chat_type=chat.type, chat_id=int(chat.id), user_id=int(user.id))


async def _guard_or_deny(update: Update, command: str) -> bool:
    ctx = _ctx(update)
    ok, reason, details = check_access(RULES, ctx, command)
    if not ok:
        await update.message.reply_text(deny_message(reason, details))
        return False
    return True


def _help_text() -> str:
    return (
        "Команды:\n"
        "/status\n"
        "/whoami\n"
        "/reload_rules\n"
        "/run_wallet\n"
        "/run_rate\n"
        "/help"
    )


async def _run_job(update: Update, job_key: str, job: Callable[[], None]):
    global _current_job, _started_at

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
            await loop.run_in_executor(None, job)
            log.info(f"✅ TG job done: {job_key}")
            await update.message.reply_text(f"✅ Готово: {job_key}")

        except Exception:
            err = traceback.format_exc()
            log.exception(f"❌ TG job error: {job_key}")
            await update.message.reply_text("❌ Ошибка при выполнении. Хвост трейса:")
            await update.message.reply_text(err[-3500:])

        finally:
            _current_job = None
            _started_at = None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "start"):
        return
    await update.message.reply_text("Ок. Я готов.\n\n" + _help_text())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "help"):
        return
    await update.message.reply_text(_help_text())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "status"):
        return

    if _current_job is None:
        await update.message.reply_text("🟢 Сейчас ничего не выполняется.")
        return

    started = _started_at.strftime("%Y-%m-%d %H:%M:%S") if _started_at else "?"
    await update.message.reply_text(f"🟡 Выполняется: {_current_job}\n⏱ Старт: {started}")


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "whoami"):
        return

    chat = update.effective_chat
    user = update.effective_user

    # уровень из rules (если записи нет — будет 0)
    try:
        snap = RULES.get_snapshot()
        chat_key = "private" if chat.type == "private" else int(chat.id)
        level = snap.access_map.get((chat_key, int(user.id)), 0)
    except Exception:
        level = 0

    await update.message.reply_text(
        "🧾 whoami\n"
        f"chat_type: {chat.type}\n"
        f"chat_id: {chat.id}\n"
        f"user_id: {user.id}\n"
        f"username: {user.username or '-'}\n"
        f"level: {level}"
    )


async def cmd_reload_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "reload_rules"):
        return

    RULES.invalidate()
    try:
        snap = RULES.get_snapshot(force_sync=True)
        await update.message.reply_text(f"♻️ rules.xlsx перечитан.\nsource: {snap.source}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Не смог перечитать rules.xlsx: {e}")


async def cmd_run_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "run_wallet"):
        return
    await _run_job(update, "wallet", run_wallet_cycle)


async def cmd_run_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "run_rate"):
        return
    await _run_job(update, "rate", run_rate_monitor_safe)


def get_handlers():
    return [
        CommandHandler("start", cmd_start),
        CommandHandler("help", cmd_help),
        CommandHandler("status", cmd_status),
        CommandHandler("whoami", cmd_whoami),
        CommandHandler("reload_rules", cmd_reload_rules),
        CommandHandler("run_wallet", cmd_run_wallet),
        CommandHandler("run_rate", cmd_run_rate),
    ]
