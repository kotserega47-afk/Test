# core/job_runner.py
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from core.event_log import append_event
from core.rules_provider import get_rules_snapshot


# =============================================================================
# Actor
# =============================================================================

@dataclass(frozen=True)
class Actor:
    kind: str  # "tg" | "scheduler" | "cli"
    chat_id: Optional[int] = None
    user_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# Registry
# =============================================================================

# job_type -> callable() (no args; it can read rules/state itself)
JOB_REGISTRY: Dict[str, Callable[[], Any]] = {}

# runtime state (in-memory)
# job_type -> (job_id, started_ts, actor_dict)
_RUNNING: Dict[str, Tuple[str, float, Dict[str, Any]]] = {}


# =============================================================================
# Locks (NO /tmp) — stored in STATE_DIR/locks
# =============================================================================

_DEFAULT_STATE_DIR = "/config/state"
_STATE_DIR_ENV_KEYS = ("STATE_DIR", "CONFIG_STATE_DIR", "DROPBOX_STATE_DIR")


def _state_dir() -> Path:
    for k in _STATE_DIR_ENV_KEYS:
        v = os.getenv(k, "").strip()
        if v:
            return Path(v)
    return Path(_DEFAULT_STATE_DIR)


def _lock_dir() -> Path:
    d = _state_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_job_name(job_type: str) -> str:
    return "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in (job_type or ""))


def _lock_path(job_type: str) -> Path:
    return _lock_dir() / f"{_safe_job_name(job_type)}.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _try_lock(job_type: str) -> bool:
    """
    Simple cross-process lock:
      - lock file contains pid
      - if pid is alive -> busy
      - if stale -> remove and acquire
    """
    p = _lock_path(job_type)

    if p.exists():
        try:
            pid = int(p.read_text(encoding="utf-8").strip())
            if pid > 0 and _pid_alive(pid):
                return False
        except Exception:
            # unreadable -> treat as stale
            pass
        try:
            p.unlink(missing_ok=True)
        except Exception:
            return False

    try:
        p.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return False


def _unlock(job_type: str) -> None:
    p = _lock_path(job_type)
    try:
        if p.exists():
            pid = p.read_text(encoding="utf-8").strip()
            if pid == str(os.getpid()):
                p.unlink(missing_ok=True)
    except Exception:
        pass


# =============================================================================
# Public API
# =============================================================================

def get_status() -> Dict[str, Any]:
    now = time.time()
    out: Dict[str, Any] = {}
    for jt, (jid, started, actor) in _RUNNING.items():
        out[jt] = {
            "job_id": jid,
            "started_ts": started,
            "runtime_sec": round(now - started, 1),
            "actor": actor,
        }
    return out


def request_job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
    """
    Execution plane entrypoint.

    Contracts:
      - Always logs: job_requested + (job_started/job_finished/job_failed/job_rejected_busy)
      - Rules snapshot is captured once per request (rules_version/rules_source attached to all events)
      - No Telegram here (transport is outside analyzers)
      - No /tmp locks
    """
    jt = (job_type or "").strip()
    job_id = uuid.uuid4().hex[:12]
    actor_d = actor.to_dict()

    # 1) Capture rules snapshot (this is your execution determinism anchor)
    rs = get_rules_snapshot(force_sync=force_rules_sync)

    append_event(
        type="job_requested",
        job_id=job_id,
        job_type=jt,
        actor=actor_d,
        rules_version=rs.rules_version,
        rules_source=rs.source,
    )

    fn = JOB_REGISTRY.get(jt)
    if fn is None:
        append_event(
            type="job_failed",
            job_id=job_id,
            job_type=jt,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
            payload={"err": "unknown_job_type"},
        )
        return job_id

    if not _try_lock(jt):
        append_event(
            type="job_rejected_busy",
            job_id=job_id,
            job_type=jt,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
        )
        return job_id

    started = time.time()
    _RUNNING[jt] = (job_id, started, actor_d)

    append_event(
        type="job_started",
        job_id=job_id,
        job_type=jt,
        actor=actor_d,
        rules_version=rs.rules_version,
        rules_source=rs.source,
    )

    try:
        # job itself may append job_skipped_* events (no Telegram!)
        fn()

        append_event(
            type="job_finished",
            job_id=job_id,
            job_type=jt,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
            payload={"runtime_sec": round(time.time() - started, 3)},
        )
    except Exception as e:
        append_event(
            type="job_failed",
            job_id=job_id,
            job_type=jt,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
            payload={"err": str(e), "runtime_sec": round(time.time() - started, 3)},
        )
        raise
    finally:
        _RUNNING.pop(jt, None)
        _unlock(jt)

    return job_id