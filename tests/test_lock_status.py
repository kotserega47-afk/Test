"""Tests for read-only lock status (Phase 1a)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core import job_runner
from core.lock_status import (
    KNOWN_JOB_TYPES,
    _lock_path,
    _safe_job_name,
    get_lock_status_for_job_types,
)


@pytest.fixture
def lock_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(root))
    return root


def test_missing_lock_returns_none(lock_root: Path) -> None:
    info = get_lock_status_for_job_types(("wallet",))
    assert info["wallet"]["lock_age_sec"] == "none"
    assert info["wallet"]["lock_pid"] == "none"


def test_existing_lock_returns_pid_and_age(lock_root: Path) -> None:
    lock_file = lock_root / "locks" / "wallet.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("4242", encoding="utf-8")
    past = time.time() - 10
    os.utime(lock_file, (past, past))

    info = get_lock_status_for_job_types(("wallet",))
    assert info["wallet"]["lock_pid"] == 4242
    assert isinstance(info["wallet"]["lock_age_sec"], float)
    assert info["wallet"]["lock_age_sec"] >= 9.0


def test_invalid_pid_returns_unknown(lock_root: Path) -> None:
    lock_file = lock_root / "locks" / "hourly.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("not-a-pid", encoding="utf-8")

    info = get_lock_status_for_job_types(("hourly",))
    assert info["hourly"]["lock_pid"] == "unknown"
    assert isinstance(info["hourly"]["lock_age_sec"], float)


def test_stat_failure_returns_unknown(lock_root: Path) -> None:
    lock_file = lock_root / "locks" / "rate.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("1", encoding="utf-8")

    with patch.object(Path, "stat", side_effect=OSError("fail")):
        info = get_lock_status_for_job_types(("rate",))
    assert info["rate"]["lock_age_sec"] == "unknown"


def test_no_mkdir_on_read(lock_root: Path) -> None:
    assert not (lock_root / "locks").exists()
    get_lock_status_for_job_types(KNOWN_JOB_TYPES)
    assert not (lock_root / "locks").exists()


def test_safe_name_parity_with_job_runner() -> None:
    samples = ["wallet", "hourly", "rate", "download", "job/type", "weird name"]
    for name in samples:
        assert _safe_job_name(name) == job_runner._safe_job_name(name)
        assert _lock_path(name).name == job_runner._lock_path(name).name
