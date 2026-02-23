# core/job_runner.py
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from core.event_log import append_event
from core.rules_provider import get_rules_snapshot

_LOCK_DIR = Path("/tmp/locks")
_LOCK_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Actor:
    kind: str              # "tg" | "scheduler" | "cli"
    chat_id: Optional[int] = None
    user_id: Optional[int] = None


# registry: job_type -> callable()
JOB_REGISTRY: Dict[str, Callable[[], Any]] = {}

# runtime state (in-memory)
_RUNNING: Dict[str, Tuple[str, float, Dict[str, Any]]] = {}  # job_type -> (job_id, started_ts, actor)


def _lock_path(job_type: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in job_type)
    return _LOCK_DIR / f"{safe}.lock"


def _try_lock(job_type: str) -> bool:
    p = _lock_path(job_type)

    # если файл есть — проверяем жив ли процесс
    if p.exists():
        try:
            pid = int(p.read_text().strip())
            # проверка процесса
            os.kill(pid, 0)
            return False  # процесс жив — занято
        except Exception:
            # процесса нет — stale lock
            try:
                p.unlink()
            except Exception:
                return False

    try:
        with p.open("w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return False


def _unlock(job_type: str) -> None:
    p = _lock_path(job_type)
    try:
        if p.exists():
            pid = p.read_text().strip()
            if pid == str(os.getpid()):
                p.unlink()
    except Exception:
        pass


def get_status() -> Dict[str, Any]:
    # shallow copy for UI
    now = time.time()
    out = {}
    for jt, (jid, started, actor) in _RUNNING.items():
        out[jt] = {"job_id": jid, "started_ts": started, "runtime_sec": round(now - started, 1), "actor": actor}
    return out


def request_job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
    job_id = uuid.uuid4().hex[:12]
    rs = get_rules_snapshot(force_sync=force_rules_sync)

    actor_d = actor.__dict__

    append_event(
        type="job_requested",
        job_id=job_id,
        job_type=job_type,
        actor=actor_d,
        rules_version=rs.rules_version,
        rules_source=rs.source,
    )

    fn = JOB_REGISTRY.get(job_type)
    if fn is None:
        append_event(
            type="job_failed",
            job_id=job_id,
            job_type=job_type,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
            payload={"err": "unknown_job_type"},
        )
        return job_id

    if not _try_lock(job_type):
        append_event(
            type="job_rejected_busy",
            job_id=job_id,
            job_type=job_type,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
        )
        return job_id

    started = time.time()
    _RUNNING[job_type] = (job_id, started, actor_d)

    append_event(
        type="job_started",
        job_id=job_id,
        job_type=job_type,
        actor=actor_d,
        rules_version=rs.rules_version,
        rules_source=rs.source,
    )

    try:
        fn()
        append_event(
            type="job_finished",
            job_id=job_id,
            job_type=job_type,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
            payload={"runtime_sec": round(time.time() - started, 3)},
        )
    except Exception as e:
        append_event(
            type="job_failed",
            job_id=job_id,
            job_type=job_type,
            actor=actor_d,
            rules_version=rs.rules_version,
            rules_source=rs.source,
            payload={"err": str(e), "runtime_sec": round(time.time() - started, 3)},
        )
        raise
    finally:
        _RUNNING.pop(job_type, None)
        _unlock(job_type)

    return job_id