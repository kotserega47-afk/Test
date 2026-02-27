# integrations/tg_commands.py
import os
import asyncio
import traceback
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from core.access_rules import AccessRules
from core.access_guard import AccessContext, check_access, deny_message

from core.job_runner import request_job, get_status, Actor, JOB_REGISTRY
from core.state_provider import get_job_value, set_job_value
from core.event_log import append_event

from integrations.downloader_wallets import run_wallet_cycle
from integrations.bakai_monitor_playwright import run_rate_monitor_safe
from analyzers.hourly_report import run_hourly_report


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("MAIN")

RULES = AccessRules(os.getenv("RULES_XLSX_PATH", "").strip())


# ---------- hourly wrapper: skip if no changes ----------

def _fp_hourly_files(payin="/tmp/hourly/payin.xlsx", payout="/tmp/hourly/payout.xlsx") -> Optional[str]:
    def f(p):
        pp = Path(p)
        if not pp.exists():
            return None
        st = pp.stat()
        return {"path": p, "size": st.st_size, "mtime": st.st_mtime}

    a = f(payin)
    b = f(payout)
    if not a or not b:
        return None

    raw = json.dumps({"payin": a, "payout": b}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run_hourly_job():
    fp = _fp_hourly_files()
    if not fp:
        append_event(type="job_skipped_missing_inputs", job_type="hourly")
        return

    last = get_job_value("hourly", "last_fingerprint", default=None, force_sync=False)
    if last == fp:
        append_event(type="job_skipped_no_changes", job_type="hourly", payload={"fingerprint": fp[:10]})
        return

    run_hourly_report()

    # фиксируем только после успешного выполнения
    set_job_value("hourly", "last_fingerprint", fp)


# ---------- job registry (единственная точка привязки) ----------

JOB_REGISTRY.update({
    "wallet": run_wallet_cycle,
    "hourly": run_hourly_job,
    "rate": run_rate_monitor_safe,
})


# ---------- access helpers ----------

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
        "/run_hourly\n"
        "/run_rate\n"
        "/help"
    )


async def _run_job_async(update: Update, job_type: str):
    actor = Actor(kind="tg", chat_id=int(update.effective_chat.id), user_id=int(update.effective_user.id))
    await update.message.reply_text(f"🚀 Запускаю: {job_type}")

    loop = asyncio.get_running_loop()
    try:
        job_id = await loop.run_in_executor(None, lambda: request_job(job_type, actor))
        await update.message.reply_text(f"✅ Принято: {job_type}\njob_id={job_id}")
    except Exception:
        err = traceback.format_exc()
        log.exception(f"❌ TG job error: {job_type}")
        await update.message.reply_text("❌ Ошибка при выполнении. Хвост трейса:")
        await update.message.reply_text(err[-3500:])


# ---------- commands ----------

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

    st = get_status()
    if not st:
        await update.message.reply_text("🟢 Сейчас ничего не выполняется.")
        return

    lines = ["🟡 Сейчас выполняется:"]
    for jt, info in st.items():
        started = datetime.fromtimestamp(info["started_ts"]).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"- {jt}: job_id={info['job_id']} runtime={info['runtime_sec']}s старт={started}")
    await update.message.reply_text("\n".join(lines))


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "whoami"):
        return

    chat = update.effective_chat
    user = update.effective_user

    try:
        snap = RULES.get_snapshot()
        chat_key = "private" if chat.type == "private" else int(chat.id)
        level = int(snap.access_map.get((chat_key, int(user.id)), 0))
    except Exception:
        level = 0

    if level < 1:
        await update.message.reply_text("❌ Нет доступа (ты не добавлен в access).")
        return

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
    await _run_job_async(update, "wallet")


async def cmd_run_hourly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "run_hourly"):
        return
    await _run_job_async(update, "hourly")


async def cmd_run_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard_or_deny(update, "run_rate"):
        return
    await _run_job_async(update, "rate")


def get_handlers():
    return [
        CommandHandler("start", cmd_start),
        CommandHandler("help", cmd_help),
        CommandHandler("status", cmd_status),
        CommandHandler("whoami", cmd_whoami),
        CommandHandler("reload_rules", cmd_reload_rules),
        CommandHandler("run_wallet", cmd_run_wallet),
        CommandHandler("run_hourly", cmd_run_hourly),
        CommandHandler("run_rate", cmd_run_rate),
    ]