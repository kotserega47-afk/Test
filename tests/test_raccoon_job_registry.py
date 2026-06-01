"""Phase R2: Raccoon jobs registered in JOB_REGISTRY and request_job lifecycle."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import integrations.raccoon_jobs  # noqa: F401 — registry side effect
import integrations.tg_commands  # noqa: F401 — loads raccoon_jobs via import
from core import job_runner
from core.job_runner import Actor, JOB_REGISTRY, request_job
from core.lock_status import KNOWN_JOB_TYPES, get_lock_status_for_job_types


class _FakeRulesWorkbook:
    rules_version = "test-rules-version"
    source = "test-source"


@pytest.fixture(autouse=True)
def _patch_rules_workbook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.job_runner.get_rules_snapshot",
        lambda **kwargs: _FakeRulesWorkbook(),
    )
    monkeypatch.setattr(
        "core.job_runner.get_snapshot_v2",
        lambda force_sync=False: MagicMock(name="snap"),
    )
    monkeypatch.setattr(
        "core.job_runner.rules_snapshot_fingerprint",
        lambda _snap: "fp_test",
    )


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))


def test_job_registry_contains_raccoon_job_types() -> None:
    for jt in ("raccoon_wallet", "raccoon_hourly", "raccoon_daily_conversion"):
        assert jt in JOB_REGISTRY
        assert callable(JOB_REGISTRY[jt])


def test_known_job_types_includes_raccoon() -> None:
    for jt in ("raccoon_wallet", "raccoon_hourly", "raccoon_daily_conversion"):
        assert jt in KNOWN_JOB_TYPES


def test_lock_status_knows_raccoon_job_types(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    info = get_lock_status_for_job_types(
        ("raccoon_wallet", "raccoon_hourly", "raccoon_daily_conversion")
    )
    for jt in ("raccoon_wallet", "raccoon_hourly", "raccoon_daily_conversion"):
        assert jt in info
        assert info[jt]["lock_pid"] == "none"


def test_request_job_raccoon_wallet_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "core.job_runner.append_event",
        lambda **kwargs: events.append(kwargs.get("type") or ""),
    )
    with patch("integrations.raccoon_jobs.run_raccoon_wallet_cycle") as cycle:
        job_id = request_job("raccoon_wallet", Actor(kind="cli"))
    assert job_id
    cycle.assert_called_once()
    assert events[0] == "job_requested"
    assert "job_started" in events
    assert "job_finished" in events


def test_request_job_raccoon_hourly_calls_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "core.job_runner.append_event",
        lambda **kwargs: events.append(kwargs.get("type") or ""),
    )
    with patch("integrations.raccoon_jobs.run_hourly_raccoon_cycle") as dl:
        with patch("integrations.raccoon_jobs.run_conversion_monitor_from_payin") as monitor:
            with patch("integrations.raccoon_jobs.run_raccoon_hourly_report_fn") as report:
                request_job("raccoon_hourly", Actor(kind="scheduler"))
    dl.assert_called_once()
    monitor.assert_called_once()
    report.assert_called_once()
    assert "job_finished" in events


def test_request_job_raccoon_daily_conversion_calls_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "core.job_runner.append_event",
        lambda **kwargs: events.append(kwargs.get("type") or ""),
    )
    with patch("integrations.raccoon_jobs.run_daily_conversion_report") as conv:
        request_job("raccoon_daily_conversion", Actor(kind="scheduler"))
    conv.assert_called_once_with("/tmp/hourly_raccoon/payin.xlsx")
    assert "job_finished" in events


def test_wallet_and_raccoon_wallet_use_separate_lock_paths() -> None:
    assert job_runner._lock_path("wallet") != job_runner._lock_path("raccoon_wallet")


def test_wallet_and_raccoon_wallet_both_run_sequentially(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setitem(JOB_REGISTRY, "wallet", lambda: calls.append("wallet"))
    monkeypatch.setitem(JOB_REGISTRY, "raccoon_wallet", lambda: calls.append("raccoon_wallet"))
    request_job("wallet", Actor(kind="cli"))
    request_job("raccoon_wallet", Actor(kind="cli"))
    assert calls == ["wallet", "raccoon_wallet"]


def test_raccoon_wallet_busy_rejects_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "core.job_runner.append_event",
        lambda **kwargs: events.append(kwargs.get("type") or ""),
    )
    release = threading.Event()

    def hold_lock() -> None:
        release.wait(timeout=5)

    monkeypatch.setitem(JOB_REGISTRY, "raccoon_wallet", hold_lock)

    t = threading.Thread(
        target=lambda: request_job("raccoon_wallet", Actor(kind="cli")),
        daemon=True,
    )
    t.start()
    time.sleep(0.15)
    request_job("raccoon_wallet", Actor(kind="cli"))
    release.set()
    t.join(timeout=5)

    assert "job_rejected_busy" in events
    assert events.count("job_started") == 1
