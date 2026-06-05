"""Linux process/thread observability for PID-1 / Playwright zombie incidents."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_LOG_PREFIX = "[ProcessResource/health]"
_DEFAULT_LOG_INTERVAL_SECONDS = 600
_DEFAULT_ZOMBIE_CHROME_CRITICAL = 20
_DEFAULT_THREAD_CRITICAL = 800
_DEFAULT_PROCESS_CRITICAL = 900

_last_log_at: float | None = None
_lock = threading.Lock()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def log_interval_seconds() -> int:
    return _env_int("PROCESS_RESOURCE_HEALTH_LOG_INTERVAL_SECONDS", _DEFAULT_LOG_INTERVAL_SECONDS)


def zombie_chrome_critical_threshold() -> int:
    return _env_int("ZOMBIE_CHROME_CRITICAL_THRESHOLD", _DEFAULT_ZOMBIE_CHROME_CRITICAL)


def thread_count_critical_threshold() -> int:
    return _env_int("THREAD_COUNT_CRITICAL_THRESHOLD", _DEFAULT_THREAD_CRITICAL)


def process_count_critical_threshold() -> int:
    return _env_int("PROCESS_COUNT_CRITICAL_THRESHOLD", _DEFAULT_PROCESS_CRITICAL)


def _parse_proc_stat(stat_text: str) -> tuple[str, str]:
    """Return (comm, state) from /proc/pid/stat."""
    lparen = stat_text.find("(")
    rparen = stat_text.rfind(")")
    if lparen < 0 or rparen <= lparen:
        return "", ""
    comm = stat_text[lparen + 1 : rparen]
    tail = stat_text[rparen + 2 :].split()
    state = tail[0] if tail else ""
    return comm, state


def collect_process_resource_snapshot(proc_root: Path | None = None) -> dict[str, Any]:
    """
    Count processes, zombies, chrome-headless zombies, and this process's thread count.

    On non-Linux or missing /proc returns zeros and thread_count from threading.active_count().
    """
    root = proc_root if proc_root is not None else Path("/proc")
    snapshot: dict[str, Any] = {
        "process_count": 0,
        "zombie_count": 0,
        "zombie_chrome_count": 0,
        "thread_count": threading.active_count(),
    }

    if not root.is_dir():
        return snapshot

    process_count = 0
    zombie_count = 0
    zombie_chrome = 0

    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        process_count += 1
        try:
            comm, state = _parse_proc_stat(entry.joinpath("stat").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if state != "Z":
            continue
        zombie_count += 1
        comm_lower = comm.lower()
        if "chrome" in comm_lower or "headless" in comm_lower:
            zombie_chrome += 1

    snapshot["process_count"] = process_count
    snapshot["zombie_count"] = zombie_count
    snapshot["zombie_chrome_count"] = zombie_chrome

    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8", errors="replace")
        for line in status.splitlines():
            if line.startswith("Threads:"):
                snapshot["thread_count"] = int(line.split(":", 1)[1].strip())
                break
    except (OSError, ValueError):
        pass

    return snapshot


def log_process_resource_health_if_due(*, force: bool = False) -> dict[str, Any] | None:
    """Periodic INFO log; CRITICAL when thresholds exceeded. Never raises."""
    global _last_log_at
    now = time.monotonic()
    interval = log_interval_seconds()

    with _lock:
        if not force and _last_log_at is not None and (now - _last_log_at) < interval:
            return None
        _last_log_at = now

    try:
        snap = collect_process_resource_snapshot()
    except Exception:
        log.exception("%s snapshot collection failed", _LOG_PREFIX)
        return None

    zc = int(snap["zombie_chrome_count"])
    z = int(snap["zombie_count"])
    threads = int(snap["thread_count"])
    procs = int(snap["process_count"])

    level = logging.INFO
    status = "OK"
    if (
        zc >= zombie_chrome_critical_threshold()
        or threads >= thread_count_critical_threshold()
        or procs >= process_count_critical_threshold()
    ):
        level = logging.CRITICAL
        status = "CRITICAL"

    log.log(
        level,
        "%s status=%s process_count=%s thread_count=%s zombie_count=%s zombie_chrome_count=%s",
        _LOG_PREFIX,
        status,
        procs,
        threads,
        z,
        zc,
    )
    return snap


def _reset_process_resource_health_for_tests() -> None:
    global _last_log_at
    with _lock:
        _last_log_at = None
