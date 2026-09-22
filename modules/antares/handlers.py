"""Antares Telegram command callbacks. Import does not bind or register."""

from __future__ import annotations

import asyncio
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from core.job_dispatch import get_job_executor
from core.job_runner import Actor, get_status
from core.lock_status import get_lock_status_for_job_types
from core.tg_command_dispatch import guard_or_deny, run_job_async
from modules.antares.work_admission import (
    ADMISSION_CLOSED_REPLY,
    AdmissionAccepted,
    bound_admission,
    watch_admitted_future,
)

ANTARES_STATUS_JOB_TYPES: tuple[str, ...] = (
    "wallet",
    "hourly",
    "rate",
    "download",
    "wallet_editor_registry_refresh",
    "wallet_editor_registry_replay",
    "script_job:operator_wallets_ready",
)

_MSK = ZoneInfo("Europe/Moscow")

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
    admission = bound_admission()
    if admission is None:
        rules, logger = _require_bound()
        if not await guard_or_deny(update, command, rules):
            return
        await run_job_async(update, job_type, logger)
        return

    rules, logger = _require_bound()
    if not await guard_or_deny(update, command, rules):
        return

    actor = Actor(
        kind="tg",
        chat_id=int(update.effective_chat.id),
        user_id=int(update.effective_user.id),
    )
    try:
        outcome = admission.submit_job_if_open(job_type, actor)
    except Exception:
        logger.exception("isolated %s submit failed", job_type)
        await update.message.reply_text("❌ Ошибка при постановке.\nХвост трейса:")
        await update.message.reply_text(traceback.format_exc()[-3500:])
        return

    if not isinstance(outcome, AdmissionAccepted):
        await update.message.reply_text(ADMISSION_CLOSED_REPLY)
        return

    admitted = watch_admitted_future(outcome.future, logger, job_type=job_type)
    try:
        await update.message.reply_text(f"🚀 Запускаю: {job_type}")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("isolated %s start reply failed", job_type)

    try:
        job_id = await admitted.wait()
    except asyncio.CancelledError:
        raise
    except Exception:
        try:
            await update.message.reply_text("❌ Ошибка при выполнении.\nХвост трейса:")
            await update.message.reply_text(traceback.format_exc()[-3500:])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("isolated %s error reply failed", job_type)
        return

    try:
        await update.message.reply_text(f"✅ Принято: {job_type}\njob_id={job_id}")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("isolated %s result reply failed", job_type)


async def _admit_direct_work(
    update: Update,
    logger: object,
    *,
    work: str,
    fn,
    args: tuple = (),
    kwargs: dict | None = None,
    start_text: str | None = None,
):
    admission = bound_admission()
    assert admission is not None
    try:
        outcome = admission.submit_if_open(
            get_job_executor(),
            fn,
            *args,
            **(kwargs or {}),
        )
    except Exception:
        logger.exception("isolated %s submit failed", work)
        await update.message.reply_text("❌ Ошибка при постановке.\nХвост трейса:")
        await update.message.reply_text(traceback.format_exc()[-3500:])
        return None
    if not isinstance(outcome, AdmissionAccepted):
        await update.message.reply_text(ADMISSION_CLOSED_REPLY)
        return None
    admitted = watch_admitted_future(outcome.future, logger, work=work)
    if start_text is None:
        return admitted
    try:
        await update.message.reply_text(start_text)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("isolated %s start reply failed", work)
    return admitted


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
    admission = bound_admission()
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "registry_replay", rules):
        return
    from integrations.wallet_editor_registry import replay_pending_outbox_records

    start_text = "🔄 Replaying pending/failed registry outbox..."

    def _summary(result) -> str:
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
        return "\n".join(lines)

    if admission is None:
        # Mixed/unbound: replay stays synchronous on the event-loop thread.
        await update.message.reply_text(start_text)
        try:
            result = replay_pending_outbox_records()
            await update.message.reply_text(_summary(result))
        except Exception as e:
            logger.exception("cmd_registry_replay failed")
            await update.message.reply_text(f"❌ /registry_replay failed: {type(e).__name__}: {e}")
        return

    # Isolated change: replay used to run on the event-loop thread; now it is
    # submitted to the shared job executor via submit_if_open (not request_job).
    admitted = await _admit_direct_work(
        update,
        logger,
        work="registry_replay",
        fn=replay_pending_outbox_records,
        start_text=start_text,
    )
    if admitted is None:
        return
    try:
        result = await admitted.wait()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            await update.message.reply_text(
                f"❌ /registry_replay failed: {type(e).__name__}: {e}"
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("isolated %s error reply failed", "registry_replay")
        return
    try:
        await update.message.reply_text(_summary(result))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("isolated %s result reply failed", "registry_replay")


async def cmd_registry_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admission = bound_admission()
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "registry_export", rules):
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        await update.message.reply_text("❌ Registry export failed: chat_id unavailable")
        return

    from telegram import InputFile
    from integrations.wallet_editor_registry_db.registry_export_builder import (
        build_registry_export_from_postgres,
        format_registry_export_summary,
    )

    start_text = "📤 Building registry export from PostgreSQL..."

    async def _deliver(artifact) -> None:
        summary_text = format_registry_export_summary(artifact.summary)
        with artifact.path.open("rb") as export_file:
            await update.message.reply_document(
                document=InputFile(export_file, filename=artifact.filename),
                caption=summary_text[:1024],
            )
        if len(summary_text) > 1024:
            await update.message.reply_text(summary_text)

    if admission is None:
        await update.message.reply_text(start_text)
        try:
            loop = asyncio.get_running_loop()
            artifact = await loop.run_in_executor(
                None,
                build_registry_export_from_postgres,
            )
            await _deliver(artifact)
        except Exception as e:
            logger.exception("cmd_registry_export failed")
            await update.message.reply_text(f"❌ Registry export failed: {e}")
        return

    admitted = await _admit_direct_work(
        update,
        logger,
        work="registry_export",
        fn=build_registry_export_from_postgres,
        start_text=start_text,
    )
    if admitted is None:
        return
    try:
        artifact = await admitted.wait()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            await update.message.reply_text(f"❌ Registry export failed: {e}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("isolated %s error reply failed", "registry_export")
        return
    try:
        await _deliver(artifact)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("cmd_registry_export failed")
        await update.message.reply_text(f"❌ Registry export failed: {e}")


async def cmd_auto_enable_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admission = bound_admission()
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "auto_enable_plan", rules):
        return

    from integrations.wallet_editor_auto_enable import run_auto_enable_plan

    actor = Actor(
        kind="tg",
        chat_id=int(update.effective_chat.id),
        user_id=int(update.effective_user.id),
    )
    start_text = "🧩 Строю WalletEditor Auto-Enable plan (plan-only)..."

    async def _reply_plan(result) -> None:
        if result.skipped_reason == "disabled":
            await update.message.reply_text("ℹ️ Auto-Enable disabled (job_params enabled=0).")
        elif result.skipped_reason == "error":
            await update.message.reply_text("⚠️ Auto-Enable plan failed. См. route-отчёт.")
        else:
            await update.message.reply_text(
                f"✅ Plan-only report sent={result.sent}. Antares/registry unchanged."
            )

    if admission is None:
        await update.message.reply_text(start_text)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: run_auto_enable_plan(actor, manual=True),
            )
            await _reply_plan(result)
        except Exception as e:
            logger.exception("cmd_auto_enable_plan failed")
            await update.message.reply_text(f"❌ /auto_enable_plan failed: {type(e).__name__}: {e}")
        return

    admitted = await _admit_direct_work(
        update,
        logger,
        work="auto_enable_plan",
        fn=run_auto_enable_plan,
        args=(actor,),
        kwargs={"manual": True},
        start_text=start_text,
    )
    if admitted is None:
        return
    try:
        result = await admitted.wait()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            await update.message.reply_text(
                f"❌ /auto_enable_plan failed: {type(e).__name__}: {e}"
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("isolated %s error reply failed", "auto_enable_plan")
        return
    try:
        await _reply_plan(result)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("isolated %s result reply failed", "auto_enable_plan")


async def cmd_auto_enable_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admission = bound_admission()
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "auto_enable_run", rules):
        return

    from integrations.wallet_editor_auto_enable import run_auto_enable

    actor = Actor(
        kind="tg",
        chat_id=int(update.effective_chat.id),
        user_id=int(update.effective_user.id),
    )
    start_text = "🧩 Запускаю WalletEditor Auto-Enable (fresh plan + execution)..."

    async def _reply_run(result) -> None:
        if result.skipped_reason == "disabled":
            await update.message.reply_text("ℹ️ Auto-Enable disabled (job_params enabled=0).")
        elif result.skipped_reason == "error":
            await update.message.reply_text("⚠️ Auto-Enable run failed. См. route-отчёт.")
        elif result.phase == "plan-only":
            await update.message.reply_text(
                "ℹ️ Execution blocked by settings (dry_run=1). Plan-only report sent."
            )
        else:
            await update.message.reply_text(
                f"✅ Auto-Enable execution finished. Telegram report sent={result.sent}"
            )

    if admission is None:
        await update.message.reply_text(start_text)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: run_auto_enable(actor, manual=True),
            )
            await _reply_run(result)
        except Exception as e:
            logger.exception("cmd_auto_enable_run failed")
            await update.message.reply_text(f"❌ /auto_enable_run failed: {type(e).__name__}: {e}")
        return

    admitted = await _admit_direct_work(
        update,
        logger,
        work="auto_enable_run",
        fn=run_auto_enable,
        args=(actor,),
        kwargs={"manual": True},
        start_text=start_text,
    )
    if admitted is None:
        return
    try:
        result = await admitted.wait()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            await update.message.reply_text(
                f"❌ /auto_enable_run failed: {type(e).__name__}: {e}"
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("isolated %s error reply failed", "auto_enable_run")
        return
    try:
        await _reply_run(result)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("isolated %s result reply failed", "auto_enable_run")


def _observation_enabled() -> bool:
    return os.getenv("OBSERVATION_ENABLED", "").strip().lower() in {"1", "true", "yes", "y"}


def _antares_help_text() -> str:
    return (
        "Команды:\n"
        "/status\n"
        "/whoami\n"
        "/reload_rules\n"
        "/run_wallet\n"
        "/run_hourly\n"
        "/run_download\n"
        "/run_rate\n"
        "/operator_wallets_ready\n"
        "/rules_validate\n"
        "/auto_enable_plan\n"
        "/auto_enable_run\n"
        "/wallet_editor_refresh\n"
        "/registry_health\n"
        "/registry_replay\n"
        "/registry_export\n"
        "/help"
    )


def _format_ts_msk(ts: object) -> str:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=_MSK).strftime("%Y-%m-%d %H:%M:%S MSK")
    return "none"


def _format_conversion_observation_lines() -> list[str]:
    from core.state_store import state_get

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


def _format_antares_job_health_lines() -> list[str]:
    from core.job_health import format_job_health_lines

    return format_job_health_lines(job_types=ANTARES_STATUS_JOB_TYPES)


def _running_antares_jobs() -> dict:
    st = get_status()
    return {jt: info for jt, info in st.items() if jt in ANTARES_STATUS_JOB_TYPES}


def _format_antares_observation_status() -> str:
    from core.scheduler_health import get_scheduler_health_snapshot

    lines = ["📊 Status"]
    try:
        health = get_scheduler_health_snapshot()
        tick_ts = health.get("scheduler_last_tick_ts", "unknown")
        tick_age = health.get("scheduler_last_tick_age_sec", "unknown")
        last_error = health.get("scheduler_last_error", "unknown")
        active_count = health.get("scheduler_active_schedules_count", "unknown")
        if isinstance(tick_ts, (int, float)):
            tick_human = datetime.fromtimestamp(tick_ts, tz=_MSK).strftime("%Y-%m-%d %H:%M:%S MSK")
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
        from integrations.telegram_bot import get_telegram_sender_health_snapshot

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
        locks = get_lock_status_for_job_types(ANTARES_STATUS_JOB_TYPES)
        for jt in ANTARES_STATUS_JOB_TYPES:
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
        lines.extend(_format_antares_job_health_lines())
    except Exception:
        lines.append("job_health:")
        lines.append("- unknown")

    lines.append("")
    lines.append("jobs:")
    try:
        st = _running_antares_jobs()
        if not st:
            lines.append("🟢 idle")
        else:
            lines.append("🟠 running:")
            for jt, info in st.items():
                started = datetime.fromtimestamp(info["started_ts"], tz=_MSK).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(
                    f"- {jt}: job_id={info['job_id']} runtime={info['runtime_sec']}s старт={started}"
                )
    except Exception:
        lines.append("unknown")

    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rules, _logger = _require_bound()
    if not await guard_or_deny(update, "start", rules):
        return
    await update.message.reply_text("Ок.\nЯ готов.\n\n" + _antares_help_text())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rules, _logger = _require_bound()
    if not await guard_or_deny(update, "help", rules):
        return
    await update.message.reply_text(_antares_help_text())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "status", rules):
        return

    if _observation_enabled():
        try:
            await update.message.reply_text(_format_antares_observation_status())
        except Exception:
            logger.exception("cmd_status observation format failed")
            await update.message.reply_text("⚠️ Status partially unavailable")
        return

    try:
        st = _running_antares_jobs()
    except Exception:
        await update.message.reply_text("unknown")
        return
    if not st:
        await update.message.reply_text("🟢 Сейчас ничего не выполняется.")
        return

    lines = ["🟠 Сейчас выполняется:"]
    for jt, info in st.items():
        started = datetime.fromtimestamp(info["started_ts"]).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"- {jt}: job_id={info['job_id']} runtime={info['runtime_sec']}s старт={started}")
    await update.message.reply_text("\n".join(lines))


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rules, _logger = _require_bound()
    if not await guard_or_deny(update, "whoami", rules):
        return

    chat = update.effective_chat
    user = update.effective_user

    try:
        snap = rules.get_snapshot()
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
    import asyncio

    from core.rules_v2.ops_rules_validate_summary import build_rules_validate_telegram_chunks_with_payload
    from core.rules_v2.rules_validate_audit import try_append_manual_validate_audit_from_payload

    rules, logger = _require_bound()
    if not await guard_or_deny(update, "rules_validate", rules):
        return

    loop = asyncio.get_running_loop()
    try:
        chunks, payload = await loop.run_in_executor(None, build_rules_validate_telegram_chunks_with_payload)
    except Exception as e:
        logger.exception("/rules_validate failed")
        await update.message.reply_text(f"❌ /rules_validate failed: {type(e).__name__}: {e}")
        return

    total = len(chunks)
    for idx, body in enumerate(chunks):
        prefix = "" if idx == 0 else f"(part {idx + 1}/{total})\n"
        await update.message.reply_text(prefix + body)

    try:
        await loop.run_in_executor(None, try_append_manual_validate_audit_from_payload, payload)
    except Exception:  # noqa: BLE001
        logger.exception("rules_validate_audit: tg manual_validate hook failed (ignored)")


class IsolatedReloadNotApplied(Exception):
    """Isolated `/reload_rules` did not reset clocks (stale_reuse)."""


def _reload_bound_rules(rules: object):
    from core.rules_provider import (
        OUTCOME_STALE_REUSE,
        publish_with_outcome,
    )
    from core.scheduler_clocks_control import request_scheduler_clocks_reset

    rules.invalidate()
    result = publish_with_outcome(force_sync=True)
    if result.outcome == OUTCOME_STALE_REUSE:
        raise IsolatedReloadNotApplied("stale_reuse")
    snap = rules.get_snapshot(force_sync=False)
    request_scheduler_clocks_reset(reason="reload_rules")
    return snap


async def cmd_reload_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from core.scheduler_clocks_control import request_scheduler_clocks_reset

    admission = bound_admission()
    rules, logger = _require_bound()
    if not await guard_or_deny(update, "reload_rules", rules):
        return

    if admission is None:
        # Mixed/unbound: keep the previous synchronous reload on the callback thread.
        rules.invalidate()
        try:
            snap = rules.get_snapshot(force_sync=True)
            request_scheduler_clocks_reset(reason="reload_rules")
            await update.message.reply_text(
                f"♻️ rules snapshot перечитан.\n"
                f"source: {snap.source}"
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Не смог перечитать rules.xlsx: {e}")
        return

    admitted = await _admit_direct_work(
        update,
        logger,
        work="reload_rules",
        fn=_reload_bound_rules,
        args=(rules,),
    )
    if admitted is None:
        return
    try:
        snap = await admitted.wait()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            await update.message.reply_text(f"⚠️ Не смог перечитать rules.xlsx: {e}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("isolated %s error reply failed", "reload_rules")
        return
    try:
        await update.message.reply_text(
            f"♻️ rules snapshot перечитан.\n"
            f"source: {snap.source}"
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("isolated %s result reply failed", "reload_rules")


def get_antares_handlers():
    from telegram.ext import CommandHandler, MessageHandler, filters

    from modules.antares.document_ingest import handle_wallet_editor_document

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
