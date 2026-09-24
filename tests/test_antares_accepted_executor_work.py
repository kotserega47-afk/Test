"""TASK-40 registry of Accepted executor Futures (not full drain)."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from core.job_dispatch import _reset_job_executor_for_tests, get_job_executor
from core.job_runner import Actor
from modules.antares.application_lifecycle import run_ptb_lifecycle
from modules.antares.work_admission import (
    AdmissionAccepted,
    AdmissionRejected,
    WorkAdmission,
    bind_antares_admission,
    reset_antares_admission_for_tests,
    watch_admitted_future,
)


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


def _join(thread: threading.Thread, errors: list[BaseException]) -> None:
    thread.join(timeout=5)
    assert not thread.is_alive()
    if errors:
        raise errors[0]


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

    thread = threading.Thread(target=_watch)
    try:
        thread.start()
        await signal
    finally:
        _join(thread, errors)


def test_lifecycle_helper_does_not_wait_accepted_executor_work() -> None:
    import inspect

    source = inspect.getsource(run_ptb_lifecycle)
    assert "wait_accepted_executor_work" not in source


def test_submit_wins_seal_future_already_in_registry() -> None:
    release = threading.Event()
    errors: list[BaseException] = []

    def _work() -> str:
        try:
            assert release.wait(timeout=5)
            return "ok"
        except BaseException as exc:
            errors.append(exc)
            raise

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-win")
    try:
        outcome = admission.submit_if_open(pool, _work)
        assert isinstance(outcome, AdmissionAccepted)
        assert outcome.future in admission.accepted_executor_futures()
        admission.seal()
        assert outcome.future in admission.accepted_executor_futures()
        release.set()
        asyncio.run(admission.wait_accepted_executor_work())
        assert outcome.future.result() == "ok"
        assert admission.accepted_executor_futures() == ()
    finally:
        release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_seal_wins_submit_not_called() -> None:
    submitted = threading.Event()

    class _Exec:
        def submit(self, *args, **kwargs):  # noqa: ANN002, ANN003
            submitted.set()
            raise AssertionError("seal won; executor.submit must not run")

    admission = _open_bound()
    admission.seal()
    outcome = admission.submit_if_open(_Exec(), lambda: 1)
    assert isinstance(outcome, AdmissionRejected)
    assert submitted.is_set() is False
    assert admission.accepted_executor_futures() == ()
    asyncio.run(admission.wait_accepted_executor_work())


def test_concurrent_submit_seal_keeps_registry_invariant() -> None:
    release = threading.Event()
    start = threading.Barrier(3)
    errors: list[BaseException] = []
    submit_calls = []
    result: dict[str, object] = {}

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

    submit_thread = threading.Thread(target=_submit)
    seal_thread = threading.Thread(target=_seal)
    try:
        submit_thread.start()
        seal_thread.start()
        start.wait(timeout=5)
        _join(submit_thread, errors)
        _join(seal_thread, errors)
        outcome = result["outcome"]
        if isinstance(outcome, AdmissionAccepted):
            assert submit_calls == [1]
            assert outcome.future in admission.accepted_executor_futures()
        else:
            assert isinstance(outcome, AdmissionRejected)
            assert submit_calls == []
            assert admission.accepted_executor_futures() == ()
        release.set()
        asyncio.run(admission.wait_accepted_executor_work())
    finally:
        release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_already_done_future_callback_without_deadlock() -> None:
    seen: list[object] = []

    class _Inline:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            future: Future = Future()
            future.set_result(fn(*args, **kwargs))
            seen.append("submitted")
            return future

    def _fn() -> str:
        seen.append("ran")
        return "inline"

    admission = _open_bound()
    outcome = admission.submit_if_open(_Inline(), _fn)
    assert isinstance(outcome, AdmissionAccepted)
    assert seen == ["ran", "submitted"]
    assert outcome.future.result() == "inline"
    asyncio.run(asyncio.wait_for(admission.wait_accepted_executor_work(), timeout=2))
    assert admission.accepted_executor_futures() == ()


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


def test_cancel_handler_does_not_drop_accepted_work() -> None:
    release = threading.Event()
    errors: list[BaseException] = []

    def _work() -> str:
        try:
            assert release.wait(timeout=5)
            return "kept"
        except BaseException as exc:
            errors.append(exc)
            raise

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-h")
    try:
        outcome = admission.submit_if_open(pool, _work)
        assert isinstance(outcome, AdmissionAccepted)

        async def _run() -> None:
            admitted = watch_admitted_future(outcome.future, MagicMock(), work="direct")
            task = asyncio.create_task(admitted.wait())
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
        release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_cancel_and_repeat_wait_does_not_cancel_queued_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occupy_started = threading.Event()
    occupy_release = threading.Event()
    queued_started = threading.Event()
    queued_release = threading.Event()
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

    def _queued() -> str:
        try:
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
        after_submit = callback_count["n"]
        assert after_submit >= 2

        async def _run() -> None:
            admission._accepted_executor_wait_armed.clear()
            first = asyncio.create_task(admission.wait_accepted_executor_work())
            await _await_thread_event(admission._accepted_executor_wait_armed)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert queued.future.cancelled() is False
            assert queued.future in admission.accepted_executor_futures()

            admission._accepted_executor_wait_armed.clear()
            second = asyncio.create_task(admission.wait_accepted_executor_work())
            await _await_thread_event(admission._accepted_executor_wait_armed)
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            assert callback_count["n"] == after_submit
            assert queued.future.cancelled() is False

            occupy_release.set()
            queued_release.set()
            await admission.wait_accepted_executor_work()
            assert queued.future.result() == "once"
            assert callback_count["n"] == after_submit

        asyncio.run(_run())
    finally:
        occupy_release.set()
        queued_release.set()
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
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t40-ae")
    try:
        occupy = admission.submit_if_open(pool, _occupy)
        assert isinstance(occupy, AdmissionAccepted)
        assert occupy_started.wait(timeout=5)
        outcome = admission.submit_auto_enable_run_if_open(pool, _ae)
        assert isinstance(outcome, AdmissionAccepted)
        assert admission._ae_continuations
        assert outcome.future in admission.accepted_executor_futures()
        admission.seal()
        assert ae_started.is_set() is False
        occupy_release.set()
        assert ae_started.wait(timeout=5)
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
