"""Shared Telegram ACL/job dispatch helpers. No project command names."""

from __future__ import annotations

import traceback

from telegram import Update

from core.access_guard import AccessContext, check_access, deny_message
from core.job_dispatch import dispatch_job_async
from core.job_runner import Actor


def build_access_context(update: Update) -> AccessContext:
    chat = update.effective_chat
    user = update.effective_user
    return AccessContext(chat_type=chat.type, chat_id=int(chat.id), user_id=int(user.id))


async def guard_or_deny(update: Update, command: str, rules: object) -> bool:
    ctx = build_access_context(update)
    ok, reason, details = check_access(rules, ctx, command)
    if not ok:
        await update.message.reply_text(deny_message(reason, details))
        return False
    return True


async def run_job_async(update: Update, job_type: str, logger: object) -> None:
    actor = Actor(
        kind="tg",
        chat_id=int(update.effective_chat.id),
        user_id=int(update.effective_user.id),
    )
    await update.message.reply_text(f"🚀 Запускаю: {job_type}")

    try:
        job_id = await dispatch_job_async(job_type, actor)
        await update.message.reply_text(f"✅ Принято: {job_type}\njob_id={job_id}")
    except Exception:
        err = traceback.format_exc()
        logger.exception("❌ TG job error: %s", job_type)
        await update.message.reply_text("❌ Ошибка при выполнении.\nХвост трейса:")
        await update.message.reply_text(err[-3500:])
