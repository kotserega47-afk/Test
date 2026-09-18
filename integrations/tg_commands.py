# integrations/tg_commands.py
from __future__ import annotations
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from core.access_rules import AccessRules
from core.job_runner import get_status, JOB_REGISTRY
from core.tg_command_dispatch import build_access_context, guard_or_deny, run_job_async
from core.lock_status import KNOWN_JOB_TYPES, get_lock_status_for_job_types
from core.scheduler_health import get_scheduler_health_snapshot

from integrations.telegram_bot import get_telegram_sender_health_snapshot

from integrations import raccoon_jobs  # noqa: F401 — registers Raccoon job types
from integrations import script_jobs  # noqa: F401 — registers script_job:* handlers
from modules.antares.jobs import register_jobs
from modules.antares.jobs import run_download_job, run_hourly_job  # noqa: F401 — compatible exports
from modules.antares.handlers import (
    bind_logger,
    bind_rules,
    cmd_auto_enable_plan,
    cmd_auto_enable_run,
    cmd_operator_wallets_ready,
    cmd_registry_export,
    cmd_registry_health,
    cmd_registry_replay,
    cmd_run_download,
    cmd_run_hourly,
    cmd_run_rate,
    cmd_run_wallet,
    cmd_wallet_editor_refresh,
)

from integrations.wallet_editor_tg import handle_wallet_editor_document
from core.state_store import state_get
from core.scheduler_clocks_control import request_scheduler_clocks_reset
from core.rules_v2.ops_rules_validate_summary import build_rules_validate_telegram_chunks_with_payload
from core.rules_v2.rules_validate_audit import try_append_manual_validate_audit_from_payload


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("MAIN")

MSK = ZoneInfo("Europe/Moscow")

RULES = AccessRules(os.getenv("RULES_XLSX_PATH", "").strip())
bind_logger(log)
bind_rules(RULES)


def _observation_enabled() -> bool:
    return os.getenv("OBSERVATION_ENABLED", "").strip().lower() in {"1", "true", "yes", "y"}


def _format_ts_msk(ts: object) -> str:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=MSK).strftime("%Y-%m-%d %H:%M:%S MSK")
    return "none"


def _format_conversion_observation_lines() -> list[str]:
    lines = ["Conversion:"]
    try:
        last_status = state_get("conversion", "last_status")
        last_run_ts = state_get("conversion", "last_run_ts")
        if last_status is None and last_run_ts is None:
            lines.append("- no data")
            return lines

        lines.append(f"- last_status={last_status or 'unknown'}")
        lines.append(f"- last_run={_format_ts_msk(last_run_ts)}")
        lines.append(f"- last_success={_format_ts_msk(state_get('conversion', 'last_success_ts'))}")
        lines.append(f"- last_failure={_format_ts_msk(state_get('conversion', 'last_failure_ts'))}")
        lines.append(f"- last_file={state_get('conversion', 'last_conv_filename') or 'none'}")
        last_error = state_get("conversion", "last_error")
        lines.append(f"- last_error={last_error if last_error else 'none'}")
        runtime = state_get("conversion", "last_runtime_sec")
        lines.append(f"- last_runtime_sec={runtime if runtime is not None else 'none'}")
    except Exception:
        lines.append("- unknown")
    return lines


def _format_observation_status() -> str:
    lines = ["📊 Status"]

    try:
        health = get_scheduler_health_snapshot()
        tick_ts = health.get("scheduler_last_tick_ts", "unknown")
        tick_age = health.get("scheduler_last_tick_age_sec", "unknown")
        last_error = health.get("scheduler_last_error", "unknown")
        active_count = health.get("scheduler_active_schedules_count", "unknown")

        if isinstance(tick_ts, (int, float)):
            tick_human = datetime.fromtimestamp(tick_ts, tz=MSK).strftime("%Y-%m-%d %H:%M:%S MSK")
        else:
            tick_human = "unknown"

        lines.append(f"scheduler: tick_age={tick_age}s last_tick={tick_human}")
        lines.append(f"scheduler: active_schedules={active_count} last_error={last_error}")
        lines.append(
            f"scheduler: hourly_gate_skip={health.get('hourly_gate_last_skip_reason', 'unknown')} "
            f"age={health.get('hourly_gate_last_skip_age_sec', 'unknown')}s"
        )
        lines.append(
            f"scheduler: hourly_gate_fire={health.get('hourly_gate_last_fire_reason', 'unknown')} "
            f"age={health.get('hourly_gate_last_fire_age_sec', 'unknown')}s"
        )
    except Exception:
        lines.append("scheduler: unknown")

    lines.append("")
    lines.append("telegram_sender:")
    try:
        tg = get_telegram_sender_health_snapshot()
        lines.append(
            f"- status={tg.get('status', 'unknown')} "
            f"sent={tg.get('total_sent', 'unknown')} "
            f"failed={tg.get('total_failed', 'unknown')} "
            f"queue_depth={tg.get('queue_depth', 'unknown')} "
            f"consecutive={tg.get('consecutive_failures', 'unknown')}"
        )
        lines.append(
            f"- last_success_age={tg.get('last_success_age_sec', 'unknown')}s "
            f"last_error={tg.get('last_error_class', 'none')}"
        )
    except Exception:
        lines.append("- unknown")

    lines.append("")
    try:
        from integrations.telegram_routes import format_telegram_routes_status_lines

        lines.extend(format_telegram_routes_status_lines())
    except Exception:
        lines.append("telegram_routes:")
        lines.append("- unknown")

    lines.append("")
    lines.append("locks:")
    try:
        locks = get_lock_status_for_job_types(KNOWN_JOB_TYPES)
        for jt in KNOWN_JOB_TYPES:
            info = locks.get(jt, {})
            pid = info.get("lock_pid", "unknown")
            age = info.get("lock_age_sec", "unknown")
            lines.append(f"- {jt}: pid={pid} age={age}s")
    except Exception:
        lines.append("- unknown")

    lines.append("")
    lines.extend(_format_conversion_observation_lines())

    lines.append("")
    try:
        from core.job_health import format_job_health_lines

        lines.extend(format_job_health_lines())
    except Exception:
        lines.append("job_health:")
        lines.append("- unknown")

    lines.append("")
    lines.append("jobs:")
    try:
        st = get_status()
        if not st:
            lines.append("🟢 idle")
        else:
            lines.append("🟠 running:")
            for jt, info in st.items():
                started = datetime.fromtimestamp(info["started_ts"], tz=MSK).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(
                    f"- {jt}: job_id={info['job_id']} runtime={info['runtime_sec']}s старт={started}"
                )
    except Exception:
        lines.append("unknown")

    return "\n".join(lines)


# =============================================================================
# Jobs (Antares bindings live in modules.antares.jobs)
# =============================================================================

register_jobs(JOB_REGISTRY)

# =============================================================================
# Access helpers
# =============================================================================

def _ctx(update: Update):
    return build_access_context(update)


async def _guard_or_deny(update: Update, command: str) -> bool:
    return await guard_or_deny(update, command, RULES)


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
        "/run_raccoon\n"
        "/run_hourly_raccoon\n"
        "/rules_validate\n"
        "/auto_enable_plan\n"
        "/auto_enable_run\n"
        "/wallet_editor_refresh\n"
        "/registry_health\n"
        "/registry_replay\n"
        "/registry_export\n"
        "/run_script_hello\n"
        "/operator_wallets_ready\n"
        "/help"
    )


async def _run_job_async(update: Update, job_type: str) -> None:
    return await run_job_async(update, job_type, log)


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

    if _observation_enabled():
        try:
            await update.message.reply_text(_format_observation_status())
        except Exception:
            log.exception("cmd_status observation format failed")
            await update.message.reply_text("⚠️ Status partially unavailable")
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

    loop = asyncio.get_running_loop()
    try:
        chunks, payload = await loop.run_in_executor(None, build_rules_validate_telegram_chunks_with_payload)
    except Exception as e:
        log.exception("/rules_validate failed")
        await update.message.reply_text(f"❌ /rules_validate failed: {type(e).__name__}: {e}")
        return

    total = len(chunks)
    for idx, body in enumerate(chunks):
        prefix = "" if idx == 0 else f"(part {idx + 1}/{total})\n"
        await update.message.reply_text(prefix + body)

    try:
        await loop.run_in_executor(None, try_append_manual_validate_audit_from_payload, payload)
    except Exception:  # noqa: BLE001
        log.exception("rules_validate_audit: tg manual_validate hook failed (ignored)")

async def cmd_reload_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "reload_rules"):
        return

    RULES.invalidate()
    try:
        snap = RULES.get_snapshot(force_sync=True)
        request_scheduler_clocks_reset(reason="reload_rules")
        await update.message.reply_text(
            f"♻️ rules snapshot перечитан.\n"
            f"source: {snap.source}"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Не смог перечитать rules.xlsx: {e}")


async def cmd_run_raccoon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "run_raccoon"):
        return
    await _run_job_async(update, "raccoon_wallet")


async def cmd_run_hourly_raccoon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "run_hourly_raccoon"):
        return
    await _run_job_async(update, "raccoon_hourly")


async def cmd_run_script_hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard_or_deny(update, "run_script_hello"):
        return
    await _run_job_async(update, "script_job:hello_world")


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
        CommandHandler("run_raccoon", cmd_run_raccoon),
        CommandHandler("run_hourly_raccoon", cmd_run_hourly_raccoon),
        CommandHandler("run_script_hello", cmd_run_script_hello),
        CommandHandler("operator_wallets_ready", cmd_operator_wallets_ready),
        CommandHandler("rules_validate", cmd_rules_validate),
        CommandHandler("auto_enable_plan", cmd_auto_enable_plan),
        CommandHandler("auto_enable_run", cmd_auto_enable_run),
        CommandHandler("wallet_editor_refresh", cmd_wallet_editor_refresh),
        CommandHandler("registry_health", cmd_registry_health),
        CommandHandler("registry_replay", cmd_registry_replay),
        CommandHandler("registry_export", cmd_registry_export),
        MessageHandler(filters.Document.ALL, handle_wallet_editor_document),
    ]