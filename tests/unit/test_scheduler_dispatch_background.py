"""WALLET-HANG-PATCH-B: scheduler non-blocking job dispatch."""
from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core import job_dispatch
from core import job_runner
from core.job_dispatch import (
    _reset_job_executor_for_tests,
    dispatch_job_background,
    dispatch_job_sync,
    get_job_executor,
)
from core.job_runner import Actor, request_job
from core.rules_provider import RulesWorkbookSnapshot
from core.rules_v2.models import MetaInfo, RulesSnapshotV2
from datetime import datetime


@pytest.fixture(autouse=True)
def _clean_executor() -> None:
    _reset_job_executor_for_tests()
    yield
    _reset_job_executor_for_tests()


@pytest.fixture(autouse=True)
def _dispatch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_DISPATCH_VIA_EXECUTOR", "1")


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
    monkeypatch.setattr("core.job_runner.append_event", lambda **_: None)


def test_dispatch_job_background_returns_without_waiting() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_job(*_a, **_k) -> str:
        started.set()
        release.wait(timeout=5)
        return "jid-slow"

    with patch("core.job_dispatch.request_job", side_effect=slow_job):
        caller = threading.current_thread()
        dispatch_job_background("wallet", Actor(kind="scheduler"))
        assert caller is threading.current_thread()
        assert started.wait(timeout=2)

    release.set()
    get_job_executor().shutdown(wait=True)
    _reset_job_executor_for_tests()


def test_dispatch_job_background_logs_future_exception(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="core.job_dispatch")

    def boom(*_a, **_k) -> str:
        raise RuntimeError("worker boom")

    with patch("core.job_dispatch.request_job", side_effect=boom):
        dispatch_job_background("wallet", Actor(kind="scheduler"))
        deadline = time.time() + 3
        while time.time() < deadline:
            if any("scheduled job worker failed" in r.message for r in caplog.records):
                break
            time.sleep(0.05)
        else:
            pytest.fail("expected callback log for future exception")

    get_job_executor().shutdown(wait=True)
    _reset_job_executor_for_tests()


def test_scheduled_overlap_gets_job_rejected_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "core.job_runner.append_event",
        lambda **kwargs: events.append(kwargs.get("type") or ""),
    )
    release = threading.Event()

    def hold() -> None:
        release.wait(timeout=5)

    monkeypatch.setitem(job_runner.JOB_REGISTRY, "wallet", hold)

    dispatch_job_background("wallet", Actor(kind="scheduler"))
    time.sleep(0.2)
    request_job("wallet", Actor(kind="scheduler"))

    release.set()
    get_job_executor().shutdown(wait=True)
    _reset_job_executor_for_tests()

    assert events.count("job_started") == 1
    assert "job_rejected_busy" in events


def test_dispatch_job_sync_waits_on_future_result() -> None:
    mock_future = MagicMock()
    mock_future.result.return_value = "jid"

    with patch.object(get_job_executor(), "submit", return_value=mock_future) as submit:
        job_id = dispatch_job_sync("wallet", Actor(kind="tg"))

    assert job_id == "jid"
    submit.assert_called_once()
    mock_future.result.assert_called_once()


def test_dispatch_job_background_does_not_wait_on_future_result() -> None:
    mock_future = MagicMock()

    with patch.object(get_job_executor(), "submit", return_value=mock_future) as submit:
        dispatch_job_background("wallet", Actor(kind="scheduler"))

    submit.assert_called_once()
    mock_future.result.assert_not_called()
    assert mock_future.add_done_callback.call_count == 1


def test_schedule_loop_ticks_while_background_job_holds() -> None:
    import scheduler

    tick_count = 0

    def count_tick() -> None:
        nonlocal tick_count
        tick_count += 1

    class _Schedule:
        job_type = "wallet"
        schedule_type = "every_seconds"
        every_seconds = 1
        cron = ""

    sleep_calls = 0

    def fake_sleep(_sec: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 3:
            raise KeyboardInterrupt

    time_calls = {"n": 0}

    def fake_time() -> float:
        time_calls["n"] += 1
        if time_calls["n"] == 1:
            return 0.0
        if time_calls["n"] == 2:
            return 2.0
        return 100.0

    def noop_background(job_type: str, actor, **kwargs) -> None:
        pass

    with patch("scheduler.load_schedules", return_value=[_Schedule()]):
        with patch("scheduler.record_tick", side_effect=count_tick):
            with patch("scheduler.record_schedules_loaded"):
                with patch("scheduler.dispatch_job_background", side_effect=noop_background):
                    with patch("scheduler.time.sleep", side_effect=fake_sleep):
                        with patch("scheduler.time.time", side_effect=fake_time):
                            with pytest.raises(KeyboardInterrupt):
                                scheduler.schedule_loop()

    assert tick_count >= 2
