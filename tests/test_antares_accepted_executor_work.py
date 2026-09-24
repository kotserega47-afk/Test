"""TASK-40 registry of Accepted executor Futures (not full drain)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.job_dispatch import _reset_job_executor_for_tests
from core.job_runner import Actor
from modules.antares.application_lifecycle import run_ptb_lifecycle
from modules.antares.work_admission import (
    AdmissionAccepted,
    AdmissionRejected,
    AdmittedJob,
    AdmissionState,
    WorkAdmission,
    bind_antares_admission,
    reset_antares_admission_for_tests,
    watch_admitted_future,
)

_INVISIBLE_FUTURE_EXIT = 7
_CALLBACK_UNDER_LOCK_READY = "CALLBACK_UNDER_LOCK_READY"
_INVISIBLE_FUTURE = "INVISIBLE_FUTURE"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_THIS_FILE = Path(__file__).resolve()


@pytest.fixture(autouse=True)
def _reset_admission() -> None:
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    yield
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()


def _open_bound() -> WorkAdmission:
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    return admission


def _join_thread(
    thread: threading.Thread | None, bucket: list[BaseException]
) -> None:
    if thread is None:
        return
    try:
        thread.join(timeout=5)
        if thread.is_alive():
            bucket.append(AssertionError(f"thread {thread.name!r} still alive"))
    except BaseException as exc:
        bucket.append(exc)


def _join(thread: threading.Thread | None, errors: list[BaseException]) -> None:
    if thread is None:
        return
    thread.join(timeout=5)
    assert not thread.is_alive(), f"thread {thread.name!r} still alive"
    if errors:
        raise errors[0]


def _child_output(obj: object) -> str:
    chunks: list[str] = []
    for attr in ("stdout", "stderr", "output"):
        raw = getattr(obj, attr, None)
        if not raw:
            continue
        chunks.append(raw if isinstance(raw, str) else raw.decode())
    return "".join(chunks)


def _waiter_count(admission: WorkAdmission) -> int:
    with admission._lock:
        return len(admission._accepted_executor_waiters)


def _continuation_states(admission: WorkAdmission) -> list[str]:
    with admission._lock:
        return [record.state for record in admission._ae_continuations.values()]


class _ObservedAdmissionLock:
    """Gate acquire/release of the real admission Lock for submit vs seal threads."""

    def __init__(self, inner: threading.Lock) -> None:
        self._inner = inner
        self.submit_thread: threading.Thread | None = None
        self.seal_thread: threading.Thread | None = None
        self.submit_at_gate = threading.Event()
        self.seal_at_gate = threading.Event()
        self.submit_entering_inner = threading.Event()
        self.seal_entering_inner = threading.Event()
        self.submit_acquired = threading.Event()
        self.seal_acquired = threading.Event()
        self.submit_may_acquire = threading.Event()
        self.seal_may_acquire = threading.Event()
        self.submit_may_release = threading.Event()
        self.seal_may_release = threading.Event()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        me = threading.current_thread()
        if me is self.submit_thread:
            self.submit_at_gate.set()
            if not self.submit_may_acquire.wait(timeout=5):
                raise AssertionError("submit was not allowed to acquire")
            self.submit_entering_inner.set()
        elif me is self.seal_thread:
            self.seal_at_gate.set()
            if not self.seal_may_acquire.wait(timeout=5):
                raise AssertionError("seal was not allowed to acquire")
            self.seal_entering_inner.set()
        ok = self._inner.acquire(blocking, timeout)
        if ok:
            if me is self.submit_thread:
                self.submit_acquired.set()
            elif me is self.seal_thread:
                self.seal_acquired.set()
        return ok

    def release(self) -> None:
        me = threading.current_thread()
        if me is self.submit_thread:
            if not self.submit_may_release.wait(timeout=5):
                raise AssertionError("submit was not allowed to release")
        elif me is self.seal_thread:
            if not self.seal_may_release.wait(timeout=5):
                raise AssertionError("seal was not allowed to release")
        self._inner.release()

    def __enter__(self) -> _ObservedAdmissionLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> bool:
        self.release()
        return False

    def unlock_all(self) -> None:
        self.submit_may_acquire.set()
        self.seal_may_acquire.set()
        self.submit_may_release.set()
        self.seal_may_release.set()


def _install_observed_lock(admission: WorkAdmission) -> _ObservedAdmissionLock:
    observed = _ObservedAdmissionLock(admission._lock)
    admission._lock = observed  # type: ignore[method-assign]
    return observed


def _arm_register(admission: WorkAdmission) -> threading.Event:
    registered = threading.Event()
    orig = admission._register_accepted_executor_future

    def _wrapped(future: Future) -> None:
        orig(future)
        registered.set()

    admission._register_accepted_executor_future = _wrapped  # type: ignore[method-assign]
    return registered


async def _await_thread_event(event: threading.Event, timeout: float = 5) -> None:
    loop = asyncio.get_running_loop()
    signal: asyncio.Future = loop.create_future()
    errors: list[BaseException] = []

    def _watch() -> None:
        try:
            if not event.wait(timeout):
                raise AssertionError("threading.Event wait timed out")
            loop.call_soon_threadsafe(lambda: signal.done() or signal.set_result(None))
        except BaseException as exc:
            errors.append(exc)

            def _fail(err: BaseException = exc) -> None:
                if not signal.done():
                    signal.set_exception(err)

            loop.call_soon_threadsafe(_fail)

    thread = threading.Thread(target=_watch, name="t40-event-watch")
    try:
        thread.start()
        await signal
    finally:
        event.set()
        _join(thread, errors)


def _run_child(mode: str, timeout: float) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_REPO_ROOT), env.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [sys.executable, str(_THIS_FILE), mode],
        cwd=str(_REPO_ROOT),
        timeout=timeout,
        capture_output=True,
        text=True,
        env=env,
    )


def test_lifecycle_helper_does_not_wait_accepted_executor_work() -> None:
    import inspect

    source = inspect.getsource(run_ptb_lifecycle)
    assert "wait_accepted_executor_work" not in source


def test_controlled_submit_wins_seal_sees_registered_future() -> None:
    release = threading.Event()
    errors: list[BaseException] = []
    pending: list[BaseException] = []
    result: dict[str, object] = {}
    submit_calls: list[int] = []
    submit_thread: threading.Thread | None = None
    seal_thread: threading.Thread | None = None
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-sw")
    admission = _open_bound()
    gate = _install_observed_lock(admission)
    registered = _arm_register(admission)

    def _work() -> str:
        try:
            assert release.wait(timeout=5)
            return "ok"
        except BaseException as exc:
            errors.append(exc)
            raise

    class _Exec:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            submit_calls.append(1)
            return pool.submit(fn, *args, **kwargs)

    def _submit() -> None:
        try:
            result["outcome"] = admission.submit_if_open(_Exec(), _work)
        except BaseException as exc:
            errors.append(exc)

    def _seal() -> None:
        try:
            admission.seal()
            result["after_seal"] = admission.accepted_executor_futures()
        except BaseException as exc:
            errors.append(exc)

    submit_thread = threading.Thread(target=_submit, name="t40-submit")
    seal_thread = threading.Thread(target=_seal, name="t40-seal")
    gate.submit_thread = submit_thread
    gate.seal_thread = seal_thread
    try:
        gate.submit_may_acquire.set()
        submit_thread.start()
        assert gate.submit_acquired.wait(timeout=5)
        assert registered.wait(timeout=5)
        seal_thread.start()
        assert gate.seal_at_gate.wait(timeout=5)
        assert result.get("after_seal") is None
        gate.seal_may_acquire.set()
        assert gate.seal_entering_inner.wait(timeout=5)
        assert result.get("after_seal") is None
        gate.submit_may_release.set()
        gate.seal_may_release.set()
        _join_thread(seal_thread, pending)
        _join_thread(submit_thread, pending)
        seal_thread = None
        submit_thread = None
        after = result["after_seal"]
        outcome = result["outcome"]
        assert isinstance(after, tuple)
        assert isinstance(outcome, AdmissionAccepted)
        assert outcome.future in after
        assert submit_calls == [1]
        assert outcome.future in admission.accepted_executor_futures()
        release.set()
        asyncio.run(admission.wait_accepted_executor_work())
        assert outcome.future.result() == "ok"
    except BaseException as exc:
        pending.append(exc)
    finally:
        try:
            gate.unlock_all()
        except BaseException as exc:
            pending.append(exc)
        release.set()
        _join_thread(seal_thread, pending)
        _join_thread(submit_thread, pending)
        try:
            pool.shutdown(wait=True)
        except BaseException as exc:
            pending.append(exc)
        pending.extend(errors)
        if pending:
            raise pending[0]


def test_controlled_seal_wins_executor_submit_not_called() -> None:
    errors: list[BaseException] = []
    result: dict[str, object] = {}
    submit_calls: list[int] = []
    submit_thread: threading.Thread | None = None
    seal_thread: threading.Thread | None = None
    admission = _open_bound()
    gate = _install_observed_lock(admission)

    class _Exec:
        def submit(self, *args, **kwargs):  # noqa: ANN002, ANN003
            submit_calls.append(1)
            raise AssertionError("seal won; executor.submit must not run")

    def _submit() -> None:
        try:
            result["outcome"] = admission.submit_if_open(_Exec(), lambda: 1)
        except BaseException as exc:
            errors.append(exc)

    def _seal() -> None:
        try:
            admission.seal()
        except BaseException as exc:
            errors.append(exc)

    submit_thread = threading.Thread(target=_submit, name="t40-submit")
    seal_thread = threading.Thread(target=_seal, name="t40-seal")
    gate.submit_thread = submit_thread
    gate.seal_thread = seal_thread
    try:
        gate.seal_may_acquire.set()
        seal_thread.start()
        assert gate.seal_acquired.wait(timeout=5)
        submit_thread.start()
        assert gate.submit_at_gate.wait(timeout=5)
        gate.submit_may_acquire.set()
        assert gate.submit_entering_inner.wait(timeout=5)
        assert submit_calls == []
        gate.seal_may_release.set()
        gate.submit_may_release.set()
        _join(seal_thread, errors)
        seal_thread = None
        _join(submit_thread, errors)
        submit_thread = None
        assert isinstance(result["outcome"], AdmissionRejected)
        assert submit_calls == []
        assert admission.accepted_executor_futures() == ()
    finally:
        gate.unlock_all()
        _join(seal_thread, errors)
        _join(submit_thread, errors)
        if errors:
            raise errors[0]


def test_concurrent_submit_seal_keeps_registry_invariant() -> None:
    release = threading.Event()
    start = threading.Barrier(3)
    errors: list[BaseException] = []
    submit_calls: list[int] = []
    result: dict[str, object] = {}
    submit_thread: threading.Thread | None = None
    seal_thread: threading.Thread | None = None

    def _work() -> str:
        try:
            assert release.wait(timeout=5)
            return "raced"
        except BaseException as exc:
            errors.append(exc)
            raise

    class _Exec:
        def __init__(self, inner: ThreadPoolExecutor) -> None:
            self._inner = inner

        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            submit_calls.append(1)
            return self._inner.submit(fn, *args, **kwargs)

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-race")
    exec_ = _Exec(pool)

    def _submit() -> None:
        try:
            start.wait(timeout=5)
            result["outcome"] = admission.submit_if_open(exec_, _work)
        except BaseException as exc:
            errors.append(exc)

    def _seal() -> None:
        try:
            start.wait(timeout=5)
            admission.seal()
        except BaseException as exc:
            errors.append(exc)

    submit_thread = threading.Thread(target=_submit, name="t40-race-submit")
    seal_thread = threading.Thread(target=_seal, name="t40-race-seal")
    try:
        submit_thread.start()
        seal_thread.start()
        start.wait(timeout=5)
        _join(submit_thread, errors)
        submit_thread = None
        _join(seal_thread, errors)
        seal_thread = None
        outcome = result.get("outcome")
        if isinstance(outcome, AdmissionAccepted):
            assert submit_calls == [1]
            assert outcome.future in admission.accepted_executor_futures()
        elif isinstance(outcome, AdmissionRejected):
            assert submit_calls == []
            assert admission.accepted_executor_futures() == ()
        else:
            raise AssertionError(f"missing outcome: {outcome!r} errors={errors!r}")
        release.set()
        asyncio.run(admission.wait_accepted_executor_work())
    finally:
        release.set()
        _join(submit_thread, errors)
        _join(seal_thread, errors)
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_already_done_future_callback_without_deadlock_subprocess() -> None:
    proc = _run_child("already_done", timeout=8)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SUBMIT_RETURNED" in proc.stdout
    assert "WAIT_DONE" in proc.stdout


def test_mutation_callback_under_lock_fails_in_subprocess_not_hanging_pytest() -> None:
    try:
        proc = _run_child("callback_under_lock", timeout=3)
    except subprocess.TimeoutExpired as exc:
        text = _child_output(exc)
        assert _CALLBACK_UNDER_LOCK_READY in text
        assert "UNEXPECTED_RETURN" not in text
        return
    raise AssertionError(
        f"callback-under-lock child exited {proc.returncode}: {_child_output(proc)}"
    )


def test_mutation_register_after_unlock_detected_in_subprocess() -> None:
    try:
        proc = _run_child("register_after_unlock", timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"register-after-unlock harness hung: {_child_output(exc)}"
        ) from exc
    text = _child_output(proc)
    assert _INVISIBLE_FUTURE in text, text
    assert proc.returncode == _INVISIBLE_FUTURE_EXIT, text


def test_submit_exception_does_not_register_or_leak_continuation() -> None:
    admission = _open_bound()

    class _Boom:
        def submit(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("submit-fail")

    with pytest.raises(RuntimeError, match="submit-fail"):
        admission.submit_if_open(_Boom(), lambda: 1)
    assert admission.accepted_executor_futures() == ()

    with pytest.raises(RuntimeError, match="submit-fail"):
        admission.submit_auto_enable_run_if_open(_Boom(), lambda: 1)
    assert admission._ae_continuations == {}
    assert admission.accepted_executor_futures() == ()


def test_queued_and_running_futures_stay_registered() -> None:
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()
    second_release = threading.Event()
    errors: list[BaseException] = []

    def _first() -> str:
        try:
            first_started.set()
            assert first_release.wait(timeout=5)
            return "first"
        except BaseException as exc:
            errors.append(exc)
            raise

    def _second() -> str:
        try:
            second_started.set()
            assert second_release.wait(timeout=5)
            return "second"
        except BaseException as exc:
            errors.append(exc)
            raise

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-q")
    try:
        a = admission.submit_if_open(pool, _first)
        b = admission.submit_if_open(pool, _second)
        assert isinstance(a, AdmissionAccepted)
        assert isinstance(b, AdmissionAccepted)
        assert first_started.wait(timeout=5)
        assert second_started.is_set() is False
        tracked = set(admission.accepted_executor_futures())
        assert tracked == {a.future, b.future}
        first_release.set()
        assert second_started.wait(timeout=5)
        assert set(admission.accepted_executor_futures()) == {b.future}
        second_release.set()
        asyncio.run(admission.wait_accepted_executor_work())
        assert a.future.result() == "first"
        assert b.future.result() == "second"
    finally:
        first_release.set()
        second_release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_callable_exception_completes_accounting_and_stays_visible() -> None:
    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-exc")
    try:

        def _boom() -> None:
            raise ValueError("job-boom")

        outcome = admission.submit_if_open(pool, _boom)
        assert isinstance(outcome, AdmissionAccepted)
        asyncio.run(admission.wait_accepted_executor_work())
        assert admission.accepted_executor_futures() == ()
        with pytest.raises(ValueError, match="job-boom"):
            outcome.future.result()
    finally:
        pool.shutdown(wait=True)


def test_submit_job_if_open_registers_future(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    errors: list[BaseException] = []

    def _job(*args, **kwargs):  # noqa: ANN002, ANN003
        try:
            assert release.wait(timeout=5)
            return "job-ok"
        except BaseException as exc:
            errors.append(exc)
            raise

    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    admission = _open_bound()
    try:
        outcome = admission.submit_job_if_open("run_wallet", Actor(kind="test"))
        assert isinstance(outcome, AdmissionAccepted)
        assert outcome.future in admission.accepted_executor_futures()
        release.set()
        asyncio.run(admission.wait_accepted_executor_work())
        assert outcome.future.result() == "job-ok"
    finally:
        release.set()
        if errors:
            raise errors[0]


def test_cancel_handler_after_real_wait_does_not_drop_accepted_work() -> None:
    release = threading.Event()
    entered = threading.Event()
    errors: list[BaseException] = []

    def _work() -> str:
        try:
            assert release.wait(timeout=5)
            return "kept"
        except BaseException as exc:
            errors.append(exc)
            raise

    class _EnteredWait(asyncio.Future):
        def __await__(self):
            entered.set()
            return super().__await__()

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-h")
    try:
        outcome = admission.submit_if_open(pool, _work)
        assert isinstance(outcome, AdmissionAccepted)

        async def _run() -> None:
            admitted = watch_admitted_future(
                outcome.future, MagicMock(), work="direct"
            )
            assert admitted.wait.__func__ is AdmittedJob.wait
            admitted._af = _EnteredWait(loop=admitted._loop)
            task = asyncio.create_task(admitted.wait())
            await _await_thread_event(entered)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert outcome.future.cancelled() is False
            assert outcome.future in admission.accepted_executor_futures()
            release.set()
            await admission.wait_accepted_executor_work()
            assert admitted.job_error is None

        asyncio.run(_run())
        assert outcome.future.result() == "kept"
    finally:
        entered.set()
        release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_wait_timeout_and_repeat_keeps_queued_future_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occupy_started = threading.Event()
    occupy_release = threading.Event()
    queued_started = threading.Event()
    queued_release = threading.Event()
    errors: list[BaseException] = []
    runs = {"queued": 0}
    callback_count = {"n": 0}
    orig = Future.add_done_callback

    def _counted(self, fn):  # noqa: ANN001
        target = getattr(fn, "__func__", fn)
        if target is WorkAdmission._on_accepted_executor_future_done:
            callback_count["n"] += 1
        return orig(self, fn)

    monkeypatch.setattr(Future, "add_done_callback", _counted)

    def _occupy() -> None:
        try:
            occupy_started.set()
            assert occupy_release.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
            raise

    def _queued() -> str:
        try:
            runs["queued"] += 1
            queued_started.set()
            assert queued_release.wait(timeout=5)
            return "once"
        except BaseException as exc:
            errors.append(exc)
            raise

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-c")
    try:
        occupy = admission.submit_if_open(pool, _occupy)
        queued = admission.submit_if_open(pool, _queued)
        assert isinstance(occupy, AdmissionAccepted)
        assert isinstance(queued, AdmissionAccepted)
        assert occupy_started.wait(timeout=5)
        assert queued_started.is_set() is False
        assert callback_count["n"] == 2

        async def _run() -> None:
            admission._accepted_executor_wait_armed.clear()
            first = asyncio.create_task(admission.wait_accepted_executor_work())
            await _await_thread_event(admission._accepted_executor_wait_armed)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert queued.future.cancelled() is False
            assert queued.future in admission.accepted_executor_futures()
            assert _waiter_count(admission) == 0
            assert callback_count["n"] == 2

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    admission.wait_accepted_executor_work(), timeout=0.05
                )
            assert queued.future.cancelled() is False
            assert queued.future in admission.accepted_executor_futures()
            assert _waiter_count(admission) == 0
            assert callback_count["n"] == 2
            assert runs["queued"] == 0

            occupy_release.set()
            queued_release.set()
            await admission.wait_accepted_executor_work()
            assert queued.future.result() == "once"
            assert runs["queued"] == 1
            assert callback_count["n"] == 2

        asyncio.run(_run())
    finally:
        occupy_release.set()
        queued_release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_cancel_wait_keeps_ae_continuation_until_wrapper_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occupy_started = threading.Event()
    occupy_release = threading.Event()
    ae_started = threading.Event()
    ae_release = threading.Event()
    errors: list[BaseException] = []
    callback_count = {"n": 0}
    orig = Future.add_done_callback

    def _counted(self, fn):  # noqa: ANN001
        target = getattr(fn, "__func__", fn)
        if target is WorkAdmission._on_accepted_executor_future_done:
            callback_count["n"] += 1
        return orig(self, fn)

    monkeypatch.setattr(Future, "add_done_callback", _counted)

    def _occupy() -> None:
        try:
            occupy_started.set()
            assert occupy_release.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
            raise

    def _ae() -> str:
        try:
            ae_started.set()
            assert ae_release.wait(timeout=5)
            return "ae-ok"
        except BaseException as exc:
            errors.append(exc)
            raise

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-ae")
    try:
        occupy = admission.submit_if_open(pool, _occupy)
        assert isinstance(occupy, AdmissionAccepted)
        assert occupy_started.wait(timeout=5)
        outcome = admission.submit_auto_enable_run_if_open(pool, _ae)
        assert isinstance(outcome, AdmissionAccepted)
        assert _continuation_states(admission) == ["pending"]
        assert callback_count["n"] == 2

        async def _run() -> None:
            admission._accepted_executor_wait_armed.clear()
            pending_wait = asyncio.create_task(
                admission.wait_accepted_executor_work()
            )
            await _await_thread_event(admission._accepted_executor_wait_armed)
            pending_wait.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending_wait
            assert outcome.future.cancelled() is False
            assert _continuation_states(admission) == ["pending"]
            assert callback_count["n"] == 2

            occupy_release.set()
            assert ae_started.wait(timeout=5)
            assert _continuation_states(admission) == ["active"]

            admission._accepted_executor_wait_armed.clear()
            active_wait = asyncio.create_task(
                admission.wait_accepted_executor_work()
            )
            await _await_thread_event(admission._accepted_executor_wait_armed)
            active_wait.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active_wait
            assert _continuation_states(admission) == ["active"]
            assert callback_count["n"] == 2

            ae_release.set()
            await admission.wait_accepted_executor_work()
            assert outcome.future.result() == "ae-ok"
            assert admission._ae_continuations == {}
            assert callback_count["n"] == 2

        asyncio.run(_run())
    finally:
        occupy_release.set()
        ae_release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_pending_auto_enable_after_seal_starts_and_revokes() -> None:
    occupy_started = threading.Event()
    occupy_release = threading.Event()
    ae_started = threading.Event()
    ae_release = threading.Event()
    errors: list[BaseException] = []

    def _occupy() -> None:
        try:
            occupy_started.set()
            assert occupy_release.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
            raise

    def _ae() -> str:
        try:
            ae_started.set()
            assert ae_release.wait(timeout=5)
            return "ae-ok"
        except BaseException as exc:
            errors.append(exc)
            raise

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-ae2")
    try:
        occupy = admission.submit_if_open(pool, _occupy)
        assert isinstance(occupy, AdmissionAccepted)
        assert occupy_started.wait(timeout=5)
        outcome = admission.submit_auto_enable_run_if_open(pool, _ae)
        assert isinstance(outcome, AdmissionAccepted)
        assert _continuation_states(admission) == ["pending"]
        admission.seal()
        assert ae_started.is_set() is False
        occupy_release.set()
        assert ae_started.wait(timeout=5)
        assert _continuation_states(admission) == ["active"]
        ae_release.set()
        asyncio.run(admission.wait_accepted_executor_work())
        assert outcome.future.result() == "ae-ok"
        assert admission._ae_continuations == {}
        assert admission.accepted_executor_futures() == ()
    finally:
        occupy_release.set()
        ae_release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def _already_done_child() -> None:
    reset_antares_admission_for_tests()
    admission = _open_bound()

    class _Inline:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            future: Future = Future()
            future.set_result(fn(*args, **kwargs))
            return future

    outcome = admission.submit_if_open(_Inline(), lambda: "inline")
    print("SUBMIT_RETURNED", flush=True)
    assert isinstance(outcome, AdmissionAccepted)
    assert outcome.future.result() == "inline"
    asyncio.run(admission.wait_accepted_executor_work())
    print("WAIT_DONE", flush=True)
    reset_antares_admission_for_tests()


def _callback_under_lock_child() -> None:
    reset_antares_admission_for_tests()
    admission = _open_bound()

    def _mutated(self, executor, fn, /, *args, **kwargs):  # noqa: ANN001
        with self._lock:
            if self._state is not AdmissionState.OPEN:
                return AdmissionRejected(self._state)
            future = executor.submit(fn, *args, **kwargs)
            self._register_accepted_executor_future(future)
            self._attach_accepted_executor_callback(future)
            return AdmissionAccepted(future)

    admission.submit_if_open = _mutated.__get__(admission, WorkAdmission)  # type: ignore[method-assign]

    class _Inline:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            future: Future = Future()
            future.set_result(fn(*args, **kwargs))
            return future

    print(_CALLBACK_UNDER_LOCK_READY, flush=True)
    admission.submit_if_open(_Inline(), lambda: "deadlock")
    print("UNEXPECTED_RETURN", flush=True)


def _register_after_unlock_child() -> None:
    reset_antares_admission_for_tests()
    release = threading.Event()
    gap = threading.Event()
    errors: list[BaseException] = []
    result: dict[str, object] = {}
    submit_thread: threading.Thread | None = None
    seal_thread: threading.Thread | None = None
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-mut")
    admission = _open_bound()
    gate = _install_observed_lock(admission)

    def _mutated(self, executor, fn, /, *args, **kwargs):  # noqa: ANN001
        with self._lock:
            if self._state is not AdmissionState.OPEN:
                return AdmissionRejected(self._state)
            future = executor.submit(fn, *args, **kwargs)
        assert gap.wait(timeout=5)
        self._register_accepted_executor_future(future)
        self._attach_accepted_executor_callback(future)
        return AdmissionAccepted(future)

    admission.submit_if_open = _mutated.__get__(admission, WorkAdmission)  # type: ignore[method-assign]
    registered = _arm_register(admission)

    def _work() -> str:
        assert release.wait(timeout=5)
        return "ok"

    class _Exec:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            return pool.submit(fn, *args, **kwargs)

    def _submit() -> None:
        try:
            result["outcome"] = admission.submit_if_open(_Exec(), _work)
        except BaseException as exc:
            errors.append(exc)

    def _seal() -> None:
        try:
            admission.seal()
            result["after_seal"] = admission.accepted_executor_futures()
        except BaseException as exc:
            errors.append(exc)

    submit_thread = threading.Thread(target=_submit, name="t40-submit")
    seal_thread = threading.Thread(target=_seal, name="t40-seal")
    gate.submit_thread = submit_thread
    gate.seal_thread = seal_thread
    try:
        gate.submit_may_acquire.set()
        submit_thread.start()
        assert gate.submit_acquired.wait(timeout=5)
        assert registered.wait(timeout=0.2) is False
        seal_thread.start()
        assert gate.seal_at_gate.wait(timeout=5)
        gate.seal_may_acquire.set()
        assert gate.seal_entering_inner.wait(timeout=5)
        gate.submit_may_release.set()
        gate.seal_may_release.set()
        _join(seal_thread, errors)
        seal_thread = None
        after = result.get("after_seal")
        if after != ():
            raise AssertionError(f"expected empty registry at seal return, got {after!r}")
        print(_INVISIBLE_FUTURE, flush=True)
        raise SystemExit(_INVISIBLE_FUTURE_EXIT)
    finally:
        gap.set()
        gate.unlock_all()
        release.set()
        join_bucket: list[BaseException] = []
        _join_thread(seal_thread, join_bucket)
        _join_thread(submit_thread, join_bucket)
        try:
            pool.shutdown(wait=True)
        except BaseException as exc:
            join_bucket.append(exc)
        reset_antares_admission_for_tests()
        if join_bucket and sys.exc_info()[0] is not SystemExit:
            raise join_bucket[0]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "already_done":
        _already_done_child()
    elif mode == "callback_under_lock":
        _callback_under_lock_child()
    elif mode == "register_after_unlock":
        _register_after_unlock_child()
    else:
        raise SystemExit(f"unknown child mode {mode!r}")
