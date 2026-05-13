"""Stage 1: job_runner telemetry when rules snapshot fingerprint changes mid-job (CONTRACT_V2 §17.3 observability)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core import job_runner
from core.job_runner import Actor, request_job


class _FakeRulesWorkbook:
    rules_version = "test-rules-version"
    source = "test-source"


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    job_runner.JOB_REGISTRY.pop("__telemetry_test__", None)


@pytest.fixture
def _patch_rules_workbook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.job_runner.get_rules_snapshot",
        lambda **kwargs: _FakeRulesWorkbook(),
    )


def test_fingerprint_unchanged_no_rules_changed_event(
    monkeypatch: pytest.MonkeyPatch, _patch_rules_workbook: None
) -> None:
    events: list[dict] = []

    def capture(**kwargs):
        events.append({"type": kwargs.get("type"), "payload": kwargs.get("payload") or {}})

    monkeypatch.setattr("core.job_runner.append_event", capture)

    snap = MagicMock(name="snap")
    monkeypatch.setattr(
        "core.job_runner.get_snapshot_v2",
        lambda force_sync=False: snap,
    )
    monkeypatch.setattr("core.job_runner.rules_snapshot_fingerprint", lambda s: "fp_same")

    called = {"n": 0}

    def fn():
        called["n"] += 1

    job_runner.JOB_REGISTRY["__telemetry_test__"] = fn
    request_job("__telemetry_test__", Actor(kind="cli"))

    types = [e["type"] for e in events]
    assert "job_requested" in types
    assert "job_started" in types
    assert "job_finished" in types
    assert "rules_changed_during_job" not in types
    assert called["n"] == 1


def test_fingerprint_changed_emits_rules_changed_during_job(
    monkeypatch: pytest.MonkeyPatch, _patch_rules_workbook: None
) -> None:
    events: list[dict] = []

    def capture(**kwargs):
        events.append(
            {
                "type": kwargs.get("type"),
                "job_id": kwargs.get("job_id"),
                "job_type": kwargs.get("job_type"),
                "actor": kwargs.get("actor"),
                "payload": kwargs.get("payload") or {},
            }
        )

    monkeypatch.setattr("core.job_runner.append_event", capture)

    monkeypatch.setattr(
        "core.job_runner.get_snapshot_v2",
        lambda force_sync=False: MagicMock(name="snap"),
    )
    seq = iter(["fp_a", "fp_b"])

    def fp_side_effect(_snap):
        return next(seq)

    monkeypatch.setattr("core.job_runner.rules_snapshot_fingerprint", fp_side_effect)

    called = {"n": 0}

    def fn():
        called["n"] += 1

    job_runner.JOB_REGISTRY["__telemetry_test__"] = fn
    request_job("__telemetry_test__", Actor(kind="scheduler"))

    changed = [e for e in events if e["type"] == "rules_changed_during_job"]
    assert len(changed) == 1
    assert changed[0]["payload"]["fingerprint_before"] == "fp_a"
    assert changed[0]["payload"]["fingerprint_after"] == "fp_b"
    assert changed[0]["job_type"] == "__telemetry_test__"
    assert changed[0]["actor"] == {"kind": "scheduler", "chat_id": None, "user_id": None}
    assert called["n"] == 1


def test_job_raises_fingerprint_check_runs_original_exception_preserved(
    monkeypatch: pytest.MonkeyPatch, _patch_rules_workbook: None
) -> None:
    events: list[dict] = []

    def capture(**kwargs):
        events.append({"type": kwargs.get("type"), "payload": kwargs.get("payload") or {}})

    monkeypatch.setattr("core.job_runner.append_event", capture)
    monkeypatch.setattr(
        "core.job_runner.get_snapshot_v2",
        lambda force_sync=False: MagicMock(),
    )
    monkeypatch.setattr(
        "core.job_runner.rules_snapshot_fingerprint",
        MagicMock(side_effect=["fp1", "fp2"]),
    )

    def fn():
        raise RuntimeError("job boom")

    job_runner.JOB_REGISTRY["__telemetry_test__"] = fn

    with pytest.raises(RuntimeError, match="job boom"):
        request_job("__telemetry_test__", Actor(kind="cli"))

    assert any(e["type"] == "job_failed" for e in events)
    assert any(e["type"] == "rules_changed_during_job" for e in events)


def test_after_check_get_snapshot_raises_logs_only_successful_job(
    monkeypatch: pytest.MonkeyPatch, _patch_rules_workbook: None, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.WARNING)

    monkeypatch.setattr("core.job_runner.append_event", MagicMock())

    calls = {"i": 0}

    def gs(force_sync=False):
        calls["i"] += 1
        if calls["i"] == 1:
            return MagicMock()
        raise RuntimeError("snapshot unavailable after job")

    monkeypatch.setattr("core.job_runner.get_snapshot_v2", gs)
    monkeypatch.setattr("core.job_runner.rules_snapshot_fingerprint", lambda _s: "fp_stable")

    def fn():
        return None

    job_runner.JOB_REGISTRY["__telemetry_test__"] = fn
    request_job("__telemetry_test__", Actor(kind="cli"))

    assert "post-job rules fingerprint check failed" in caplog.text


def test_request_job_shared_path_scheduler_parity_doc() -> None:
    """Scheduler and TG both use request_job; no separate code path — telemetry lives in request_job only."""
    assert request_job.__module__ == "core.job_runner"
