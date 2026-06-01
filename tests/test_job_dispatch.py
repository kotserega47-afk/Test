"""Tests for shared job dispatch (Phase 3a / Variant B)."""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest

from core.job_dispatch import (
    _reset_job_executor_for_tests,
    dispatch_job_async,
    dispatch_job_sync,
    get_job_executor,
)
from core.job_runner import Actor


@pytest.fixture(autouse=True)
def _clean_executor() -> None:
    _reset_job_executor_for_tests()
    yield
    _reset_job_executor_for_tests()


@pytest.fixture(autouse=True)
def _dispatch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_DISPATCH_VIA_EXECUTOR", "1")


def test_dispatch_enabled_uses_job_executor() -> None:
    seen: dict[str, threading.Thread] = {}

    def fake_request_job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        seen["thread"] = threading.current_thread()
        return "job-1"

    with patch("core.job_dispatch.request_job", side_effect=fake_request_job):
        caller = threading.current_thread()
        job_id = dispatch_job_sync("wallet", Actor(kind="scheduler"))

    assert job_id == "job-1"
    assert seen["thread"].name.startswith("job-worker")
    assert seen["thread"] is not caller


def test_dispatch_disabled_calls_request_job_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_DISPATCH_VIA_EXECUTOR", "0")

    with patch("core.job_dispatch.request_job", return_value="legacy") as req:
        job_id = dispatch_job_sync("hourly", Actor(kind="scheduler"))

    assert job_id == "legacy"
    req.assert_called_once()


def test_dispatch_async_disabled_uses_default_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_DISPATCH_VIA_EXECUTOR", "0")

    async def run() -> None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result("legacy-async")

        with patch("core.job_dispatch.request_job", return_value="legacy-async"):
            with patch.object(loop, "run_in_executor", return_value=fut) as rie:
                job_id = await dispatch_job_async("wallet", Actor(kind="tg"))

        assert job_id == "legacy-async"
        assert rie.call_args.args[0] is None

    asyncio.run(run())


def test_dispatch_async_uses_shared_job_executor() -> None:
    async def run() -> None:
        executor = get_job_executor()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result("async-1")

        with patch("core.job_dispatch.request_job", return_value="async-1"):
            with patch.object(loop, "run_in_executor", return_value=fut) as rie:
                job_id = await dispatch_job_async("hourly", Actor(kind="tg"))

        assert job_id == "async-1"
        assert rie.call_args.args[0] is executor

    asyncio.run(run())


def test_get_job_executor_singleton() -> None:
    a = get_job_executor()
    b = get_job_executor()
    assert a is b
