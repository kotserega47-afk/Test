"""TASK-47: sender intake seal + S1/S3 drain + worker stop (not full resource stop)."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

import integrations.telegram_bot as tg
from core.antares_sender_ownership import (
    AntaresSenderOwnershipAttestation,
    _reset_antares_sender_ownership_for_tests,
    claim_antares_sender_ownership,
)
from integrations.telegram_sender_gates import SenderPtbCompatibilityResult


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


@pytest.fixture(autouse=True)
def _isolate_sender_lifecycle(monkeypatch):
    _reset_antares_sender_ownership_for_tests()
    tg._reset_telegram_sender_health_for_tests()
    # Force a clean worker/queue for every test.
    with tg._lifecycle_lock:
        tg._lifecycle_state = "DRAINING"
        tg._intake_sealed = True
        tg._sentinel_state = tg._SENTINEL_ENQUEUED
        tg._sentinel_ack.set()
    tg._reset_sender_worker_lifecycle_for_tests()
    monkeypatch.setattr(tg, "TELEGRAM_TOKEN", "123456:ABC-TEST")
    monkeypatch.setattr(tg, "inspect_sender_ptb_compatibility", _ptb_pass)

    async def _fake_send_message(chat_id: str, text: str):
        return None

    async def _fake_send_file(chat_id: str, path: str, caption: str | None):
        return None

    monkeypatch.setattr(tg, "_send_message", _fake_send_message)
    monkeypatch.setattr(tg, "_send_file", _fake_send_file)
    assert tg._worker_ready.is_set()
    assert tg._worker_task is not None and not tg._worker_task.done()
    yield
    with tg._lifecycle_lock:
        tg._lifecycle_state = "DRAINING"
        tg._intake_sealed = True
        tg._sentinel_state = tg._SENTINEL_ENQUEUED
        tg._sentinel_ack.set()
    tg._reset_sender_worker_lifecycle_for_tests()
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


def test_t1_no_ownership_proof_refuses_open_intake() -> None:
    result = _run(tg.drain_and_stop_sender_worker(object(), timeout=1.0))
    assert result.ok is False
    assert result.ownership_passed is False
    assert result.intake_sealed is False
    assert tg._lifecycle_state == "RUNNING"


def test_t2_foreign_proof_refuses_no_mutation() -> None:
    _claim()
    foreign = AntaresSenderOwnershipAttestation()
    result = _run(tg.drain_and_stop_sender_worker(foreign, timeout=1.0))
    assert result.ok is False
    assert result.ownership_passed is False
    assert result.intake_sealed is False
    assert tg._lifecycle_state == "RUNNING"


def test_t3_ptb_fail_no_seal(monkeypatch) -> None:
    proof = _claim()
    monkeypatch.setattr(tg, "inspect_sender_ptb_compatibility", _ptb_fail)
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=1.0))
    assert result.ok is False
    assert result.ptb_passed is False
    assert result.intake_sealed is False


def test_t4_structural_dead_worker_no_seal(monkeypatch) -> None:
    proof = _claim()
    dead = MagicMock()
    dead.done.return_value = True
    monkeypatch.setattr(tg, "_worker_task", dead)
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=1.0))
    assert result.ok is False
    assert result.structural_passed is False
    assert result.intake_sealed is False
    assert result.reason == "worker_already_dead"


def test_t5_snd1_s1_blocked_put_not_idle() -> None:
    proof = _claim()
    with tg._lifecycle_lock:
        tg._pending_loop_handoffs = 1
        tg._s1_idle.clear()

    assert tg._pending_loop_handoffs == 1
    assert not tg._s1_idle.is_set()

    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=0.15))
    assert result.ok is False
    assert result.reason == "deadline_s1_pending"
    assert result.intake_sealed is True
    assert result.pending_loop_handoffs >= 1


def test_t6_d27_call_soon_throws_rolls_back_s1(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("schedule boom")

    monkeypatch.setattr(tg.loop, "call_soon_threadsafe", boom)
    before = tg._terminal_intake_failure_total
    tg.send_message_sync("x", chat_id="-1")
    assert tg._pending_loop_handoffs == 0
    assert tg._s1_idle.is_set()
    assert tg._terminal_intake_failure_total == before + 1
    assert any("d27_schedule" in e for e in tg._recent_intake_failures)


def test_t7_d28_put_nowait_throws(monkeypatch) -> None:
    def run_soon(cb, *args):
        cb(*args)

    monkeypatch.setattr(tg.loop, "call_soon_threadsafe", run_soon)

    def boom(_item):
        raise RuntimeError("put boom")

    monkeypatch.setattr(tg.queue, "put_nowait", boom)
    before = tg._terminal_intake_failure_total
    tg.send_message_sync("x", chat_id="-1")
    _wait_until(lambda: tg._pending_loop_handoffs == 0)
    assert tg._s1_idle.is_set()
    assert tg._terminal_intake_failure_total == before + 1
    assert any("d28_put" in e for e in tg._recent_intake_failures)


def test_t8_snd2_drain_waits_queued_item() -> None:
    proof = _claim()
    gate = threading.Event()

    async def blocked_send(chat_id: str, text: str):
        while not gate.is_set():
            await asyncio.sleep(0.01)

    tg._send_message = blocked_send  # type: ignore[assignment]
    tg.send_message_sync("queued", chat_id="-100")
    _wait_until(lambda: tg._active_user_sends >= 1 or tg.queue.qsize() >= 1)

    def release_later():
        time.sleep(0.1)
        gate.set()

    threading.Thread(target=release_later, daemon=True).start()
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert result.ok is True
    assert result.worker_stopped is True
    assert result.full_resource_stopped is False
    assert result.loop_running is True
    assert result.loop_thread_alive is True


def test_t9_snd3_active_s3_blocks_completion() -> None:
    proof = _claim()
    hold = threading.Event()

    async def blocked_send(chat_id: str, text: str):
        while not hold.is_set():
            await asyncio.sleep(0.01)

    tg._send_message = blocked_send  # type: ignore[assignment]
    tg.send_message_sync("in-flight", chat_id="-100")
    _wait_until(lambda: tg._active_user_sends >= 1)

    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=0.2))
    assert result.ok is False
    assert result.reason in {"deadline_s3_active", "deadline_queue_join"}
    assert result.intake_sealed is True
    assert result.active_user_sends >= 1 or result.reason == "deadline_queue_join"

    hold.set()
    _wait_until(lambda: tg._active_user_sends == 0)


def test_t10_send_success_s3_zero_and_join() -> None:
    proof = _claim()
    tg.send_message_sync("ok", chat_id="-100")
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
    assert result.ok is True
    assert result.active_user_sends == 0
    assert result.worker_terminal is True


def test_t11_snd14_failure_after_get_still_task_done(monkeypatch) -> None:
    proof = _claim()

    async def boom_send(chat_id: str, text: str):
        raise RuntimeError("send boom")

    tg._send_message = boom_send  # type: ignore[assignment]
    tg.send_message_sync("fail", chat_id="-100")
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
    assert result.ok is True
    assert result.queue_drained is True


def test_t12_seal_race_admitted_or_rejected() -> None:
    proof = _claim()
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def sender():
        barrier.wait()
        try:
            tg.send_message_sync("race", chat_id="-100")
            outcomes.append("admitted")
        except tg.TelegramSenderIntakeClosedError:
            outcomes.append("rejected")

    def drainer():
        barrier.wait()
        _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))

    t1 = threading.Thread(target=sender)
    t2 = threading.Thread(target=drainer)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert outcomes in (["admitted"], ["rejected"])
    assert tg._lifecycle_state == "WORKER_STOPPED"


def test_t13_snd7_late_text_reject() -> None:
    proof = _claim()
    assert _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0)).ok is True
    s1_before = tg._pending_loop_handoffs
    with pytest.raises(tg.TelegramSenderIntakeClosedError):
        tg.send_message_sync("late", chat_id="-100")
    assert tg._pending_loop_handoffs == s1_before


def test_t14_late_file_reject() -> None:
    proof = _claim()
    assert _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0)).ok is True
    s1_before = tg._pending_loop_handoffs
    with pytest.raises(tg.TelegramSenderIntakeClosedError):
        tg.send_file_sync("/tmp/x", None, chat_id="-100")
    assert tg._pending_loop_handoffs == s1_before


def test_t15_sentinel_after_idle_exact_once() -> None:
    proof = _claim()
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
    assert result.ok is True
    assert result.sentinel_submitted is True
    assert result.worker_terminal is True
    # Repeat must not submit another sentinel / must stay idempotent.
    again = _run(tg.drain_and_stop_sender_worker(proof, timeout=1.0))
    assert again.ok is True
    assert again.worker_stopped is True


def test_t16_snd8_cancel_waiter_keeps_accepted(monkeypatch) -> None:
    proof = _claim()
    hold = threading.Event()

    async def blocked_send(chat_id: str, text: str):
        while not hold.is_set():
            await asyncio.sleep(0.01)

    tg._send_message = blocked_send  # type: ignore[assignment]
    tg.send_message_sync("kept", chat_id="-100")
    _wait_until(lambda: tg._active_user_sends >= 1)

    async def cancellable():
        task = asyncio.create_task(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(cancellable())
    assert tg._intake_sealed is True
    assert tg._lifecycle_state == "DRAINING"
    assert tg._active_user_sends >= 1
    hold.set()
    _wait_until(lambda: tg._active_user_sends == 0)
    # Continue drain with same proof.
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
    assert result.ok is True


def test_t17_snd9_deadline_with_active_s3() -> None:
    proof = _claim()
    hold = threading.Event()

    async def blocked_send(chat_id: str, text: str):
        while not hold.is_set():
            await asyncio.sleep(0.01)

    tg._send_message = blocked_send  # type: ignore[assignment]
    tg.send_message_sync("active", chat_id="-100")
    _wait_until(lambda: tg._active_user_sends >= 1)
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=0.15))
    assert result.ok is False
    assert result.intake_sealed is True
    assert tg._lifecycle_state == "DRAINING"
    hold.set()


def test_t18_snd13_unexpected_dead_worker_after_seal(monkeypatch) -> None:
    proof = _claim()
    # Partial DRAINING state with a dead worker handle — empty queue must not
    # be treated as success (SND13).
    with tg._lifecycle_lock:
        tg._intake_sealed = True
        tg._lifecycle_state = "DRAINING"
        tg._drain_owner_proof = proof
        tg._sentinel_state = tg._SENTINEL_NOT_SUBMITTED
        tg._sentinel_ack.clear()
        tg._sentinel_failure = None
    dead = MagicMock()
    dead.done.return_value = True
    monkeypatch.setattr(tg, "_worker_task", dead)

    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=2.0))
    assert result.ok is False
    assert result.reason == "unexpected_dead_worker"
    assert result.intake_sealed is True


def test_t19_repeat_partial_no_duplicate_sentinel() -> None:
    proof = _claim()
    hold = threading.Event()

    async def blocked_send(chat_id: str, text: str):
        while not hold.is_set():
            await asyncio.sleep(0.01)

    tg._send_message = blocked_send  # type: ignore[assignment]
    tg.send_message_sync("partial", chat_id="-100")
    _wait_until(lambda: tg._active_user_sends >= 1)
    first = _run(tg.drain_and_stop_sender_worker(proof, timeout=0.2))
    assert first.ok is False
    assert tg._sentinel_state == tg._SENTINEL_NOT_SUBMITTED
    assert tg._intake_sealed is True
    hold.set()
    _wait_until(lambda: tg._active_user_sends == 0)
    owned = tg._worker_owned_queue or tg.queue
    fut = asyncio.run_coroutine_threadsafe(owned.join(), tg.loop)
    fut.result(timeout=2.0)
    assert tg._worker_task is not None and not tg._worker_task.done()
    second = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert second.ok is True, second
    assert tg._sentinel_state == tg._SENTINEL_ENQUEUED


def test_t20_worker_stopped_idempotent_not_final_stopped() -> None:
    proof = _claim()
    first = _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
    assert first.ok is True
    second = _run(tg.drain_and_stop_sender_worker(proof, timeout=1.0))
    assert second.ok is True
    assert second.worker_stopped is True
    assert second.full_resource_stopped is False
    assert second.loop_running is True
    assert second.loop_thread_alive is True
    assert tg.loop.is_running()
    assert tg._loop_thread.is_alive()


def test_t21_worker_stopped_foreign_refuses() -> None:
    proof = _claim()
    assert _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0)).ok is True
    foreign = AntaresSenderOwnershipAttestation()
    result = _run(tg.drain_and_stop_sender_worker(foreign, timeout=1.0))
    assert result.ok is False
    assert result.ownership_passed is False


def test_t22_intake_failure_still_allows_worker_success(monkeypatch) -> None:
    proof = _claim()

    def boom(*_a, **_k):
        raise RuntimeError("schedule boom")

    monkeypatch.setattr(tg.loop, "call_soon_threadsafe", boom)
    tg.send_message_sync("lost", chat_id="-1")
    assert tg._terminal_intake_failure_total >= 1

    monkeypatch.setattr(tg.loop, "call_soon_threadsafe", tg._real_call_soon_threadsafe)
    # Ensure worker is alive after the D27 scheduling failure path.
    assert tg._worker_task is not None and not tg._worker_task.done()
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert result.ok is True, result
    assert result.terminal_intake_failure_total >= 1
    assert result.worker_stopped is True
    assert result.full_resource_stopped is False


def test_worker_ready_wait_keeps_caller_loop_responsive() -> None:
    """BLOCKER 1: drain must not Event.wait()-block the caller asyncio loop."""

    proof = _claim()
    tg._worker_ready.clear()

    async def scenario():
        ticks: list[int] = []

        async def ticker() -> None:
            for i in range(8):
                ticks.append(i)
                await asyncio.sleep(0)

        drain_task = asyncio.create_task(
            tg.drain_and_stop_sender_worker(proof, timeout=3.0)
        )
        tick_task = asyncio.create_task(ticker())
        # Yield repeatedly until ticker proves the loop is responsive.
        for _ in range(100):
            if ticks:
                break
            await asyncio.sleep(0)
        assert ticks, "caller event loop did not progress during worker_ready wait"
        tg._worker_ready.set()
        result = await drain_task
        await tick_task
        return result

    result = _run(scenario())
    assert result.ok is True, result
    assert result.worker_stopped is True


def test_sent1_scheduled_not_yet_enqueued(monkeypatch) -> None:
    proof = _claim()
    held: list[tuple] = []
    real = tg._real_call_soon_threadsafe

    def capture(cb, *args):
        if cb is tg._enqueue_sentinel_on_loop:
            held.append((cb, args))
            return None
        return real(cb, *args)

    monkeypatch.setattr(tg, "_real_call_soon_threadsafe", capture)
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=0.25))
    assert result.ok is False
    assert result.reason == "deadline_sentinel_ack"
    assert result.sentinel_submitted is False
    assert tg._sentinel_state == tg._SENTINEL_SCHEDULED
    assert len(held) == 1
    # Complete pending submission so fixture reset can restart cleanly.
    # Use unbound `real` — monkeypatch still wraps tg._real_call_soon_threadsafe.
    real(held[0][0], *held[0][1])
    _wait_until(lambda: tg._sentinel_state == tg._SENTINEL_ENQUEUED)


def test_sent2_loop_side_put_failure(monkeypatch) -> None:
    proof = _claim()
    owned = tg._worker_owned_queue or tg.queue
    real_put = owned.put_nowait

    def boom(item):
        if isinstance(item, tg._WorkerStopSentinel):
            raise RuntimeError("sentinel put boom")
        return real_put(item)

    monkeypatch.setattr(owned, "put_nowait", boom)
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
    assert result.ok is False
    assert result.reason.startswith("sentinel_submit_failed:")
    assert result.sentinel_submitted is False
    assert tg._sentinel_state == tg._SENTINEL_FAILED
    assert tg._lifecycle_state == "DRAINING"
    assert tg._worker_task is not None and not tg._worker_task.done()


def test_sent3_repeat_after_put_failure(monkeypatch) -> None:
    proof = _claim()
    owned = tg._worker_owned_queue or tg.queue
    real_put = owned.put_nowait
    fail_once = {"n": 0}

    def boom_once(item):
        if isinstance(item, tg._WorkerStopSentinel) and fail_once["n"] == 0:
            fail_once["n"] += 1
            raise RuntimeError("sentinel put boom")
        return real_put(item)

    monkeypatch.setattr(owned, "put_nowait", boom_once)
    first = _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
    assert first.ok is False
    assert first.sentinel_submitted is False
    assert tg._sentinel_state == tg._SENTINEL_FAILED
    monkeypatch.setattr(owned, "put_nowait", real_put)
    second = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert second.ok is True, second
    assert second.sentinel_submitted is True
    assert tg._sentinel_state == tg._SENTINEL_ENQUEUED
    assert second.worker_stopped is True


def test_sent4_pending_sentinel_no_duplicate_on_repeat(monkeypatch) -> None:
    proof = _claim()
    held: list[tuple] = []
    real = tg._real_call_soon_threadsafe

    def capture(cb, *args):
        if cb is tg._enqueue_sentinel_on_loop:
            held.append((cb, args))
            return None
        return real(cb, *args)

    monkeypatch.setattr(tg, "_real_call_soon_threadsafe", capture)
    first = _run(tg.drain_and_stop_sender_worker(proof, timeout=0.2))
    assert first.ok is False
    assert tg._sentinel_state == tg._SENTINEL_SCHEDULED
    assert len(held) == 1

    async def continue_drain():
        task = asyncio.create_task(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
        # Let repeat observe SCHEDULED and wait — must not schedule a second callback.
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(held) == 1
        # Use unbound `real` — monkeypatch still wraps tg._real_call_soon_threadsafe.
        real(held[0][0], *held[0][1])
        return await task

    second = _run(continue_drain())
    assert second.ok is True, second
    assert len(held) == 1
    assert tg._sentinel_state == tg._SENTINEL_ENQUEUED


def test_wt1_waiter_cancel_does_not_cancel_owned_task() -> None:
    """BLOCKER 1: shield — cancelling terminal waiter must not cancel owned Task."""

    hold = threading.Event()
    done_flag = threading.Event()

    async def owned_body():
        while not hold.is_set():
            await asyncio.sleep(0.01)
        done_flag.set()

    async def _start_named_owned():
        return asyncio.create_task(owned_body(), name="wt1-owned-task")

    fut = asyncio.run_coroutine_threadsafe(_start_named_owned(), tg.loop)
    owned_task = fut.result(timeout=2.0)
    assert not owned_task.done()

    # Temporarily point module worker task at the owned task so production
    # `_await_worker_terminal` (shield) is exercised.
    previous = tg._worker_task
    tg._worker_task = owned_task
    try:

        async def cancel_waiter():
            deadline = time.monotonic() + 5.0
            wait_task = asyncio.create_task(
                tg._run_on_sender_loop(tg._await_worker_terminal, deadline)
            )
            for _ in range(10):
                await asyncio.sleep(0)
            wait_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wait_task
            for _ in range(20):
                await asyncio.sleep(0)

        _run(cancel_waiter())
        assert not owned_task.done()
        assert not owned_task.cancelled()
        hold.set()
        _wait_until(lambda: done_flag.is_set())
        _wait_until(lambda: owned_task.done())
        assert not owned_task.cancelled()
    finally:
        tg._worker_task = previous


def test_wt1b_drain_timeout_path_does_not_cancel_worker() -> None:
    """Timeout of `_run_on_sender_loop(_await_worker_terminal)` must not cancel worker."""

    worker = tg._worker_task
    assert worker is not None and not worker.done()

    async def timeout_terminal_wait():
        # Near-zero deadline after scheduling wait — wait_for cancels the
        # concurrent future / waiter coroutine, which must not cancel worker.
        with pytest.raises(TimeoutError):
            await tg._run_on_sender_loop(tg._await_worker_terminal, time.monotonic() + 0.05)
        for _ in range(20):
            await asyncio.sleep(0)

    _run(timeout_terminal_wait())
    assert tg._worker_task is worker
    assert not worker.done()
    assert not worker.cancelled()


def test_qj1_queue_join_watcher_cancel_cleans_orphan() -> None:
    """BLOCKER 2: cancelling join watcher must not leave orphan join Task."""

    proof = _claim()
    hold = threading.Event()

    async def blocked_send(chat_id: str, text: str):
        while not hold.is_set():
            await asyncio.sleep(0.01)

    tg._send_message = blocked_send  # type: ignore[assignment]
    tg.send_message_sync("qj1", chat_id="-100")
    _wait_until(lambda: tg._active_user_sends >= 1)

    def count_join_waiters() -> int:
        fut = asyncio.run_coroutine_threadsafe(_count_named_join_waiters(), tg.loop)
        return fut.result(timeout=2.0)

    async def cancel_during_join():
        drain_task = asyncio.create_task(
            tg.drain_and_stop_sender_worker(proof, timeout=5.0)
        )
        for _ in range(200):
            if tg._intake_sealed and tg._active_user_sends >= 1:
                break
            await asyncio.sleep(0)
        assert tg._intake_sealed
        for _ in range(50):
            if count_join_waiters() >= 1:
                break
            await asyncio.sleep(0)
        assert count_join_waiters() >= 1
        drain_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain_task
        for _ in range(40):
            await asyncio.sleep(0)
        assert count_join_waiters() == 0
        assert tg._intake_sealed is True
        assert tg._lifecycle_state == "DRAINING"
        assert tg._active_user_sends >= 1

    _run(cancel_during_join())
    hold.set()
    _wait_until(lambda: tg._active_user_sends == 0)
    result = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert result.ok is True, result


async def _count_named_join_waiters() -> int:
    return sum(
        1
        for t in asyncio.all_tasks()
        if t.get_name() == "telegram-sender-queue-join-waiter" and not t.done()
    )


def test_sf1_sentinel_put_failure_not_intake_counter(monkeypatch) -> None:
    """BLOCKER 3: sentinel put failure must not bump D27/D28 intake total."""

    proof = _claim()
    before = tg._terminal_intake_failure_total
    owned = tg._worker_owned_queue or tg.queue
    real_put = owned.put_nowait

    def boom(item):
        if isinstance(item, tg._WorkerStopSentinel):
            raise RuntimeError("sentinel put boom")
        return real_put(item)

    monkeypatch.setattr(owned, "put_nowait", boom)
    first = _run(tg.drain_and_stop_sender_worker(proof, timeout=3.0))
    assert first.ok is False
    assert first.reason.startswith("sentinel_submit_failed:")
    assert tg._terminal_intake_failure_total == before
    assert not any("sentinel_put" in e for e in tg._recent_intake_failures)
    monkeypatch.setattr(owned, "put_nowait", real_put)
    second = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert second.ok is True, second
    assert second.worker_stopped is True
    assert tg._terminal_intake_failure_total == before


def test_wt2_worker_cancelled_structured_not_caller_cancel() -> None:
    """Worker Task cancel during terminal observe → structured failure, not CancelledError."""

    release = threading.Event()

    async def body():
        while not release.is_set():
            await asyncio.sleep(0.01)

    async def start():
        return asyncio.create_task(body(), name="wt2-worker")

    owned = asyncio.run_coroutine_threadsafe(start(), tg.loop).result(timeout=2.0)
    prev = tg._worker_task
    tg._worker_task = owned
    try:

        async def observe_then_cancel_worker():
            wait_task = asyncio.create_task(
                tg._run_on_sender_loop(
                    tg._await_worker_terminal, time.monotonic() + 5.0
                )
            )
            for _ in range(10):
                await asyncio.sleep(0)
            tg._real_call_soon_threadsafe(owned.cancel)
            outcome = await wait_task
            assert isinstance(outcome, tg.WorkerTerminalOutcome)
            assert outcome.clean is False
            assert outcome.reason == "unexpected_dead_worker"
            assert outcome.diagnostic == "worker_cancelled"
            return outcome

        outcome = _run(observe_then_cancel_worker())
        assert outcome.clean is False
        assert owned.cancelled()
    finally:
        tg._worker_task = prev
        release.set()

    # Fresh worker for full drain path after ENQUEUED.
    with tg._lifecycle_lock:
        tg._lifecycle_state = "DRAINING"
        tg._intake_sealed = True
        tg._sentinel_state = tg._SENTINEL_ENQUEUED
        tg._sentinel_ack.set()
    tg._reset_sender_worker_lifecycle_for_tests()
    proof = _claim()

    real_enqueue = tg._enqueue_sentinel_on_loop

    def enqueue_then_cancel_worker() -> None:
        real_enqueue()
        worker = tg._worker_task
        if worker is not None and not worker.done():
            worker.cancel()

    async def drain_cancel_worker():
        tg._enqueue_sentinel_on_loop = enqueue_then_cancel_worker  # type: ignore[assignment]
        try:
            return await tg.drain_and_stop_sender_worker(proof, timeout=5.0)
        finally:
            tg._enqueue_sentinel_on_loop = real_enqueue  # type: ignore[assignment]

    result = _run(drain_cancel_worker())
    assert result.ok is False, result
    assert result.reason == "unexpected_dead_worker"
    assert result.worker_terminal is True
    assert result.worker_stopped is False
    assert result.full_resource_stopped is False
    assert tg._lifecycle_state == "DRAINING"


def test_wt3_worker_exception_structured_no_raw_escape() -> None:
    """Worker Task exception during terminal observe → structured failure."""

    async def boom():
        raise RuntimeError("worker boom")

    async def start():
        return asyncio.create_task(boom(), name="wt3-worker")

    owned = asyncio.run_coroutine_threadsafe(start(), tg.loop).result(timeout=2.0)
    _wait_until(lambda: owned.done())
    prev = tg._worker_task
    tg._worker_task = owned
    try:

        async def observe():
            return await tg._run_on_sender_loop(
                tg._await_worker_terminal, time.monotonic() + 2.0
            )

        outcome = _run(observe())
        assert isinstance(outcome, tg.WorkerTerminalOutcome)
        assert outcome.clean is False
        assert outcome.reason == "unexpected_dead_worker"
        assert outcome.diagnostic == "RuntimeError"
    finally:
        tg._worker_task = prev

    with tg._lifecycle_lock:
        tg._lifecycle_state = "DRAINING"
        tg._intake_sealed = True
        tg._sentinel_state = tg._SENTINEL_ENQUEUED
        tg._sentinel_ack.set()
    tg._reset_sender_worker_lifecycle_for_tests()
    proof = _claim()

    real_enqueue = tg._enqueue_sentinel_on_loop

    def enqueue_then_swap_failed_worker() -> None:
        real_enqueue()

        async def boom2():
            raise RuntimeError("worker boom")

        failed = asyncio.get_running_loop().create_task(
            boom2(), name="wt3-drain-worker"
        )
        # Attach failure synchronously on this loop tick before waiter observes.
        tg._worker_task = failed

    async def drain_with_failed_worker():
        tg._enqueue_sentinel_on_loop = enqueue_then_swap_failed_worker  # type: ignore[assignment]
        try:
            return await tg.drain_and_stop_sender_worker(proof, timeout=5.0)
        finally:
            tg._enqueue_sentinel_on_loop = real_enqueue  # type: ignore[assignment]

    result = _run(drain_with_failed_worker())
    assert result.ok is False, result
    assert result.reason == "unexpected_dead_worker"
    assert result.worker_terminal is True
    assert result.worker_stopped is False
    assert tg._lifecycle_state == "DRAINING"


def _patch_first_terminal_wait_timeout(monkeypatch) -> None:
    real = tg._run_on_sender_loop
    calls = {"n": 0}

    async def run(factory, deadline):
        if factory is tg._await_worker_terminal:
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("wt synthetic terminal wait timeout")
        return await real(factory, deadline)

    monkeypatch.setattr(tg, "_run_on_sender_loop", run)


def _stop_previous_worker_task() -> None:
    prev = tg._worker_task
    if prev is not None and not prev.done():
        tg._real_call_soon_threadsafe(prev.cancel)
        _wait_until(lambda: prev.done())


def _install_hang_after_sentinel_worker() -> tuple[asyncio.Event, asyncio.Task]:
    """Worker consumes sentinel (queue join ok) then hangs until released."""

    _stop_previous_worker_task()
    hang_holder: list[asyncio.Event] = []

    async def hang_worker() -> None:
        hang = asyncio.Event()
        hang_holder.append(hang)
        owned = tg.queue
        tg._worker_owned_queue = owned
        while True:
            item = await owned.get()
            try:
                if isinstance(item, tg._WorkerStopSentinel):
                    break
            finally:
                owned.task_done()
        await hang.wait()

    async def start():
        return asyncio.create_task(hang_worker(), name="wt-hang-after-sentinel")

    owned = asyncio.run_coroutine_threadsafe(start(), tg.loop).result(timeout=2.0)
    tg._worker_task = owned
    _wait_until(lambda: len(hang_holder) == 1)
    return hang_holder[0], owned


def _install_crash_after_sentinel_accounted_worker() -> asyncio.Task:
    """Worker finishes sentinel queue accounting then exits with RuntimeError."""

    _stop_previous_worker_task()

    async def crash_worker() -> None:
        owned = tg.queue
        tg._worker_owned_queue = owned
        while True:
            item = await owned.get()
            try:
                if isinstance(item, tg._WorkerStopSentinel):
                    break
            finally:
                owned.task_done()
        raise RuntimeError("wt5 repeat boom")

    async def start():
        return asyncio.create_task(crash_worker(), name="wt-crash-after-sentinel")

    owned = asyncio.run_coroutine_threadsafe(start(), tg.loop).result(timeout=2.0)
    tg._worker_task = owned
    return owned


def _release_hang_on_sender_loop(hang: asyncio.Event) -> None:
    tg._real_call_soon_threadsafe(hang.set)


def test_wt4_repeat_after_terminal_timeout_worker_cancelled(monkeypatch) -> None:
    """Repeat path: ENQUEUED + abnormal worker cancel must not claim WORKER_STOPPED."""

    proof = _claim()
    _install_hang_after_sentinel_worker()
    _patch_first_terminal_wait_timeout(monkeypatch)

    first = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert first.ok is False, first
    assert first.reason == "deadline_worker_terminal"
    assert tg._sentinel_state == tg._SENTINEL_ENQUEUED
    assert tg._lifecycle_state == "DRAINING"

    worker = tg._worker_task
    assert worker is not None and not worker.done()
    tg._real_call_soon_threadsafe(worker.cancel)
    _wait_until(lambda: worker.done())
    assert worker.cancelled()

    second = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert second.ok is False, second
    assert second.reason == "unexpected_dead_worker"
    assert second.worker_terminal is True
    assert second.worker_stopped is False
    assert tg._lifecycle_state == "DRAINING"


def test_wt5_repeat_after_terminal_timeout_worker_exception(monkeypatch) -> None:
    """Repeat path: ENQUEUED + worker exception must stay structured failure."""

    proof = _claim()
    owned = _install_crash_after_sentinel_accounted_worker()
    _patch_first_terminal_wait_timeout(monkeypatch)

    first = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert first.ok is False, first
    assert first.reason == "deadline_worker_terminal"
    assert tg._lifecycle_state == "DRAINING"

    _wait_until(lambda: owned.done())
    assert owned.exception() is not None

    second = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert second.ok is False, second
    assert second.reason == "unexpected_dead_worker"
    assert second.worker_terminal is True
    assert second.worker_stopped is False
    assert tg._lifecycle_state == "DRAINING"


def test_wt6_repeat_after_terminal_timeout_clean_worker_success(monkeypatch) -> None:
    """Repeat path: ENQUEUED + clean worker terminal promotes WORKER_STOPPED."""

    proof = _claim()
    hang, worker = _install_hang_after_sentinel_worker()
    _patch_first_terminal_wait_timeout(monkeypatch)

    first = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert first.ok is False, first
    assert first.reason == "deadline_worker_terminal"
    assert tg._sentinel_state == tg._SENTINEL_ENQUEUED
    assert tg._lifecycle_state == "DRAINING"
    assert not worker.done()

    _release_hang_on_sender_loop(hang)
    _wait_until(lambda: worker.done())
    assert worker.exception() is None
    assert not worker.cancelled()

    second = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert second.ok is True, second
    assert second.worker_stopped is True
    assert tg._lifecycle_state == "WORKER_STOPPED"
    assert second.loop_running is True
    assert second.loop_thread_alive is True
