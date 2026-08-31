"""OS-level job lock: threads, processes, crash, old mtime."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Barrier, Thread

import pytest

from core import job_runner
from core.job_runner import Actor, exclusive_job

CHILD = Path(__file__).with_name("_job_lock_child.py")
ROOT = Path(__file__).resolve().parents[1]


def _payload(lock_root: Path) -> dict:
    meta = lock_root / "locks" / "wallet.lock.meta"
    lock = lock_root / "locks" / "wallet.lock"
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    return json.loads(lock.read_text(encoding="utf-8"))


def _spawn(
    lock_root: Path,
    action: str,
    flag: Path,
    *,
    wait: bool = False,
) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, str(CHILD), str(lock_root), action, "wallet", str(flag)],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if wait:
        proc.wait(timeout=15)
    return proc


def _wait_flag(flag: Path, timeout: float = 10) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if flag.exists() and flag.stat().st_size:
            return flag.read_text(encoding="utf-8").strip()
        time.sleep(0.05)
    raise AssertionError(f"flag not written: {flag}")


@pytest.fixture
def lock_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(root))
    job_runner._RUNNING.clear()
    for jt in list(job_runner._HOLDERS):
        job_runner._unlock(jt)
    yield root
    for jt in list(job_runner._HOLDERS):
        job_runner._unlock(jt)
    job_runner._RUNNING.clear()


def test_live_lock_old_mtime_stays_busy(lock_root: Path) -> None:
    assert job_runner._try_lock("wallet") is True
    path = lock_root / "locks" / "wallet.lock"
    old = time.time() - 1000
    os.utime(path, (old, old))
    assert job_runner._try_lock("wallet") is False
    job_runner._unlock("wallet")


def test_two_threads_exactly_one_holder(lock_root: Path) -> None:
    barrier = Barrier(2)
    acquired: list[bool] = []

    def worker() -> None:
        barrier.wait(timeout=5)
        acquired.append(job_runner._try_lock("wallet"))

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert acquired.count(True) == 1
    assert acquired.count(False) == 1
    job_runner._unlock("wallet")


def test_second_os_process_sees_busy(lock_root: Path) -> None:
    flag = lock_root / "hold.flag"
    proc = _spawn(lock_root, "hold", flag)
    assert _wait_flag(flag) == "1"
    assert job_runner._try_lock("wallet") is False
    assert proc.stdin is not None
    proc.stdin.write(b"x")
    proc.stdin.close()
    assert proc.wait(timeout=10) == 0
    assert job_runner._try_lock("wallet") is True
    job_runner._unlock("wallet")


def test_crashed_holder_allows_next_process(lock_root: Path) -> None:
    flag = lock_root / "crash.flag"
    proc = _spawn(lock_root, "crash", flag)
    assert _wait_flag(flag) == "1"
    proc.wait(timeout=10)
    assert proc.returncode == 1
    deadline = time.time() + 3
    got = False
    while time.time() < deadline:
        if job_runner._try_lock("wallet"):
            got = True
            break
        time.sleep(0.05)
    assert got is True
    job_runner._unlock("wallet")


def test_foreign_unlock_does_not_drop_holder(lock_root: Path) -> None:
    assert job_runner._try_lock("wallet") is True
    flag = lock_root / "unlock.flag"
    proc = _spawn(lock_root, "unlock", flag, wait=True)
    assert proc.returncode == 0
    assert _wait_flag(flag) == "called"
    assert job_runner._try_lock("wallet") is False
    try_flag = lock_root / "try.flag"
    other = _spawn(lock_root, "try", try_flag, wait=True)
    assert other.returncode == 0
    assert _wait_flag(try_flag) == "0"
    job_runner._unlock("wallet")


def test_exclusive_job_exception_releases_lock(lock_root: Path) -> None:
    actor = Actor(kind="cli")
    with pytest.raises(RuntimeError, match="boom"):
        with exclusive_job("wallet", actor) as job_id:
            assert job_id
            raise RuntimeError("boom")
    assert job_runner._try_lock("wallet") is True
    job_runner._unlock("wallet")


def test_lock_record_is_json_pid(lock_root: Path) -> None:
    assert job_runner._try_lock("wallet") is True
    payload = _payload(lock_root)
    assert payload["pid"] == os.getpid()
    assert payload["token"]
    job_runner._unlock("wallet")
