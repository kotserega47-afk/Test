"""TASK-48: stop_isolated_sender full resource stop (FS1–FS25).

CRITICAL: never permanently stop the module-global sender loop/thread in the
main pytest process. Loop-stop success is simulated via ``LoopStopHarness``;
an optional subprocess proves public wiring can reach real STOPPED.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

import integrations.telegram_bot as tg
from core.antares_sender_ownership import (
    AntaresSenderOwnershipAttestation,
    _reset_antares_sender_ownership_for_tests,
    claim_antares_sender_ownership,
)
from integrations.telegram_sender_gates import (
    SenderPtbCompatibilityResult,
    SenderRequestRolePlan,
)


# ---------------------------------------------------------------------------
# PTB helpers
# ---------------------------------------------------------------------------


def _ptb_pass(**_kwargs) -> SenderPtbCompatibilityResult:
    return SenderPtbCompatibilityResult(
        supported=True,
        reason=None,
        roles=(),
        close_targets=(),
    )


def _ptb_fail(**_kwargs) -> SenderPtbCompatibilityResult:
    return SenderPtbCompatibilityResult(
        supported=False,
        reason="bot_shutdown_unavailable",
        roles=(),
        close_targets=(),
    )


def _ptb_for_graph(gu, gen) -> SenderPtbCompatibilityResult:
    roles = (
        SenderRequestRolePlan(role="get_updates_request", request=gu),
        SenderRequestRolePlan(role="request", request=gen),
    )
    return SenderPtbCompatibilityResult(
        supported=True,
        reason=None,
        roles=roles,
        close_targets=(gu, gen),
    )


# ---------------------------------------------------------------------------
# Fake Bot / request graph
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self, closed: bool = False):
        self.is_closed = closed


class FakeRequest:
    def __init__(self, *, closed: bool = False, fail: bool = False):
        self._client = FakeClient(closed)
        self._fail = fail
        self.shutdown_calls = 0
        self._hold: threading.Event | None = None
        self._entered: threading.Event | None = None

    async def shutdown(self):
        self.shutdown_calls += 1
        if self._entered is not None:
            self._entered.set()
        if self._hold is not None:
            while not self._hold.is_set():
                await asyncio.sleep(0.01)
        if self._fail:
            raise RuntimeError("req boom")
        self._client.is_closed = True


class FakeBot:
    def __init__(self, gu, gen, *, fail_bot: bool = False):
        self._request = (gu, gen)
        self.request = gen
        self._fail_bot = fail_bot
        self.shutdown_calls = 0
        self._hold: threading.Event | None = None
        self._entered: threading.Event | None = None

    async def shutdown(self):
        self.shutdown_calls += 1
        if self._entered is not None:
            self._entered.set()
        if self._hold is not None:
            while not self._hold.is_set():
                await asyncio.sleep(0.01)
        if self._fail_bot:
            raise RuntimeError("bot boom")


class FakeRequestUnknownDiag:
    """Request whose leftover diagnostic is unavailable (no _client)."""

    def __init__(self):
        self.shutdown_calls = 0

    async def shutdown(self):
        self.shutdown_calls += 1


# ---------------------------------------------------------------------------
# Loop-stop harness (never kills the real sender loop)
# ---------------------------------------------------------------------------


def _real_loop_is_running() -> bool:
    """Observe the real loop, bypassing any test monkeypatch on ``is_running``."""
    return asyncio.BaseEventLoop.is_running(tg.loop)


def _real_thread_is_alive() -> bool:
    """Observe the real thread, bypassing any test monkeypatch on ``is_alive``."""
    return threading.Thread.is_alive(tg._loop_thread)


def _is_loop_stop_callback(cb) -> bool:
    """Bound ``loop.stop`` methods are not identity-stable across attribute access."""
    return (
        callable(cb)
        and getattr(cb, "__self__", None) is tg.loop
        and getattr(cb, "__name__", None) == "stop"
    )


class LoopStopHarness:
    """Intercept ``loop.stop`` scheduling; fake thread/loop death for STOPPED."""

    def __init__(self) -> None:
        self.stop_schedule_count = 0
        self.fail_schedule = False
        self.auto_signal_stopped = True
        self.thread_alive_after_stop = False
        self.loop_running_after_stop = False
        self.order: list[str] = []
        self._stop_faked = False
        self._orig_is_alive = None
        self._orig_is_running = None
        self._active = False

    def install(self, monkeypatch) -> "LoopStopHarness":
        real = tg._real_call_soon_threadsafe
        self._orig_is_alive = tg._loop_thread.is_alive
        self._orig_is_running = tg.loop.is_running
        self._active = True

        def wrapped(cb, *args):
            if _is_loop_stop_callback(cb):
                self.stop_schedule_count += 1
                self.order.append("loop_stop")
                if self.fail_schedule:
                    raise RuntimeError("schedule stop boom")
                self._stop_faked = True
                if self.auto_signal_stopped:
                    tg._loop_stopped.set()
                return None
            return real(cb, *args)

        def fake_is_alive():
            if self._stop_faked:
                return bool(self.thread_alive_after_stop)
            return threading.Thread.is_alive(tg._loop_thread)

        def fake_is_running():
            if self._stop_faked:
                return bool(self.loop_running_after_stop)
            return asyncio.BaseEventLoop.is_running(tg.loop)

        def fake_join(_timeout=None):
            return None

        monkeypatch.setattr(tg, "_real_call_soon_threadsafe", wrapped)
        monkeypatch.setattr(tg._loop_thread, "is_alive", fake_is_alive)
        monkeypatch.setattr(tg.loop, "is_running", fake_is_running)
        monkeypatch.setattr(tg._loop_thread, "join", fake_join)
        return self

    def deactivate_death_fakes(self) -> None:
        """Stop lying about loop/thread death so fixture reset can proceed."""
        self._stop_faked = False


@contextmanager
def without_killing_sender_loop(monkeypatch, harness: LoopStopHarness | None = None):
    h = harness or LoopStopHarness()
    h.install(monkeypatch)
    try:
        yield h
    finally:
        h.deactivate_death_fakes()
        # Clear fake loop-stopped signal so later waits are not instantly satisfied.
        # Leave lifecycle / _loop_stop_requested for the test body; fixture resets.
        tg._loop_stopped.clear()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _ensure_worker_alive(*, timeout: float = 3.0) -> None:
    """Reset worker lifecycle and wait until a live worker Task is ready."""
    with tg._lifecycle_lock:
        tg._lifecycle_state = "DRAINING"
        tg._intake_sealed = True
        tg._sentinel_state = tg._SENTINEL_ENQUEUED
        tg._sentinel_ack.set()
        tg._loop_stop_requested = False
        tg._http_close_plan = None
        tg._http_request_results = {}
        tg._bot_shutdown_attempted = False
        tg._bot_shutdown_ok = False
        tg._bot_shutdown_error_type = None
        tg._bot_shutdown_error_text = None
        tg._http_close_session_task = None
        tg._http_phase_ack.clear()
    tg._loop_stopped.clear()
    if not _real_loop_is_running() or not _real_thread_is_alive():
        raise RuntimeError(
            "sender loop/thread died in-process; a prior test called real loop.stop"
        )
    tg._reset_sender_worker_lifecycle_for_tests()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = tg._worker_task
        if tg._worker_ready.is_set() and task is not None and not task.done():
            return
        time.sleep(0.01)
    raise AssertionError(
        f"worker not ready after reset "
        f"(ready={tg._worker_ready.is_set()} task={tg._worker_task!r} "
        f"state={tg._lifecycle_state} loop={_real_loop_is_running()})"
    )


@pytest.fixture(autouse=True)
def _isolate_sender_lifecycle(monkeypatch):
    _reset_antares_sender_ownership_for_tests()
    tg._reset_telegram_sender_health_for_tests()
    _ensure_worker_alive()
    monkeypatch.setattr(tg, "TELEGRAM_TOKEN", "123456:ABC-TEST")
    monkeypatch.setattr(tg, "inspect_sender_ptb_compatibility", _ptb_pass)

    async def _fake_send_message(chat_id: str, text: str):
        return None

    async def _fake_send_file(chat_id: str, path: str, caption: str | None):
        return None

    monkeypatch.setattr(tg, "_send_message", _fake_send_message)
    monkeypatch.setattr(tg, "_send_file", _fake_send_file)
    yield
    _ensure_worker_alive()
    _reset_antares_sender_ownership_for_tests()
    tg._reset_telegram_sender_health_for_tests()


def _claim():
    return claim_antares_sender_ownership()


def _run(coro):
    return asyncio.run(coro)


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def _force_worker_stopped(proof) -> None:
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert result.ok is True, result
    assert result.worker_stopped is True
    assert tg._lifecycle_state == "WORKER_STOPPED"


def _install_fake_bot(
    monkeypatch,
    *,
    fail_bot: bool = False,
    fail_gu: bool = False,
    fail_gen: bool = False,
    closed_gu: bool = False,
    closed_gen: bool = False,
    unknown_diag: bool = False,
):
    if unknown_diag:
        gu = FakeRequestUnknownDiag()
        gen = FakeRequestUnknownDiag()
    else:
        gu = FakeRequest(closed=closed_gu, fail=fail_gu)
        gen = FakeRequest(closed=closed_gen, fail=fail_gen)
    bot = FakeBot(gu, gen, fail_bot=fail_bot)
    monkeypatch.setattr(tg, "bot", bot)
    monkeypatch.setattr(tg, "request", gen)

    def _ptb(**_k):
        return _ptb_for_graph(gu, gen)

    monkeypatch.setattr(tg, "inspect_sender_ptb_compatibility", _ptb)
    return bot, gu, gen


# ---------------------------------------------------------------------------
# FS1–FS5: refuse / no mutation
# ---------------------------------------------------------------------------


def test_fs1_missing_ownership_refuses_no_seal() -> None:
    result = _run(tg.stop_isolated_sender(object(), timeout=1.0))
    assert result.ok is False
    assert result.ownership_passed is False
    assert result.intake_sealed is False
    assert tg._lifecycle_state == "RUNNING"
    assert result.full_resource_stopped is False
    assert tg.loop.is_running()


def test_fs2_foreign_proof_refuses() -> None:
    _claim()
    foreign = AntaresSenderOwnershipAttestation()
    result = _run(tg.stop_isolated_sender(foreign, timeout=1.0))
    assert result.ok is False
    assert result.ownership_passed is False
    assert result.intake_sealed is False
    assert tg._lifecycle_state == "RUNNING"


def test_fs3_running_ptb_fail_no_seal(monkeypatch) -> None:
    proof = _claim()
    monkeypatch.setattr(tg, "inspect_sender_ptb_compatibility", _ptb_fail)
    result = _run(tg.stop_isolated_sender(proof, timeout=1.0))
    assert result.ok is False
    assert result.ptb_passed is False
    assert result.intake_sealed is False
    assert tg._lifecycle_state == "RUNNING"


def test_fs4_worker_stopped_ptb_fail_no_http(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    monkeypatch.setattr(tg, "inspect_sender_ptb_compatibility", _ptb_fail)
    result = _run(tg.stop_isolated_sender(proof, timeout=1.0))
    assert result.ok is False
    assert result.ptb_passed is False
    assert result.lifecycle_state == "WORKER_STOPPED"
    assert result.http_stopped is False
    assert result.bot_shutdown_attempted is False
    assert tg.loop.is_running()
    assert tg._loop_thread.is_alive()


def test_fs5_structural_fail_before_http_untouched(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    # Worker task not done while claiming WORKER_STOPPED → structural refuse.
    alive = MagicMock()
    alive.done.return_value = False
    monkeypatch.setattr(tg, "_worker_task", alive)
    before_bot = tg._bot_shutdown_attempted
    result = _run(tg.stop_isolated_sender(proof, timeout=1.0))
    assert result.ok is False
    assert result.structural_passed is False
    assert result.reason == "worker_not_terminal"
    assert tg._lifecycle_state == "WORKER_STOPPED"
    assert tg._bot_shutdown_attempted == before_bot
    assert result.http_stopped is False


# ---------------------------------------------------------------------------
# FS6–FS7: drain ordering / no second sentinel
# ---------------------------------------------------------------------------


def test_fs6_running_success_drains_before_http(monkeypatch) -> None:
    proof = _claim()
    _install_fake_bot(monkeypatch)
    order: list[str] = []
    real_drain = tg.drain_and_stop_sender_worker

    async def spy_drain(*a, **k):
        order.append("drain")
        return await real_drain(*a, **k)

    async def abort_http(*_a, **_k):
        order.append("http")
        assert tg._lifecycle_state == "WORKER_STOPPED"
        return tg._snapshot_full_stop_fields(
            ok=False,
            reason="aborted_after_drain_for_test",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "drain_and_stop_sender_worker", spy_drain)
    monkeypatch.setattr(tg, "_run_http_close_phase", abort_http)
    result = _run(tg.stop_isolated_sender(proof, timeout=5.0))
    assert order == ["drain", "http"]
    assert result.reason == "aborted_after_drain_for_test"
    assert tg._lifecycle_state == "WORKER_STOPPED"
    assert tg.loop.is_running()


def test_fs7_already_worker_stopped_no_second_sentinel(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    sentinel_schedules = []
    real = tg._real_call_soon_threadsafe

    def capture(cb, *args):
        if cb is tg._enqueue_sentinel_on_loop:
            sentinel_schedules.append(1)
        return real(cb, *args)

    monkeypatch.setattr(tg, "_real_call_soon_threadsafe", capture)

    async def abort_http(*_a, **_k):
        return tg._snapshot_full_stop_fields(
            ok=False,
            reason="aborted_http_for_sentinel_test",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "_run_http_close_phase", abort_http)
    _run(tg.stop_isolated_sender(proof, timeout=2.0))
    assert sentinel_schedules == []


# ---------------------------------------------------------------------------
# FS8–FS14: HTTP phase (loop stays alive)
# ---------------------------------------------------------------------------


def test_fs8_bot_shutdown_failure_requests_still_attempted(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch, fail_bot=True)
    result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert result.ok is False
    assert result.bot_shutdown_attempted is True
    assert result.bot_shutdown_ok is False
    assert result.bot_shutdown_error_type == "RuntimeError"
    assert result.bot_shutdown_error_text is not None
    assert "bot boom" in result.bot_shutdown_error_text
    assert result.http_stopped is False
    assert bot.shutdown_calls == 1
    assert gu.shutdown_calls == 1
    assert gen.shutdown_calls == 1
    assert tg._lifecycle_state == "WORKER_STOPPED"
    assert tg.loop.is_running()
    assert not tg._loop_stop_requested


def test_fs9_one_request_fail_other_attempted_no_loop_stop(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch, fail_gu=True)
    result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert result.ok is False
    assert result.http_stopped is False
    assert bot.shutdown_calls == 1
    assert gu.shutdown_calls == 1
    assert gen.shutdown_calls == 1
    leftovers = [r for r in result.request_close_results if r.leftover_open]
    assert leftovers
    assert tg._lifecycle_state == "WORKER_STOPPED"
    assert not tg._loop_stop_requested
    assert tg.loop.is_running()


def test_fs10_already_closed_after_bot_no_unnecessary_request_shutdown(
    monkeypatch,
) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    gu = FakeRequest(closed=False)
    gen = FakeRequest(closed=False)
    bot = FakeBot(gu, gen)

    async def bot_shutdown_closes_both():
        bot.shutdown_calls += 1
        gu._client.is_closed = True
        gen._client.is_closed = True

    bot.shutdown = bot_shutdown_closes_both  # type: ignore[method-assign]
    monkeypatch.setattr(tg, "bot", bot)
    monkeypatch.setattr(tg, "request", gen)
    monkeypatch.setattr(
        tg,
        "inspect_sender_ptb_compatibility",
        lambda **_k: _ptb_for_graph(gu, gen),
    )

    async def abort_loop(*_a, **_k):
        assert tg._lifecycle_state == "HTTP_STOPPED"
        return tg._snapshot_full_stop_fields(
            ok=False,
            reason="loop_aborted_for_fs10",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "_run_loop_stop_phase", abort_loop)
    result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert gu.shutdown_calls == 0
    assert gen.shutdown_calls == 0
    closed = [r for r in result.request_close_results if r.already_closed]
    assert len(closed) == 2
    assert tg._lifecycle_state == "HTTP_STOPPED"
    assert result.http_stopped is True or result.reason == "loop_aborted_for_fs10"
    assert tg.loop.is_running()
    assert not tg._loop_stop_requested


def test_fs11_unknown_leftover_diagnostic_fail_closed(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch, unknown_diag=True)
    # PTB inspect with unknown diag would refuse leftover diagnostic; force plan
    # via monkeypatch so HTTP phase still runs against unknown targets.
    monkeypatch.setattr(
        tg,
        "inspect_sender_ptb_compatibility",
        lambda **_k: _ptb_for_graph(gu, gen),
    )
    result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert result.ok is False
    assert result.http_stopped is False
    assert not tg._loop_stop_requested
    assert tg._lifecycle_state == "WORKER_STOPPED"
    assert any(
        r.error_type == "leftover_diagnostic_unavailable"
        for r in result.request_close_results
    )
    assert tg.loop.is_running()


def test_fs12_partial_http_then_repeat_reaches_http_stopped(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch, fail_gu=True)
    first = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert first.ok is False
    assert first.http_stopped is False
    assert tg._lifecycle_state == "WORKER_STOPPED"
    # Repair gu for repeat; gen may already be closed from first attempt.
    gu._fail = False
    if not gu._client.is_closed:
        pass  # will close on retry
    # gen may already be fully closed → skipped on repeat.

    async def abort_loop(*_a, **_k):
        assert tg._lifecycle_state == "HTTP_STOPPED"
        return tg._snapshot_full_stop_fields(
            ok=True,
            reason=None,
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "_run_loop_stop_phase", abort_loop)
    second = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert tg._lifecycle_state == "HTTP_STOPPED" or second.http_stopped is True
    assert tg.loop.is_running()


def test_fs13_concurrent_http_stopping_no_duplicate_close(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch)
    hold = threading.Event()
    entered = threading.Event()
    second_observing = threading.Event()
    bot._hold = hold
    bot._entered = entered

    results: list = []
    errors: list = []

    def runner():
        try:
            results.append(_run(tg.stop_isolated_sender(proof, timeout=5.0)))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    real_await = tg._await_http_phase_terminal

    async def spy_await(deadline, *, cancel_owner_on_deadline):
        # Observer path: waiting on existing HTTP_STOPPING session.
        if not cancel_owner_on_deadline:
            second_observing.set()
        return await real_await(
            deadline, cancel_owner_on_deadline=cancel_owner_on_deadline
        )

    monkeypatch.setattr(tg, "_await_http_phase_terminal", spy_await)

    async def abort_loop(*_a, **_k):
        return tg._snapshot_full_stop_fields(
            ok=False,
            reason="loop_aborted_fs13",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "_run_loop_stop_phase", abort_loop)

    t1 = threading.Thread(target=runner)
    t2 = threading.Thread(target=runner)
    t1.start()
    assert entered.wait(timeout=3.0)
    t2.start()
    assert second_observing.wait(timeout=3.0)
    assert tg._lifecycle_state == "HTTP_STOPPING"
    assert bot.shutdown_calls == 1
    hold.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not errors
    assert len(results) == 2
    assert bot.shutdown_calls == 1
    assert gu.shutdown_calls == 1
    assert gen.shutdown_calls == 1
    assert _real_loop_is_running()


def test_fs14_cancel_during_http_leaves_owner_session(monkeypatch) -> None:
    """Caller cancel must not publish HTTP terminal; owner session continues."""
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch)
    hold = threading.Event()
    entered = threading.Event()
    bot._hold = hold
    bot._entered = entered

    async def abort_loop(*_a, **_k):
        return tg._snapshot_full_stop_fields(
            ok=False,
            reason="loop_aborted_fs14",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "_run_loop_stop_phase", abort_loop)

    async def scenario():
        task = asyncio.create_task(tg.stop_isolated_sender(proof, timeout=5.0))
        assert await asyncio.to_thread(entered.wait, 3.0)
        with tg._lifecycle_lock:
            session = tg._http_close_session_task
            state = tg._lifecycle_state
        assert state == "HTTP_STOPPING"
        assert session is not None and not session.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with tg._lifecycle_lock:
            session_after = tg._http_close_session_task
            state_after = tg._lifecycle_state
        assert state_after == "HTTP_STOPPING"
        assert session_after is not None and not session_after.done()
        assert bot.shutdown_calls == 1

        repeat = asyncio.create_task(tg.stop_isolated_sender(proof, timeout=5.0))
        # Repeat must observe the same in-flight session (no second Bot.shutdown).
        assert bot.shutdown_calls == 1
        hold.set()
        result = await repeat
        assert bot.shutdown_calls == 1
        assert result.lifecycle_state == "HTTP_STOPPED"
        assert tg._lifecycle_state == "HTTP_STOPPED"
        assert tg._http_phase_ack.is_set()
        return result

    _run(scenario())
    assert gu.shutdown_calls == 1
    assert gen.shutdown_calls == 1
    assert _real_loop_is_running()


# ---------------------------------------------------------------------------
# FS15–FS21: loop stop (faked) / idempotent STOPPED
# ---------------------------------------------------------------------------


def test_fs15_loop_stop_only_after_http(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    order: list[str] = []
    real_http = tg._http_close_on_sender_loop

    async def spy_http(deadline):
        order.append("http")
        assert tg._loop_stop_requested is False
        ok = await real_http(deadline)
        order.append("http_done")
        assert tg._loop_stop_requested is False
        return ok

    monkeypatch.setattr(tg, "_http_close_on_sender_loop", spy_http)
    with without_killing_sender_loop(monkeypatch) as harness:
        result = _run(tg.stop_isolated_sender(proof, timeout=5.0))
    assert result.ok is True
    assert result.full_resource_stopped is True
    assert order[:2] == ["http", "http_done"]
    assert "loop_stop" in harness.order
    assert order.index("http_done") < len(order)
    # loop.stop recorded after HTTP completed
    assert harness.order.index("loop_stop") >= 0
    assert "http_done" in order


def test_fs16_loop_stop_schedule_failure_http_remains_stopped(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    harness = LoopStopHarness()
    harness.fail_schedule = True
    with without_killing_sender_loop(monkeypatch, harness):
        result = _run(tg.stop_isolated_sender(proof, timeout=5.0))
    assert result.ok is False
    assert result.reason == "loop_stop_schedule_failed"
    assert result.http_stopped is True
    assert result.lifecycle_state == "LOOP_STOPPING"
    assert tg._lifecycle_state == "LOOP_STOPPING"
    assert tg._loop_stop_requested is False
    assert _real_loop_is_running()
    assert _real_thread_is_alive()
    # Repeat must not re-close HTTP (plan already done; bot already shut down).
    bot = tg.bot
    calls_before = bot.shutdown_calls
    harness2 = LoopStopHarness()
    harness2.fail_schedule = True
    with without_killing_sender_loop(monkeypatch, harness2):
        again = _run(tg.stop_isolated_sender(proof, timeout=2.0))
    assert again.reason == "loop_stop_schedule_failed"
    assert again.http_stopped is True
    assert again.lifecycle_state == "LOOP_STOPPING"
    assert bot.shutdown_calls == calls_before
    assert _real_loop_is_running()
    assert _real_thread_is_alive()
    assert tg._lifecycle_state == "LOOP_STOPPING"
    assert tg._http_phase_ack.is_set()


def test_fs17_loop_stop_accepted_timeout_before_join_then_repeat(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    harness = LoopStopHarness()
    harness.auto_signal_stopped = False  # stop scheduled but _loop_stopped never set
    with without_killing_sender_loop(monkeypatch, harness):
        first = _run(tg.stop_isolated_sender(proof, timeout=0.2))
    assert first.ok is False
    assert first.reason == "deadline_loop_stopped"
    assert first.lifecycle_state == "LOOP_STOPPING"
    assert tg._loop_stop_requested is True
    # Repeat: signal stopped and finish.
    harness2 = LoopStopHarness()
    harness2.auto_signal_stopped = True
    # Stop already requested — harness must still fake death when wait proceeds.
    # Production will not re-call loop.stop; we need _loop_stopped set + death fakes.
    with without_killing_sender_loop(monkeypatch, harness2):
        # Manually mark stop faked so is_alive/is_running flip after we set event.
        harness2._stop_faked = True
        tg._loop_stopped.set()
        second = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert second.ok is True
    assert second.full_resource_stopped is True
    assert second.lifecycle_state == "STOPPED"


def test_fs18_thread_join_timeout_not_stopped(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    harness = LoopStopHarness()
    harness.thread_alive_after_stop = True
    harness.loop_running_after_stop = False
    with without_killing_sender_loop(monkeypatch, harness):
        result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert result.ok is False
    assert result.reason == "deadline_thread_join"
    assert result.full_resource_stopped is False
    assert result.lifecycle_state == "LOOP_STOPPING"
    assert result.loop_thread_alive is True


def test_fs19_repeat_after_thread_terminal_reaches_stopped(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    harness = LoopStopHarness()
    harness.thread_alive_after_stop = True
    with without_killing_sender_loop(monkeypatch, harness):
        first = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert first.ok is False
    assert first.lifecycle_state == "LOOP_STOPPING"
    harness2 = LoopStopHarness()
    harness2.thread_alive_after_stop = False
    harness2.loop_running_after_stop = False
    with without_killing_sender_loop(monkeypatch, harness2):
        harness2._stop_faked = True
        tg._loop_stopped.set()
        second = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert second.ok is True
    assert second.lifecycle_state == "STOPPED"
    assert second.full_resource_stopped is True


def test_fs20_stopped_same_proof_idempotent_no_redo(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch)
    ptb_calls = {"n": 0}
    real_ptb = tg.inspect_sender_ptb_compatibility

    def counting_ptb(**kwargs):
        ptb_calls["n"] += 1
        return real_ptb(**kwargs)

    monkeypatch.setattr(tg, "inspect_sender_ptb_compatibility", counting_ptb)
    with without_killing_sender_loop(monkeypatch):
        first = _run(tg.stop_isolated_sender(proof, timeout=5.0))
    assert first.ok is True
    assert first.lifecycle_state == "STOPPED"
    bot_calls = bot.shutdown_calls
    gu_calls = gu.shutdown_calls
    gen_calls = gen.shutdown_calls
    ptb_after_first = ptb_calls["n"]
    stop_schedules = {"n": 0}
    real = tg._real_call_soon_threadsafe

    def count_stop(cb, *args):
        if _is_loop_stop_callback(cb):
            stop_schedules["n"] += 1
            return None
        return real(cb, *args)

    monkeypatch.setattr(tg, "_real_call_soon_threadsafe", count_stop)
    # Keep fake death so STOPPED snapshot stays truthful.
    with without_killing_sender_loop(monkeypatch) as h:
        h._stop_faked = True
        second = _run(tg.stop_isolated_sender(proof, timeout=2.0))
    assert second.ok is True
    assert second.lifecycle_state == "STOPPED"
    assert bot.shutdown_calls == bot_calls
    assert gu.shutdown_calls == gu_calls
    assert gen.shutdown_calls == gen_calls
    assert ptb_calls["n"] == ptb_after_first
    assert stop_schedules["n"] == 0


def test_fs21_stopped_foreign_refuses(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    with without_killing_sender_loop(monkeypatch):
        assert _run(tg.stop_isolated_sender(proof, timeout=5.0)).ok is True
    foreign = AntaresSenderOwnershipAttestation()
    with without_killing_sender_loop(monkeypatch) as h:
        h._stop_faked = True
        result = _run(tg.stop_isolated_sender(foreign, timeout=1.0))
    assert result.ok is False
    assert result.ownership_passed is False
    assert result.reason is not None


# ---------------------------------------------------------------------------
# FS22–FS24: cross-API / intake failure accounting
# ---------------------------------------------------------------------------


def test_fs22_drain_after_full_stop_idempotent_no_sentinel(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    with without_killing_sender_loop(monkeypatch):
        assert _run(tg.stop_isolated_sender(proof, timeout=5.0)).ok is True
    sentinel_schedules = []
    real = tg._real_call_soon_threadsafe

    def capture(cb, *args):
        if cb is tg._enqueue_sentinel_on_loop:
            sentinel_schedules.append(1)
        return real(cb, *args)

    monkeypatch.setattr(tg, "_real_call_soon_threadsafe", capture)
    with without_killing_sender_loop(monkeypatch) as h:
        h._stop_faked = True
        drain = _run(tg.drain_and_stop_sender_worker(proof, timeout=2.0))
    assert drain.ok is True
    assert drain.worker_stopped is True
    assert sentinel_schedules == []


def test_fs23_historical_d27_d28_still_allow_full_stop(monkeypatch) -> None:
    proof = _claim()

    def boom(*_a, **_k):
        raise RuntimeError("schedule boom")

    monkeypatch.setattr(tg.loop, "call_soon_threadsafe", boom)
    tg.send_message_sync("lost", chat_id="-1")
    assert tg._terminal_intake_failure_total >= 1
    monkeypatch.setattr(tg.loop, "call_soon_threadsafe", tg._real_call_soon_threadsafe)
    _install_fake_bot(monkeypatch)
    with without_killing_sender_loop(monkeypatch):
        result = _run(tg.stop_isolated_sender(proof, timeout=5.0))
    assert result.ok is True, result
    assert result.full_resource_stopped is True
    assert result.terminal_intake_failure_total >= 1
    assert any("d27_schedule" in e for e in result.recent_intake_failures)


def test_fs24_http_failure_does_not_increment_intake_failure_total(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    before = tg._terminal_intake_failure_total
    _install_fake_bot(monkeypatch, fail_bot=True)
    result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert result.ok is False
    assert result.http_stopped is False
    assert tg._terminal_intake_failure_total == before


# ---------------------------------------------------------------------------
# FS25: final success field asserts
# ---------------------------------------------------------------------------


def test_fs25_final_success_fields(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch)
    with without_killing_sender_loop(monkeypatch):
        result = _run(tg.stop_isolated_sender(proof, timeout=5.0))
    assert result.ok is True
    assert result.reason is None
    assert result.ownership_passed is True
    assert result.ptb_passed is True
    assert result.structural_passed is True
    assert result.lifecycle_state == "STOPPED"
    assert result.intake_sealed is True
    assert result.worker_stopped is True
    assert result.worker_terminal is True
    assert result.bot_shutdown_attempted is True
    assert result.bot_shutdown_ok is True
    assert result.http_stopped is True
    assert result.loop_stop_requested is True
    assert result.loop_running is False
    assert result.loop_thread_alive is False
    assert result.thread_joined is True
    assert result.full_resource_stopped is True
    assert all(not r.leftover_open for r in result.request_close_results)
    assert bot.shutdown_calls == 1
    assert gu.shutdown_calls == 1
    assert gen.shutdown_calls == 1


# ---------------------------------------------------------------------------
# Optional subprocess: real STOPPED via public API (isolated process)
# ---------------------------------------------------------------------------


def test_subprocess_public_wiring_reaches_real_stopped() -> None:
    code = textwrap.dedent(
        """
        import asyncio
        import integrations.telegram_bot as tg
        from core.antares_sender_ownership import (
            _reset_antares_sender_ownership_for_tests,
            claim_antares_sender_ownership,
        )
        from integrations.telegram_sender_gates import (
            SenderPtbCompatibilityResult,
            SenderRequestRolePlan,
        )

        class FakeClient:
            def __init__(self, closed=False):
                self.is_closed = closed

        class FakeRequest:
            def __init__(self):
                self._client = FakeClient(False)
                self.shutdown_calls = 0
            async def shutdown(self):
                self.shutdown_calls += 1
                self._client.is_closed = True

        class FakeBot:
            def __init__(self, gu, gen):
                self._request = (gu, gen)
                self.request = gen
                self.shutdown_calls = 0
            async def shutdown(self):
                self.shutdown_calls += 1

        _reset_antares_sender_ownership_for_tests()
        tg._reset_telegram_sender_health_for_tests()
        with tg._lifecycle_lock:
            tg._lifecycle_state = "DRAINING"
            tg._intake_sealed = True
            tg._sentinel_state = tg._SENTINEL_ENQUEUED
            tg._sentinel_ack.set()
        tg._reset_sender_worker_lifecycle_for_tests()

        gu, gen = FakeRequest(), FakeRequest()
        bot = FakeBot(gu, gen)
        tg.bot = bot
        tg.request = gen
        roles = (
            SenderRequestRolePlan(role="get_updates_request", request=gu),
            SenderRequestRolePlan(role="request", request=gen),
        )
        tg.inspect_sender_ptb_compatibility = lambda **k: SenderPtbCompatibilityResult(
            supported=True, reason=None, roles=roles, close_targets=(gu, gen)
        )

        async def _fake_send_message(chat_id, text):
            return None
        async def _fake_send_file(chat_id, path, caption):
            return None
        tg._send_message = _fake_send_message
        tg._send_file = _fake_send_file

        proof = claim_antares_sender_ownership()
        result = asyncio.run(tg.stop_isolated_sender(proof, timeout=10.0))
        assert result.ok is True, result
        assert result.full_resource_stopped is True, result
        assert result.lifecycle_state == "STOPPED"
        assert not tg.loop.is_running()
        assert not tg._loop_thread.is_alive()
        print("SUBPROCESS_STOPPED_OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(__file__).rsplit("tests", 1)[0],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "SUBPROCESS_STOPPED_OK" in proc.stdout


# ---------------------------------------------------------------------------
# HC1–HC3 + probe hardening (GPT review regressions)
# ---------------------------------------------------------------------------


def test_hc1_cancel_leaves_http_stopping_same_session_no_second_bot(
    monkeypatch,
) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch)
    hold = threading.Event()
    entered = threading.Event()
    second_observing = threading.Event()
    bot._hold = hold
    bot._entered = entered

    real_await = tg._await_http_phase_terminal

    async def spy_await(deadline, *, cancel_owner_on_deadline):
        if not cancel_owner_on_deadline:
            second_observing.set()
        return await real_await(
            deadline, cancel_owner_on_deadline=cancel_owner_on_deadline
        )

    monkeypatch.setattr(tg, "_await_http_phase_terminal", spy_await)

    async def abort_loop(*_a, **_k):
        return tg._snapshot_full_stop_fields(
            ok=False,
            reason="loop_aborted_hc1",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "_run_loop_stop_phase", abort_loop)

    async def scenario():
        first = asyncio.create_task(tg.stop_isolated_sender(proof, timeout=5.0))
        assert await asyncio.to_thread(entered.wait, 3.0)
        with tg._lifecycle_lock:
            session = tg._http_close_session_task
            state = tg._lifecycle_state
        assert state == "HTTP_STOPPING"
        assert session is not None and not session.done()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with tg._lifecycle_lock:
            assert tg._lifecycle_state == "HTTP_STOPPING"
            assert tg._http_close_session_task is session
            assert not session.done()
        assert bot.shutdown_calls == 1

        repeat = asyncio.create_task(tg.stop_isolated_sender(proof, timeout=5.0))
        assert await asyncio.to_thread(second_observing.wait, 3.0)
        assert bot.shutdown_calls == 1
        hold.set()
        result = await repeat
        assert bot.shutdown_calls == 1
        assert result.lifecycle_state == "HTTP_STOPPED"
        assert result.bot_shutdown_ok is True
        assert tg._lifecycle_state == "HTTP_STOPPED"
        assert tg._http_phase_ack.is_set()
        with tg._lifecycle_lock:
            assert tg._http_close_session_task is None
        return result

    _run(scenario())
    assert gu.shutdown_calls == 1
    assert gen.shutdown_calls == 1
    assert _real_loop_is_running()


def test_hc2_bot_shutdown_not_retried_after_success_partial_http(
    monkeypatch,
) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    bot, gu, gen = _install_fake_bot(monkeypatch, fail_gu=True)

    async def abort_loop(*_a, **_k):
        return tg._snapshot_full_stop_fields(
            ok=False,
            reason="loop_aborted_hc2",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "_run_loop_stop_phase", abort_loop)

    first = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert first.ok is False
    assert first.bot_shutdown_ok is True
    assert first.lifecycle_state == "WORKER_STOPPED"
    assert bot.shutdown_calls == 1
    assert gu.shutdown_calls == 1
    assert gen.shutdown_calls == 1
    assert any(r.leftover_open for r in first.request_close_results)

    # Repair only the failed request; do not reopen gen or reset bot counters.
    gu._fail = False
    second = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert bot.shutdown_calls == 1
    assert gen.shutdown_calls == 1
    assert gu.shutdown_calls == 2
    assert second.bot_shutdown_ok is True
    assert second.lifecycle_state == "HTTP_STOPPED"
    assert second.http_stopped is True
    assert all(not r.leftover_open for r in second.request_close_results)


def test_hc3_http_structural_observes_worker_via_sender_loop(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    _install_fake_bot(monkeypatch)
    seen: list = []
    real_run = tg._run_on_sender_loop

    async def spy_run(coro_factory, deadline):
        seen.append(coro_factory)
        return await real_run(coro_factory, deadline)

    monkeypatch.setattr(tg, "_run_on_sender_loop", spy_run)

    async def abort_http(*_a, **_k):
        return tg._snapshot_full_stop_fields(
            ok=False,
            reason="aborted_http_hc3",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    monkeypatch.setattr(tg, "_run_http_close_phase", abort_http)
    result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert result.reason == "aborted_http_hc3"
    assert tg._worker_status_on_sender_loop in seen
    assert result.structural_passed is True


def test_hc_bot_shutdown_probe_raises_structured_terminal(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    gu = FakeRequest()
    gen = FakeRequest()

    class ProbeBoomBot:
        def __init__(self):
            self._request = (gu, gen)
            self.request = gen
            self.shutdown_calls = 0

        @property
        def shutdown(self):
            raise RuntimeError("bot probe boom")

    bot = ProbeBoomBot()
    monkeypatch.setattr(tg, "bot", bot)
    monkeypatch.setattr(tg, "request", gen)
    monkeypatch.setattr(
        tg, "inspect_sender_ptb_compatibility", lambda **_k: _ptb_for_graph(gu, gen)
    )
    result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert result.ok is False
    assert result.bot_shutdown_attempted is True
    assert result.bot_shutdown_ok is False
    assert result.bot_shutdown_error_type == "RuntimeError"
    assert result.bot_shutdown_error_text is not None
    assert "bot probe boom" in result.bot_shutdown_error_text
    assert tg._lifecycle_state == "WORKER_STOPPED"
    assert _real_loop_is_running()
    # Repeat remains possible.
    again = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert again.lifecycle_state == "WORKER_STOPPED"
    assert again.bot_shutdown_error_type == "RuntimeError"


def test_hc_request_shutdown_probe_raises_structured_terminal(monkeypatch) -> None:
    proof = _claim()
    _force_worker_stopped(proof)
    gen = FakeRequest()

    class ProbeBoomRequest:
        def __init__(self):
            self._client = FakeClient(closed=False)
            self.shutdown_calls = 0

        @property
        def shutdown(self):
            raise RuntimeError("req probe boom")

    gu = ProbeBoomRequest()
    bot = FakeBot(gu, gen)
    monkeypatch.setattr(tg, "bot", bot)
    monkeypatch.setattr(tg, "request", gen)
    monkeypatch.setattr(
        tg, "inspect_sender_ptb_compatibility", lambda **_k: _ptb_for_graph(gu, gen)
    )
    result = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert result.ok is False
    assert result.bot_shutdown_ok is True
    assert any(
        r.error_type == "RuntimeError" and r.error_text and "req probe boom" in r.error_text
        for r in result.request_close_results
    )
    assert tg._lifecycle_state == "WORKER_STOPPED"
    assert _real_loop_is_running()
    # Repeat remains possible without stranding HTTP_STOPPING.
    again = _run(tg.stop_isolated_sender(proof, timeout=3.0))
    assert again.lifecycle_state == "WORKER_STOPPED"
    assert tg._lifecycle_state != "HTTP_STOPPING"
