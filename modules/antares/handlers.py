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
