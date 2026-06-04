# core/job_health.py
"""Job Health Guard v2 — observe-only (C1). No lock recovery or runtime changes."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional, Union

from core.event_log import append_event
from core.job_progress import get_progress
from core.job_runner import get_status
from core.lock_status import KNOWN_JOB_TYPES, get_lock_status_for_job_types

log = logging.getLogger(__name__)

JobHealthState = Literal[
    "idle",
    "running_ok",
    "running_slow",
    "stuck",
    "ghost_lock",
    "unknown",
]

SnapshotValue = Union[str, float, int]

_last_eval_ts: float = 0.0
_last_snapshot: Dict[str, Any] = {}


def job_health_guard_enabled() -> bool:
    return os.getenv("JOB_HEALTH_GUARD_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }


def job_health_recovery_mode() -> str:
    """C1: only ``observe`` is effective; recovery actions are not implemented."""
    raw = os.getenv("JOB_HEALTH_RECOVERY_MODE", "observe").strip().lower()
    if raw in {"off", "observe", "ghost_lock_only", "stuck_release"}:
        return raw
    return "observe"


def _tick_interval_sec() -> float:
    raw = os.getenv("JOB_HEALTH_TICK_INTERVAL_SEC", "30").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 30.0


def _warning_seconds() -> float:
    raw = os.getenv("JOB_HEALTH_WARNING_SECONDS", "600").strip()
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 600.0


def _timeout_seconds() -> float:
    raw = os.getenv("JOB_HEALTH_TIMEOUT_SECONDS", "1800").strip()
    try:
        return max(120.0, float(raw))
    except ValueError:
        return 1800.0


def _progress_timeout_seconds() -> float:
    raw = os.getenv("JOB_HEALTH_PROGRESS_TIMEOUT_SECONDS", "600").strip()
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 600.0


def _executor_queue_depth() -> SnapshotValue:
    try:
        from core.job_dispatch import get_job_executor

        executor = get_job_executor()
        q = getattr(executor, "_work_queue", None)
        if q is None:
            return "unknown"
        return q.qsize()
    except Exception:
        return "unknown"


def _classify_running(
    *,
    runtime_sec: float,
    progress_age_sec: Optional[float],
) -> JobHealthState:
    progress_timeout = _progress_timeout_seconds()
    timeout = _timeout_seconds()
    warning = _warning_seconds()

    stuck = False
    if progress_age_sec is not None and progress_age_sec > progress_timeout:
        stuck = True
    if runtime_sec > timeout:
        stuck = True
    if stuck:
        return "stuck"

    if runtime_sec > warning:
        return "running_slow"
    return "running_ok"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _lock_stale_max_age_sec() -> float:
    raw = os.getenv("JOB_LOCK_STALE_SEC", "600").strip()
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 600.0


def _is_ghost_lock(job_type: str, lock_pid: SnapshotValue, lock_age: SnapshotValue) -> bool:
    """Lock file present but not held by a live in-process job (read-only, mirrors job_runner)."""
    if lock_pid == "none" or lock_age == "none":
        return False
    if job_type in get_status():
        return False
    if lock_pid == "unknown":
        return False
    try:
        pid = int(lock_pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if pid <= 0 or not _pid_alive(pid):
        return True
    if pid == os.getpid():
        return True
    if isinstance(lock_age, (int, float)) and float(lock_age) > _lock_stale_max_age_sec():
        return True
    return False


def evaluate_job_health() -> Dict[str, Any]:
    """Build a fresh health snapshot for all known job types."""
    now = time.time()
    running = get_status()
    locks = get_lock_status_for_job_types(KNOWN_JOB_TYPES)
    jobs: Dict[str, Dict[str, SnapshotValue]] = {}

    for jt in KNOWN_JOB_TYPES:
        lock_info = locks.get(jt, {})
        lock_age = lock_info.get("lock_age_sec", "unknown")
        lock_pid = lock_info.get("lock_pid", "unknown")

        if jt in running:
            info = running[jt]
            runtime_sec = float(info.get("runtime_sec", 0))
            prog = get_progress(jt)
            stage: Optional[str] = None
            progress_age: Optional[float] = None
            if prog is not None:
                stage, prog_ts = prog
                progress_age = now - prog_ts
            state = _classify_running(
                runtime_sec=runtime_sec,
                progress_age_sec=progress_age,
            )
            jobs[jt] = {
                "state": state,
                "stage": stage or "unknown",
                "progress_age_sec": progress_age if progress_age is not None else "unknown",
                "runtime_sec": runtime_sec,
                "lock_age_sec": lock_age,
                "lock_pid": lock_pid,
            }
        elif _is_ghost_lock(jt, lock_pid, lock_age):
            jobs[jt] = {
                "state": "ghost_lock",
                "stage": "none",
                "progress_age_sec": "none",
                "runtime_sec": "none",
                "lock_age_sec": lock_age,
                "lock_pid": lock_pid,
            }
        else:
            jobs[jt] = {
                "state": "idle",
                "stage": "none",
                "progress_age_sec": "none",
                "runtime_sec": "none",
                "lock_age_sec": lock_age,
                "lock_pid": lock_pid,
            }

    snapshot = {
        "enabled": job_health_guard_enabled(),
        "mode": job_health_recovery_mode() if job_health_guard_enabled() else "off",
        "evaluated_at_ts": now,
        "executor_queue_depth": _executor_queue_depth(),
        "jobs": jobs,
    }
    return snapshot


def _emit_observe_events(snapshot: Dict[str, Any]) -> None:
    """Best-effort degraded telemetry (observe only — no recovery)."""
    if not job_health_guard_enabled():
        return
    if job_health_recovery_mode() == "off":
        return

    for jt, info in (snapshot.get("jobs") or {}).items():
        state = info.get("state")
        if state not in {"running_slow", "stuck", "ghost_lock"}:
            continue
        try:
            append_event(
                type="job_health_degraded",
                job_type=jt,
                payload={
                    "state": state,
                    "stage": info.get("stage"),
                    "progress_age_sec": info.get("progress_age_sec"),
                    "runtime_sec": info.get("runtime_sec"),
                    "recovery_mode": job_health_recovery_mode(),
                },
            )
        except Exception:
            log.warning("job_health: append_event failed for %s", jt, exc_info=True)


def evaluate_job_health_if_due() -> None:
    """Called from scheduler loop; throttled, never raises."""
    global _last_eval_ts, _last_snapshot
    if not job_health_guard_enabled():
        return
    try:
        now = time.time()
        if now - _last_eval_ts < _tick_interval_sec():
            return
        _last_eval_ts = now
        _last_snapshot = evaluate_job_health()
        _emit_observe_events(_last_snapshot)
    except Exception:
        log.warning("job_health: evaluate failed", exc_info=True)


def get_job_health_snapshot(*, force_refresh: bool = False) -> Dict[str, Any]:
    """Snapshot for /status (uses cache unless force_refresh or guard disabled)."""
    global _last_snapshot
    if not job_health_guard_enabled():
        return {
            "enabled": False,
            "mode": "off",
            "executor_queue_depth": "unknown",
            "jobs": {jt: {"state": "unknown"} for jt in KNOWN_JOB_TYPES},
        }
    if force_refresh or not _last_snapshot:
        return evaluate_job_health()
    return _last_snapshot


def format_job_health_lines() -> List[str]:
    """Lines for ``/status`` job_health block."""
    snap = get_job_health_snapshot(force_refresh=True)
    lines = ["job_health:", f"- mode={snap.get('mode', 'off')}"]
    qd = snap.get("executor_queue_depth", "unknown")
    lines.append(f"- executor_queue_depth={qd}")

    jobs = snap.get("jobs") or {}
    for jt in KNOWN_JOB_TYPES:
        info = jobs.get(jt, {})
        state = info.get("state", "unknown")
        line_parts = [f"- {jt}: state={state}"]
        stage = info.get("stage")
        if stage and stage != "none" and state in {"running_ok", "running_slow", "stuck"}:
            line_parts.append(f"stage={stage}")
        pa = info.get("progress_age_sec")
        if pa != "none" and pa != "unknown" and state in {"running_ok", "running_slow", "stuck"}:
            if isinstance(pa, (int, float)):
                line_parts.append(f"progress_age={round(float(pa), 1)}s")
            else:
                line_parts.append(f"progress_age={pa}")
        rt = info.get("runtime_sec")
        if isinstance(rt, (int, float)) and state in {"running_ok", "running_slow", "stuck"}:
            line_parts.append(f"runtime={round(float(rt), 1)}s")
        if state in {"running_ok", "running_slow", "stuck", "ghost_lock"}:
            line_parts.append(f"lock_pid={info.get('lock_pid', 'unknown')}")
            la = info.get("lock_age_sec", "unknown")
            line_parts.append(f"lock_age={la}s" if isinstance(la, (int, float)) else f"lock_age={la}")
        lines.append(" ".join(line_parts))

    return lines


def _reset_job_health_for_tests() -> None:
    global _last_eval_ts, _last_snapshot
    _last_eval_ts = 0.0
    _last_snapshot = {}
