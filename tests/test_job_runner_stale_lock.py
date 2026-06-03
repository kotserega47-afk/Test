"""Stale job lock handling (PID-1 containers / persistent STATE_DIR)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core import job_runner
from core.job_runner import Actor, request_job
from core.rules_provider import RulesWorkbookSnapshot
from core.rules_v2.models import MetaInfo, RulesSnapshotV2
from datetime import datetime


@pytest.fixture
def lock_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(root))
    job_runner._RUNNING.clear()
    return root


@pytest.fixture(autouse=True)
def _fast_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_LOCK_STALE_SEC", "60")


@pytest.fixture(autouse=True)
def _no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.job_runner.append_event", lambda **_: None)


@pytest.fixture(autouse=True)
def _mock_rules_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    wb = RulesWorkbookSnapshot(
        local_path="/tmp/rules.xlsx",
        stat_key=(1.0, 1),
        loaded_at_ts=1.0,
        source="test",
        rules_version="test",
    )
    monkeypatch.setattr("core.job_runner.get_rules_snapshot", lambda **_: wb)
    snap_v2 = RulesSnapshotV2(
        meta=MetaInfo(
            ruleset_version="test",
            updated_at=datetime(2026, 6, 3, 12, 0, 0),
            updated_by="test",
        ),
    )
    monkeypatch.setattr("core.job_runner.get_snapshot_v2", lambda **_: snap_v2)


def test_stale_lock_same_pid_not_running_allows_acquire(lock_root: Path) -> None:
    """Ghost lock: pid matches main process but job is not in _RUNNING."""

    lock_file = lock_root / "locks" / "wallet.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(str(os.getpid()), encoding="utf-8")

    ran: list[str] = []

    def _fn() -> None:
        ran.append("ok")

    with patch.dict(job_runner.JOB_REGISTRY, {"wallet": _fn}, clear=False):
        job_id = request_job("wallet", Actor(kind="scheduler"))

    assert ran == ["ok"]
    assert job_id


def test_stale_lock_dead_pid_allows_acquire(lock_root: Path) -> None:
    lock_file = lock_root / "locks" / "wallet.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("999999", encoding="utf-8")

    assert job_runner._try_lock("wallet") is True
    assert lock_file.read_text(encoding="utf-8") == str(os.getpid())


def test_stale_lock_by_age_allows_acquire(lock_root: Path) -> None:
    lock_file = lock_root / "locks" / "wallet.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("999999", encoding="utf-8")
    old = time.time() - 120
    os.utime(lock_file, (old, old))

    assert job_runner._try_lock("wallet") is True


def test_live_lock_same_pid_while_running_blocks(lock_root: Path) -> None:
    lock_file = lock_root / "locks" / "wallet.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    job_runner._RUNNING["wallet"] = ("jid", time.time(), {})

    ran: list[str] = []

    def _fn() -> None:
        ran.append("ok")

    with patch.dict(job_runner.JOB_REGISTRY, {"wallet": _fn}, clear=False):
        request_job("wallet", Actor(kind="scheduler"))

    assert ran == []
    job_runner._RUNNING.pop("wallet", None)


def test_non_stale_lock_rejects_concurrent_job(lock_root: Path) -> None:
    import threading

    events: list[str] = []
    release = threading.Event()

    def hold() -> None:
        release.wait(timeout=5)

    with patch.dict(job_runner.JOB_REGISTRY, {"wallet": hold}, clear=False):
        with patch(
            "core.job_runner.append_event",
            lambda **kwargs: events.append(kwargs.get("type") or ""),
        ):
            t = threading.Thread(
                target=lambda: request_job("wallet", Actor(kind="scheduler")),
                daemon=True,
            )
            t.start()
            time.sleep(0.15)
            request_job("wallet", Actor(kind="scheduler"))
            release.set()
            t.join(timeout=5)

    assert "job_rejected_busy" in events
    assert events.count("job_started") == 1
