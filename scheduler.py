# scheduler.py
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from telegram.ext import Application

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
# Ensure Telegram token redaction / httpx quieting is installed for this process.
import utils.logger  # noqa: F401

from core.schedules import load_schedules, Schedule
from core.scheduler_clocks_control import _apply_scheduler_clock_reset_if_requested
from core.scheduler_health import (
    record_error,
    record_hourly_gate_fire,
    record_hourly_gate_skip,
    record_schedules_loaded,
    record_tick,
)
from core.event_log import append_event
from core.job_health import evaluate_job_health_if_due
from core.job_dispatch import dispatch_job_background
from core.job_runner import Actor
from core.config_manager import get_job_params
from integrations.tg_commands import get_handlers, RULES
from integrations.telegram_bot import log_telegram_health_if_due
from observability.process_resource_health import log_process_resource_health_if_due
from automation.worker import ensure_worker_started

MSK = ZoneInfo("Europe/Moscow")


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("MAIN")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


# =============================================================================
# Cron (минимальный, но надёжный)
# =============================================================================

def _parse_cron_min_hour(expr: str) -> Tuple[int, Optional[int]]:
    """
    Поддержка:
      - "M H * * *" где M=0..59, H=0..23 или '*'
    Пример:
      - "0 * * * *"  -> каждый час в :00
      - "5 2 * * *"  -> каждый день в 02:05
    """
    parts = (expr or "").strip().split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 parts: 'M H * * *'")

    m_s, h_s, _, _, _ = parts

    if m_s == "*":
        raise ValueError("cron minute '*' not supported; use explicit minute 0..59")
    minute = int(m_s)

    hour_any = (h_s == "*")
    hour = None if hour_any else int(h_s)

    if not (0 <= minute <= 59):
        raise ValueError("cron minute out of range 0..59")
    if hour is not None and not (0 <= hour <= 23):
        raise ValueError("cron hour out of range 0..23")

    return minute, hour  # hour=None means '*'


def _next_cron_run(now: datetime, cron_expr: str) -> datetime:
    minute, hour = _parse_cron_min_hour(cron_expr)

    if hour is None:
        # every hour at :minute
        target = now.replace(minute=minute, second=5, microsecond=0)
        if target <= now:
            target = target + timedelta(hours=1)
        return target

    # every day at hour:minute
    target = now.replace(hour=hour, minute=minute, second=5, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target


# =============================================================================
# Hourly gating via job_params (control plane)
# =============================================================================

def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    s = (s or "").strip()
    if not s:
        return None
    if ":" not in s:
        return None
    hh_s, mm_s = s.split(":", 1)
    try:
        hh = int(hh_s)
        mm = int(mm_s)
    except Exception:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


@dataclass
class HourlyGate:
    """
    Два триггера:
      - intraday: каждые N минут
      - final: один раз в сутки в final_daily_time
    Дедуп делаем по "ключам запуска" на уровне scheduler,
    чтобы не спамить request_job если schedules тикают часто.
    """
    last_intraday_key: Optional[str] = None  # YYYYMMDD-HHMM bucket
    last_final_key: Optional[str] = None     # YYYYMMDD final date key


_GATE_SKIP_EVENT_INTERVAL_SEC = 300.0
_last_gate_skip_event_ts: Dict[str, float] = {}


def _reset_hourly_gate_skip_throttle_for_tests() -> None:
    _last_gate_skip_event_ts.clear()


def evaluate_hourly_gate(now: datetime, gate: HourlyGate) -> tuple[bool, str]:
    """
    Decide whether scheduler may dispatch job_type=hourly.

    Returns (should_fire, reason). Reason describes the fire trigger or skip cause.
    Intraday dedup uses wall-clock buckets (total_minutes // interval), so the first
    schedule tick inside a new bucket fires even when minute % interval != 0.
    """
    params = get_job_params(job="hourly")

    intraday_min = int(params.get("intraday_interval_minutes") or 0)
    final_time = _parse_hhmm(str(params.get("final_daily_time") or ""))

    if intraday_min <= 0 and final_time is None:
        return False, (
            "no_gate_config: set job_params intraday_interval_minutes or "
            "final_daily_time for job=hourly"
        )

    fired = False
    fire_parts: list[str] = []

    if intraday_min > 0:
        total_min = now.hour * 60 + now.minute
        bucket = total_min // intraday_min
        key = f"{now:%Y%m%d}-{bucket:04d}"
        if gate.last_intraday_key != key:
            gate.last_intraday_key = key
            fired = True
            fire_parts.append(f"intraday_interval_minutes={intraday_min} bucket={key}")

    if final_time is not None:
        hh, mm = final_time
        if now.hour == hh and now.minute == mm:
            key = f"{now:%Y%m%d}"
            if gate.last_final_key != key:
                gate.last_final_key = key
                fired = True
                fire_parts.append(f"final_daily_time={hh:02d}:{mm:02d}")

    if fired:
        return True, "; ".join(fire_parts)

    if intraday_min > 0:
        total_min = now.hour * 60 + now.minute
        bucket = total_min // intraday_min
        key = f"{now:%Y%m%d}-{bucket:04d}"
        if gate.last_intraday_key == key:
            return False, f"intraday_already_fired bucket={key}"
        return False, f"intraday_waiting bucket={key} interval={intraday_min}m"

    hh, mm = final_time  # type: ignore[misc]
    return False, (
        f"final_not_due now={now.hour:02d}:{now.minute:02d} "
        f"target={hh:02d}:{mm:02d}"
    )


def _hourly_should_fire(now: datetime, gate: HourlyGate) -> bool:
    """Backward-compatible bool wrapper for evaluate_hourly_gate."""
    should_fire, _reason = evaluate_hourly_gate(now, gate)
    return should_fire


def _emit_hourly_gate_skip(reason: str) -> None:
    record_hourly_gate_skip(reason)
    now = time.time()
    prev = _last_gate_skip_event_ts.get(reason, 0.0)
    if now - prev < _GATE_SKIP_EVENT_INTERVAL_SEC:
        return
    _last_gate_skip_event_ts[reason] = now
    log.info("[scheduler] hourly gate skipped: %s", reason)
    append_event(
        type="job_gate_skipped",
        job_type="hourly",
        payload={"reason": reason},
    )


def _apply_hourly_gate(now: datetime, gate: HourlyGate) -> bool:
    should_fire, reason = evaluate_hourly_gate(now, gate)
    if should_fire:
        record_hourly_gate_fire(reason)
        return True
    _emit_hourly_gate_skip(reason)
    return False


# =============================================================================
# Scheduler loop
# =============================================================================

def schedule_loop() -> None:
    """
    Каждые ~5 сек:
      - читаем schedules из rules.xlsx
      - вычисляем "пора ли"
      - триггерим dispatch_job_background(job_type, actor="scheduler")

    Особенность:
      - Для job_type == "hourly" дополнительно применяем gate из job_params:
        intraday_interval_minutes / final_daily_time.
    """
    next_every: Dict[str, float] = {}     # job_type -> ts_next
    next_cron: Dict[str, datetime] = {}   # job_type -> dt_next
    hourly_gate = HourlyGate()

    log.info("🕒 schedule loop started (rules.xlsx:schedules + job_params gating)")

    while True:
        _apply_scheduler_clock_reset_if_requested(next_every, next_cron, logger=log)
        record_tick()
        evaluate_job_health_if_due()
        log_telegram_health_if_due()
        log_process_resource_health_if_due()

        try:
            schedules = load_schedules(force_sync=False)
        except Exception as e:
            record_error(str(e))
            log.warning(f"⚠️ schedules load failed: {e}")
            time.sleep(10)
            continue

        record_schedules_loaded(len(schedules))

        active = {s.job_type for s in schedules}
        for k in list(next_every.keys()):
            if k not in active:
                next_every.pop(k, None)
        for k in list(next_cron.keys()):
            if k not in active:
                next_cron.pop(k, None)

        now = datetime.now(MSK)

        for s in schedules:
            jt = s.job_type

            # === every_seconds ===
            if s.schedule_type == "every_seconds":
                ts_now = time.time()
                ts_next = next_every.get(jt)

                if ts_next is None:
                    next_every[jt] = ts_now + max(1, int(s.every_seconds))
                    continue

                if ts_now >= ts_next:
                    # hourly is gated by job_params
                    if jt == "hourly":
                        if not _apply_hourly_gate(now, hourly_gate):
                            next_every[jt] = ts_now + max(1, int(s.every_seconds))
                            continue

                    actor = Actor(kind="scheduler")
                    try:
                        dispatch_job_background(jt, actor)
                    except Exception as e:
                        log.exception(f"❌ scheduled job dispatch failed: {jt}: {e}")

                    next_every[jt] = ts_now + max(1, int(s.every_seconds))

            # === cron ===
            elif s.schedule_type == "cron":
                dt_next = next_cron.get(jt)
                if dt_next is None:
                    try:
                        next_cron[jt] = _next_cron_run(now, s.cron)
                    except Exception as e:
                        log.warning(f"⚠️ bad cron for {jt}: {s.cron} err={e}")
                    continue

                if now >= dt_next:
                    # hourly still gated (cron может быть "каждый час", но intraday=5)
                    if jt == "hourly":
                        if not _apply_hourly_gate(now, hourly_gate):
                            try:
                                next_cron[jt] = _next_cron_run(datetime.now(MSK), s.cron)
                            except Exception:
                                next_cron.pop(jt, None)
                            continue

                    actor = Actor(kind="scheduler")
                    try:
                        dispatch_job_background(jt, actor)
                    except Exception as e:
                        log.exception(f"❌ scheduled job dispatch failed: {jt}: {e}")

                    try:
                        next_cron[jt] = _next_cron_run(datetime.now(MSK), s.cron)
                    except Exception:
                        next_cron.pop(jt, None)

        time.sleep(5)


# =============================================================================
# Entry point
# =============================================================================

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()

    for h in get_handlers():
        app.add_handler(h)

    # прогреваем rules (fail-fast): если rules битые — лучше упасть сразу
    RULES.get_snapshot(force_sync=True)

    ensure_worker_started()
    threading.Thread(target=schedule_loop, daemon=True).start()

    log.info("🟢 Telegram started (polling + schedules + wallet editor worker)")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()