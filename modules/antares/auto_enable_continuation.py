"""Thread-local Auto-Enable continuation for an Accepted isolated run."""

from __future__ import annotations

import functools
import threading
from dataclasses import dataclass, field
from typing import Any, Callable


class IsolatedAutoEnableEnqueueRejected(RuntimeError):
    """Bound isolated enqueue without a valid Accepted auto-enable continuation."""


@dataclass
class AutoEnableContinuationRecord:
    token: str
    admission: Any
    kind: str = "auto_enable_run"
    state: str = "pending"
    owner_thread: threading.Thread | None = None
    orchestrator_future: Any = None


_tls = threading.local()


def current_auto_enable_continuation() -> AutoEnableContinuationRecord | None:
    return getattr(_tls, "continuation", None)


def bind_thread_continuation(record: AutoEnableContinuationRecord) -> None:
    _tls.continuation = record


def clear_thread_continuation() -> None:
    _tls.continuation = None


def make_auto_enable_wrapper(
    admission: Any,
    token: str,
    fn: Callable[..., Any],
    args: tuple,
    kwargs: dict,
):
    """Submit this callable; it activates TLS on the job thread, not via contextvars."""

    @functools.wraps(fn)
    def wrapper():
        admission.activate_auto_enable_continuation(token)
        try:
            return fn(*args, **kwargs)
        finally:
            admission.revoke_auto_enable_continuation(token)

    return wrapper
