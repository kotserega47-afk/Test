"""Tests that scheduler uses non-blocking dispatch_job_background."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

import scheduler


def test_scheduler_source_uses_background_dispatch_not_request_job() -> None:
    src = Path(scheduler.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.append(node.func.id)
    assert "dispatch_job_background" in names
    assert "dispatch_job_sync" not in names
    assert "request_job" not in names


def test_schedule_loop_calls_dispatch_job_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_DISPATCH_VIA_EXECUTOR", "1")

    calls: list[tuple[str, str]] = []

    def fake_dispatch(job_type: str, actor, **kwargs) -> None:
        calls.append((job_type, actor.kind))

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

    with patch("scheduler.load_schedules", return_value=[_Schedule()]):
        with patch("scheduler.record_tick") as record_tick:
            with patch("scheduler.record_schedules_loaded"):
                with patch("scheduler.dispatch_job_background", side_effect=fake_dispatch):
                    with patch("scheduler.time.sleep", side_effect=fake_sleep):
                        with patch("scheduler.time.time", side_effect=fake_time):
                            with pytest.raises(KeyboardInterrupt):
                                scheduler.schedule_loop()

    assert calls == [("wallet", "scheduler")]
    assert record_tick.call_count >= 2
