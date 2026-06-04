"""JOB-HEALTH-GUARD-V2-C1 — observe-only job health + wallet progress hooks."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from core import job_runner
from core.job_health import (
    _reset_job_health_for_tests,
    evaluate_job_health,
    format_job_health_lines,
    job_health_guard_enabled,
    job_health_recovery_mode,
)
from core.job_progress import _reset_job_progress_for_tests, record_progress


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    _reset_job_progress_for_tests()
    _reset_job_health_for_tests()
    job_runner._RUNNING.clear()
    yield
    _reset_job_progress_for_tests()
    _reset_job_health_for_tests()
    job_runner._RUNNING.clear()


@pytest.fixture
def guard_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_HEALTH_GUARD_ENABLED", "1")
    monkeypatch.setenv("JOB_HEALTH_RECOVERY_MODE", "observe")
    monkeypatch.setenv("JOB_HEALTH_TICK_INTERVAL_SEC", "0")
    monkeypatch.setenv("JOB_HEALTH_WARNING_SECONDS", "600")
    monkeypatch.setenv("JOB_HEALTH_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("JOB_HEALTH_PROGRESS_TIMEOUT_SECONDS", "600")


def test_guard_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOB_HEALTH_GUARD_ENABLED", raising=False)
    assert job_health_guard_enabled() is False
    lines = format_job_health_lines()
    assert lines[0] == "job_health:"
    assert "- mode=off" in lines


def test_wallet_running_ok_with_progress(guard_on: None) -> None:
    started = time.time() - 30
    job_runner._RUNNING["wallet"] = ("jid1", started, {"kind": "scheduler"})
    record_progress("wallet", "payin_export_click")
    time.sleep(0.05)

    snap = evaluate_job_health()
    wallet = snap["jobs"]["wallet"]
    assert wallet["state"] == "running_ok"
    assert wallet["stage"] == "payin_export_click"
    assert isinstance(wallet["progress_age_sec"], float)
    assert wallet["progress_age_sec"] < 5

    text = "\n".join(format_job_health_lines())
    assert "job_health:" in text
    assert "- mode=observe" in text
    assert "wallet: state=running_ok" in text
    assert "stage=payin_export_click" in text
    assert "progress_age=" in text


def test_wallet_idle_when_not_running(guard_on: None) -> None:
    record_progress("wallet", "payin_export_click")
    snap = evaluate_job_health()
    assert snap["jobs"]["wallet"]["state"] == "idle"
    assert snap["jobs"]["wallet"]["stage"] == "none"


def test_wallet_stuck_when_progress_stale(guard_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.job_health._progress_timeout_seconds", lambda: 0.5)
    started = time.time() - 10
    job_runner._RUNNING["wallet"] = ("jid1", started, {"kind": "scheduler"})
    record_progress("wallet", "payin_goto_start")
    time.sleep(0.6)

    snap = evaluate_job_health()
    assert snap["jobs"]["wallet"]["state"] == "stuck"


def test_observe_emits_degraded_event(guard_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "core.job_health.append_event",
        lambda **kwargs: events.append(kwargs.get("type") or ""),
    )
    monkeypatch.setattr("core.job_health._progress_timeout_seconds", lambda: 0.5)
    job_runner._RUNNING["wallet"] = ("jid1", time.time() - 5, {"kind": "scheduler"})
    record_progress("wallet", "old_stage")
    time.sleep(0.6)

    from core.job_health import evaluate_job_health_if_due

    evaluate_job_health_if_due()
    assert "job_health_degraded" in events


def test_recovery_mode_observe_only(guard_on: None) -> None:
    assert job_health_recovery_mode() == "observe"


def test_scheduler_calls_evaluate_if_due(guard_on: None) -> None:
    import scheduler

    with patch("scheduler.load_schedules", return_value=[]):
        with patch("scheduler.record_tick") as tick:
            with patch("scheduler.evaluate_job_health_if_due") as eval_due:
                with patch("scheduler.time.sleep", side_effect=KeyboardInterrupt):
                    with pytest.raises(KeyboardInterrupt):
                        scheduler.schedule_loop()
    assert tick.call_count >= 1
    assert eval_due.call_count >= 1


def test_wallet_stage_records_progress() -> None:
    from integrations.downloader_wallets import _wallet_stage
    from core.job_progress import get_progress

    _wallet_stage("payin_export_click")
    prog = get_progress("wallet")
    assert prog is not None
    assert prog[0] == "payin_export_click"
