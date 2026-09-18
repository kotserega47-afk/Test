"""Antares Telegram run-command callbacks. Import does not bind or register."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.tg_command_dispatch import guard_or_deny, run_job_async

_rules: object | None = None
_logger: object | None = None


class HandlerNotBoundError(RuntimeError):
    """Raised when a callback runs before both RULES and logger are bound."""


class HandlerBindError(RuntimeError):
    """Raised when bind_rules/bind_logger would replace a different object."""


def bind_rules(rules: object) -> None:
    global _rules
    if rules is None:
        raise HandlerBindError("bind_rules requires an AccessRules instance")
    if _rules is not None and _rules is not rules:
        raise HandlerBindError("antares handlers already bound to a different AccessRules instance")
    _rules = rules


def bind_logger(logger: object) -> None:
    global _logger
    if logger is None:
        raise HandlerBindError("bind_logger requires a logger")
    if _logger is not None and _logger is not logger:
        raise HandlerBindError("antares handlers already bound to a different logger")
    _logger = logger


def _require_bound() -> tuple[object, object]:
    if _rules is None or _logger is None:
        raise HandlerNotBoundError(
            "antares handlers are not fully bound (need bind_rules and bind_logger)"
        )
    return _rules, _logger


async def _run_antares_command(update: Update, command: str, job_type: str) -> None:
    rules, logger = _require_bound()
    if not await guard_or_deny(update, command, rules):
        return
    await run_job_async(update, job_type, logger)


async def cmd_run_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_antares_command(update, "run_wallet", "wallet")


async def cmd_run_hourly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_antares_command(update, "run_hourly", "hourly")


async def cmd_run_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_antares_command(update, "run_download", "download")


async def cmd_run_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_antares_command(update, "run_rate", "rate")


async def cmd_operator_wallets_ready(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_antares_command(update, "operator_wallets_ready", "script_job:operator_wallets_ready")


async def cmd_wallet_editor_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_antares_command(update, "wallet_editor_refresh", "wallet_editor_registry_refresh")


async def cmd_registry_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "registry_health", rules):
        return
    from integrations.wallet_editor_registry import (
        build_registry_health_report,
        format_registry_health_report,
    )

    try:
        report = build_registry_health_report()
        await update.message.reply_text(format_registry_health_report(report))
    except Exception as e:
        logger.exception("cmd_registry_health failed")
        await update.message.reply_text(f"❌ /registry_health failed: {type(e).__name__}: {e}")


async def cmd_registry_replay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "registry_replay", rules):
        return
    from integrations.wallet_editor_registry import replay_pending_outbox_records

    await update.message.reply_text("🔄 Replaying pending/failed registry outbox...")
    try:
        result = replay_pending_outbox_records()
        lines = [
            "Registry outbox replay",
            f"attempted: {result.attempted}",
            f"synced: {result.synced}",
            f"failed: {result.failed}",
            f"skipped: {result.skipped}",
        ]
        if result.errors:
            lines.append("")
            lines.append("errors:")
            lines.extend(f"- {err}" for err in result.errors[:10])
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.exception("cmd_registry_replay failed")
        await update.message.reply_text(f"❌ /registry_replay failed: {type(e).__name__}: {e}")


async def cmd_registry_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "registry_export", rules):
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        await update.message.reply_text("❌ Registry export failed: chat_id unavailable")
        return

    import asyncio

    from telegram import InputFile
    from integrations.wallet_editor_registry_db.registry_export_builder import (
        build_registry_export_from_postgres,
        format_registry_export_summary,
    )

    await update.message.reply_text("📤 Building registry export from PostgreSQL...")
    try:
        loop = asyncio.get_running_loop()
        artifact = await loop.run_in_executor(
            None,
            build_registry_export_from_postgres,
        )
        summary_text = format_registry_export_summary(artifact.summary)
        with artifact.path.open("rb") as export_file:
            await update.message.reply_document(
                document=InputFile(export_file, filename=artifact.filename),
                caption=summary_text[:1024],
            )
        if len(summary_text) > 1024:
            await update.message.reply_text(summary_text)
    except Exception as e:
        logger.exception("cmd_registry_export failed")
        await update.message.reply_text(f"❌ Registry export failed: {e}")
