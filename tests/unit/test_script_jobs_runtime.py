from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import integrations.script_jobs  # noqa: F401
from core import job_runner
from core.job_runner import Actor, JOB_REGISTRY, request_job
from integrations.script_jobs.runtime import (
    UnknownScriptError,
    build_execution_context,
    run_script,
)
from integrations.script_jobs.types import ScriptExecutionContext, ScriptResult


class _FakeRulesWorkbook:
    rules_version = "test-rules-version"
    source = "test-source"


@pytest.fixture(autouse=True)
def _patch_rules(monkeypatch: pytest.MonkeyPatch):
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


@pytest.fixture(autouse=True)
def _mock_script_job_params(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "integrations.script_jobs.runtime.get_job_params",
        lambda **kwargs: {},
    )


def test_run_script_unknown_key_fails_closed():
    ctx = ScriptExecutionContext(
        actor=Actor(kind="cli"),
        script_key="missing",
        job_type="script_job:missing",
        source="cli",
    )
    with pytest.raises(UnknownScriptError):
        run_script("missing", ctx)


@patch("integrations.script_jobs.runtime.deliver_script_result")
def test_run_script_executes_and_returns_result(mock_deliver):
    actor = Actor(kind="tg", chat_id=100, user_id=1)
    ctx = build_execution_context("hello_world", actor)

    result = run_script("hello_world", ctx)

    assert result.status == "ok"
    assert "hello_world ok" in result.text
    mock_deliver.assert_called_once()


@patch("integrations.script_jobs.runtime.deliver_script_result")
def test_run_script_handles_execution_failure(mock_deliver, monkeypatch: pytest.MonkeyPatch):
    from integrations.script_jobs.registry import SCRIPT_REGISTRY
    from integrations.script_jobs.types import ScriptSpec

    def boom(_context):
        raise RuntimeError("boom")

    monkeypatch.setitem(
        SCRIPT_REGISTRY,
        "hello_world",
        ScriptSpec(
            script_key="hello_world",
            command_name="run_script_hello",
            run=boom,
        ),
    )

    actor = Actor(kind="scheduler")
    ctx = build_execution_context("hello_world", actor)
    result = run_script("hello_world", ctx)

    assert result.status == "failed"
    mock_deliver.assert_called_once()


def test_script_lock_paths_are_distinct():
    assert job_runner._lock_path("script_job:hello_world") != job_runner._lock_path(
        "script_job:other"
    )
    assert job_runner._lock_path("wallet") == job_runner._lock_path("wallet")


def test_script_a_does_not_block_script_b(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def make_runner(key: str):
        def _run(actor: Actor) -> None:
            calls.append(key)

        return _run

    monkeypatch.setitem(JOB_REGISTRY, "script_job:hello_world", make_runner("a"))
    monkeypatch.setitem(JOB_REGISTRY, "script_job:other", make_runner("b"))

    request_job("script_job:hello_world", Actor(kind="cli"))
    request_job("script_job:other", Actor(kind="cli"))

    assert calls == ["a", "b"]


def test_same_script_blocks_concurrent_run(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []
    monkeypatch.setattr(
        "core.job_runner.append_event",
        lambda **kwargs: events.append(kwargs.get("type") or ""),
    )
    release = threading.Event()

    def hold(actor: Actor) -> None:
        release.wait(timeout=5)

    monkeypatch.setitem(JOB_REGISTRY, "script_job:hello_world", hold)

    thread = threading.Thread(
        target=lambda: request_job("script_job:hello_world", Actor(kind="cli")),
        daemon=True,
    )
    thread.start()
    time.sleep(0.15)
    request_job("script_job:hello_world", Actor(kind="cli"))
    release.set()
    thread.join(timeout=5)

    assert "job_rejected_busy" in events
    assert events.count("job_started") == 1


@patch("integrations.script_jobs.runtime.deliver_script_result")
def test_request_job_lifecycle_for_script_job(mock_deliver, monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []
    monkeypatch.setattr(
        "core.job_runner.append_event",
        lambda **kwargs: events.append(kwargs.get("type") or ""),
    )

    job_id = request_job("script_job:hello_world", Actor(kind="tg", chat_id=1, user_id=2))

    assert job_id
    assert "job_requested" in events
    assert "job_started" in events
    assert "job_finished" in events
    mock_deliver.assert_called_once()


def test_build_execution_context_manual_has_chat_id():
    actor = Actor(kind="tg", chat_id=555, user_id=9)
    ctx = build_execution_context("hello_world", actor)
    assert ctx.chat_id == 555
    assert ctx.source == "manual"


def test_build_execution_context_scheduled_has_route_from_params():
    actor = Actor(kind="scheduler")
    with patch(
        "integrations.script_jobs.runtime.get_job_params",
        return_value={"telegram_route_report": "platform_hourly_report"},
    ):
        ctx = build_execution_context("hello_world", actor)
    assert ctx.chat_id is None
    assert ctx.source == "scheduled"
    assert ctx.route_key == "platform_hourly_report"
