# core/job_runner.py
from __future__ import annotations

import inspect
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
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
#
# Mutual exclusion is local to this machine (or replicas that share STATE_DIR
# on the same filesystem). It is not a distributed lock across independent
# replicas with separate disks.
#
# Acquire is atomic for threads (per-job threading.Lock) and OS processes
# (fcntl.flock / msvcrt.locking on an open FD). Liveness is the OS lock, not
# file mtime: a live holder is never stolen because JOB_LOCK_STALE_SEC elapsed.
# Crash / PID-1 container restart releases the OS lock with the process.
# Unlock only releases a lock this process currently holds.


@dataclass
class _LockHolder:
    fd: int
    token: str
    thread_lock: threading.Lock
    path: Path


_HOLDERS: Dict[str, _LockHolder] = {}
_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


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


def _meta_path(lock_path: Path) -> Path:
    return lock_path.with_name(lock_path.name + ".meta")


def _lock_stale_max_age_sec() -> float:
    """Observability helper. Not used to steal a live OS lock."""
    raw = os.getenv("JOB_LOCK_STALE_SEC", "600").strip()
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 600.0


def _thread_lock_for(job_type: str) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(job_type)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[job_type] = lock
        return lock


def _open_lock_fd(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOINHERIT"):
        flags |= os.O_NOINHERIT
    fd = os.open(path, flags, 0o644)
    if os.name != "nt":
        try:
            import fcntl  # type: ignore[import-untyped]

            fcntl.fcntl(fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)  # type: ignore[attr-defined]
        except Exception:
            pass
    return fd


def _os_try_lock(fd: int) -> bool:
    if os.fstat(fd).st_size < 1:
        os.write(fd, b" ")
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl  # type: ignore[import-untyped]

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        return True
    except (BlockingIOError, OSError):
        return False


def _os_unlock(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl  # type: ignore[import-untyped]

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except OSError:
        pass


def _write_holder_record(fd: int, path: Path, token: str) -> None:
    payload = json.dumps(
        {"pid": os.getpid(), "token": token, "acquired_at": time.time()},
        separators=(",", ":"),
    )
    encoded = payload.encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        os.write(fd, encoded)
        os.ftruncate(fd, len(encoded))
        os.fsync(fd)
    except OSError:
        pass
    _meta_path(path).write_text(payload + "\n", encoding="utf-8")


def _try_lock(job_type: str) -> bool:
    jt = (job_type or "").strip()
    if not jt:
        return False
    if jt in _HOLDERS or jt in _RUNNING:
        return False

    tlock = _thread_lock_for(jt)
    if not tlock.acquire(blocking=False):
        return False

    fd: int | None = None
    try:
        path = _lock_path(jt)
        fd = _open_lock_fd(path)
        if not _os_try_lock(fd):
            os.close(fd)
            tlock.release()
            return False
        token = uuid.uuid4().hex
        _write_holder_record(fd, path, token)
        _HOLDERS[jt] = _LockHolder(fd=fd, token=token, thread_lock=tlock, path=path)
        return True
    except Exception:
        if fd is not None:
            try:
                _os_unlock(fd)
            except Exception:
                pass
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            tlock.release()
        except RuntimeError:
            pass
        return False


def _unlock(job_type: str) -> None:
    """Release only the lock held by this process for ``job_type``."""
    jt = (job_type or "").strip()
    holder = _HOLDERS.pop(jt, None)
    if holder is None:
        return
    try:
        _os_unlock(holder.fd)
    except Exception:
        pass
    try:
        os.close(holder.fd)
    except Exception:
        pass
    try:
        holder.path.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        _meta_path(holder.path).unlink(missing_ok=True)
    except Exception:
        pass
    try:
        holder.thread_lock.release()
    except RuntimeError:
        pass


@contextmanager
def exclusive_job(job_type: str, actor: Actor) -> Iterator[str | None]:
    """Hold the job lock for the duration of a run.

    Yields ``None`` when another live run holds the lock (caller must not enqueue).
    Yields ``job_id`` when the lock was acquired.

    The lock is local to ``STATE_DIR`` on this filesystem. Independent Railway
    replicas without a shared volume do not see each other's locks. Cancelling
    a Telegram coroutine does not unlock while the worker thread still holds it.
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
