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
        tg._sentinel_submitted = True
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
        tg._sentinel_submitted = True
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
        tg._sentinel_submitted = False
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
    assert tg._sentinel_submitted is False
    assert tg._intake_sealed is True
    hold.set()
    _wait_until(lambda: tg._active_user_sends == 0)
    owned = tg._worker_owned_queue or tg.queue
    fut = asyncio.run_coroutine_threadsafe(owned.join(), tg.loop)
    fut.result(timeout=2.0)
    assert tg._worker_task is not None and not tg._worker_task.done()
    second = _run(tg.drain_and_stop_sender_worker(proof, timeout=5.0))
    assert second.ok is True, second
    assert tg._sentinel_submitted is True


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
