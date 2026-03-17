# integrations/tg_commands.py
from __future__ import annotations
import time
import asyncio
import os
import traceback
from datetime import datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from core.access_rules import AccessRules
from core.access_guard import AccessContext, check_access, deny_message
from core.job_runner import request_job, get_status, Actor, JOB_REGISTRY

from integrations.downloader_wallets import run_wallet_cycle
from integrations.bakai_monitor_playwright import run_rate_monitor_safe
from integrations.downloader import run_download

from analyzers.hourly_report import run_hourly_report
from transport.telegram_transport import send_text
from core.state_store import state_update
from core.config_manager import rules_validate_all
from core.rules_provider import get_snapshot_v2


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("MAIN")

RULES = AccessRules(os.getenv("RULES_XLSX_PATH", "").strip())


# =============================================================================
# Jobs (wrappers)
# =============================================================================

def run_hourly_job() -> None:
    """
    Contract:
      - run_hourly_report() does: download + fp compare + DTO + render (NO TG, NO fp commit)
      - wrapper does: send + fp commit ONLY after successful send
      - skip/no-changes -> only event_log (handled inside hourly_report)
    """
    res = run_hourly_report(job="hourly")
    if res.skipped_no_changes or not res.text:
        return

    chat_id = os.getenv("TELEGRAM_CHAT_ID_HOURLY", "").strip()
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID_HOURLY is not set")

    send_text(text=res.text, chat_id=chat_id)

    if res.fingerprint:
        state_update("hourly", {
            "last_fingerprint": res.fingerprint,
            "last_sent_ts": int(time.time())
        })

def run_download_job() -> None:
    run_download()

# Единственная точка привязки job_type -> runnable
JOB_REGISTRY.update(
    {
        "wallet": run_wallet_cycle,
        "hourly": run_hourly_job,
        "rate": run_rate_monitor_safe,
        "download": run_download_job,
    }
)

# =============================================================================
# Access helpers
# =============================================================================

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
        "/run_download\n"
        "/run_rate\n"
        "/rules_validate\n"
        "/help"
    )


async def _run_job_async(update: Update, job_type: str) -> None:
    actor = Actor(kind="tg", chat_id=int(update.effective_chat.id), user_id=int(update.effective_user.id))
    await update.message.reply_text(f"🚀 Запускаю: {job_type}")

    loop = asyncio.get_running_loop()
    try:
        job_id = await loop.run_in_executor(None, lambda: request_job(job_type, actor))
        await update.message.reply_text(f"✅ Принято: {job_type}\njob_id={job_id}")
    except Exception:
        err = traceback.format_exc()
        log.exception("❌ TG job error: %s", job_type)
        await update.message.reply_text("❌ Ошибка при выполнении.\nХвост трейса:")
        await update.message.reply_text(err[-3500:])


# =============================================================================
# Commands
# =============================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "start"):
        return
    await update.message.reply_text("Ок.\nЯ готов.\n\n" + _help_text())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "help"):
        return
    await update.message.reply_text(_help_text())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "status"):
        return

    st = get_status()
    if not st:
        await update.message.reply_text("🟢 Сейчас ничего не выполняется.")
        return

    lines = ["🟠 Сейчас выполняется:"]
    for jt, info in st.items():
        started = datetime.fromtimestamp(info["started_ts"]).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"- {jt}: job_id={info['job_id']} runtime={info['runtime_sec']}s старт={started}")

    await update.message.reply_text("\n".join(lines))


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        "👤 whoami\n"
        f"chat_type: {chat.type}\n"
        f"chat_id: {chat.id}\n"
        f"user_id: {user.id}\n"
        f"username: {user.username or '-'}\n"
        f"level: {level}"
    )

async def cmd_rules_validate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "rules_validate"):
        return

    await update.message.reply_text("🔎 Валидирую rules.xlsx…")

    try:
        snap = get_snapshot_v2(force_sync=...)
        errors, warnings = rules_validate_all(force_sync=False)

        if errors:
            body = "\n".join(f"- {x}" for x in errors[:60])
            tail = "" if len(errors) <= 60 else f"\n… (+{len(errors)-60} more)"
            await update.message.reply_text(
                "❌ rules_validate: FAIL\n"
                f"rules_version: {snap.meta.ruleset_version}\n"
                f"source: snapshot_v2\n\n"
                + body + tail
            )
            return

        if warnings:
            body = "\n".join(f"- {x}" for x in warnings[:60])
            tail = "" if len(warnings) <= 60 else f"\n… (+{len(warnings)-60} more)"
            await update.message.reply_text(
                "⚠️ rules_validate: WARN\n"
                f"rules_version: {snap.meta.ruleset_version}\n"
                f"source: snapshot_v2\n\n"
                + body + tail
            )
            return

        await update.message.reply_text(
            "✅ rules_validate: OK\n"
            f"rules_version: {snap.meta.ruleset_version}\n"
            f"source: snapshot_v2\n\n"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ rules_validate crashed: {e}")

async def cmd_reload_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "reload_rules"):
        return

    RULES.invalidate()
    try:
        snap = RULES.get_snapshot(force_sync=True)
        await update.message.reply_text(
            f"♻️ rules snapshot перечитан.\n"
            f"source: {snap.source}"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Не смог перечитать rules.xlsx: {e}")


async def cmd_run_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "run_wallet"):
        return
    await _run_job_async(update, "wallet")


async def cmd_run_hourly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "run_hourly"):
        return
    await _run_job_async(update, "hourly")

async def cmd_run_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "run_download"):
        return
    await _run_job_async(update, "download")

async def cmd_run_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        CommandHandler("run_download", cmd_run_download),
        CommandHandler("run_rate", cmd_run_rate),
        CommandHandler("rules_validate", cmd_rules_validate),
    ]