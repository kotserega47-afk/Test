# core/job_runner.py
from __future__ import annotations

import logging
import os
import inspect
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from core.event_log import append_event
from core.rules_provider import get_rules_snapshot, get_snapshot_v2
from core.rules_v2.snapshot_fingerprint import rules_snapshot_fingerprint

log = logging.getLogger(__name__)


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

JOB_REGISTRY: Dict[str, Callable[..., Any]] = {}
_RUNNING: Dict[str, Tuple[str, float, Dict[str, Any]]] = {}


# =============================================================================
# Locks (NO /tmp) — stored in STATE_DIR/locks
# =============================================================================


def _state_dir() -> Path:
    return Path(os.getenv("STATE_DIR", "/data/state"))


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


def _lock_stale_max_age_sec() -> float:
    raw = os.getenv("JOB_LOCK_STALE_SEC", "600").strip()
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 600.0


def _lock_held_by_live_runner(job_type: str, pid: int, lock_path: Path) -> bool:
    """True when another live holder should block this job (not a ghost lock file)."""

    if pid <= 0 or not _pid_alive(pid):
        return False

    # PID-1 containers: after restart the volume may still contain wallet.lock with "1"
    # while the new main process is also PID 1 but is not running the job.
    if pid == os.getpid() and job_type not in _RUNNING:
        return False

    try:
        age = time.time() - lock_path.stat().st_mtime
        if age > _lock_stale_max_age_sec():
            return False
    except OSError:
        pass

    return True


def _try_lock(job_type: str) -> bool:
    p = _lock_path(job_type)

    if p.exists():
        try:
            pid = int(p.read_text(encoding="utf-8").strip())
            if _lock_held_by_live_runner(job_type, pid, p):
                return False
        except Exception:
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


@contextmanager
def exclusive_job(job_type: str, actor: Actor) -> Iterator[str | None]:
    """Hold the job lock for the duration of a run.

    Yields ``None`` when another live run holds the lock (caller must not enqueue).
    Yields ``job_id`` when the lock was acquired.
    """

    jt = (job_type or "").strip()
    if not jt:
        yield None
        return
    if not _try_lock(jt):
        yield None
        return

    job_id = uuid.uuid4().hex[:12]
    started = time.time()
    _RUNNING[jt] = (job_id, started, actor.to_dict())
    try:
        yield job_id
    finally:
        _RUNNING.pop(jt, None)
        _unlock(jt)


def _invoke_job(fn: Callable[..., Any], actor: Actor) -> None:
    """Call registry handler; pass actor when the callable accepts it (script jobs)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        fn()
        return

    if not params:
        fn()
        return

    if "actor" in params:
        fn(actor)
        return

    if len(params) == 1:
        first = next(iter(params.values()))
        ann = first.annotation
        if ann is Actor or ann == "Actor":
            fn(actor)
            return

    fn()


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
    jt = (job_type or "").strip()
    job_id = uuid.uuid4().hex[:12]
    actor_d = actor.to_dict()

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

    # Stage 1 observability only (CONTRACT_V2 §17.3 gap); real pinning requires
    # explicit snapshot/index propagation. Detect active rules snapshot fingerprint drift.
    fp_before: Optional[str] = None
    try:
        snap_before = get_snapshot_v2(force_sync=False)
        fp_before = rules_snapshot_fingerprint(snap_before)
    except Exception:
        log.warning(
            "job_runner: could not read rules snapshot before job; skipping rules-changed telemetry",
            exc_info=True,
        )

    job_exc: Optional[Exception] = None
    try:
        _invoke_job(fn, actor)
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
        job_exc = e
        append_event(
            type="job_failed",
            job_id=job_id,
            job_type=jt,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
            payload={"err": str(e), "runtime_sec": round(time.time() - started, 3)},
        )
    finally:
        if fp_before is not None:
            try:
                snap_after = get_snapshot_v2(force_sync=False)
                fp_after = rules_snapshot_fingerprint(snap_after)
                if fp_after != fp_before:
                    append_event(
                        type="rules_changed_during_job",
                        job_id=job_id,
                        job_type=jt,
                        actor=actor_d,
                        rules_version=rs.rules_version,
                        rules_source=rs.source,
                        payload={
                            "fingerprint_before": fp_before,
                            "fingerprint_after": fp_after,
                        },
                    )
                    log.warning(
                        "job_runner: rules snapshot fingerprint changed during job job_id=%s job_type=%s",
                        job_id,
                        jt,
                    )
            except Exception:
                log.warning(
                    "job_runner: post-job rules fingerprint check failed",
                    exc_info=True,
                )
        _RUNNING.pop(jt, None)
        _unlock(jt)
        if job_exc is not None:
            raise job_exc

    return job_id