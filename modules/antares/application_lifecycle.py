"""Manual PTB Application lifecycle for isolated Antares.

Caller owns the ``Application`` and the running event loop. This helper only
``await``s on that loop. It does not call ``sys.exit`` / ``os._exit`` and does
not close the loop.

Order: ``initialize`` → ``start`` → wait ``stop`` → ``stop`` → ``shutdown``.
``enable_polling=True`` is rejected before ``initialize``. Polling is not
implemented in this module.

Supported Application (checked before ``initialize``):

- ``updater`` is present
- ``persistence`` is None
- no JobQueue extra (``Application._job_queue`` is None)
- ``update_processor`` is ``SimpleUpdateProcessor`` (the default when
  ``concurrent_updates`` is ``True`` / an int)

Other processors, persistence, missing updater, or JobQueue extra are
rejected with ``ValueError``. This is **not** a universal PTB shutdown
for arbitrary Application graphs. JobQueue extra is **not** claimed tested.

PTB 22.8 internals used here (not monkeypatched globally):

- ``Application._initialized`` — ``Application.shutdown`` is a no-op until True.
  A no-op shutdown is **not** recorded as cleanup.
- ``Application._job_queue`` — public ``job_queue`` warns when unset; the helper
  reads the private attribute to reject extra without that warning.
- ``Bot._requests_initialized`` — ``Bot.shutdown`` is a no-op until True.
- ``Bot._request`` — ``(getUpdates client, general API client)``. Public
  ``Bot.request`` is only the general client. After a failed
  ``HTTPXRequest.initialize`` (flag still false) both objects are closed via
  public ``BaseRequest.shutdown`` without calling ``Bot.initialize`` again.
  If one ``request.shutdown`` fails, the other still runs when its client is open.
- ``Updater._initialized`` — ``Updater.shutdown`` is a no-op until True and
  then calls ``Bot.shutdown``.
- Default ``SimpleUpdateProcessor`` initialize/shutdown are no-ops; those calls
  are not recorded as cleanup.

If ``Application.shutdown`` fails, the helper does **not** call it again.
It inspects remaining running flags and open HTTPX clients and closes only
those remaining pieces.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from telegram.ext._baseupdateprocessor import SimpleUpdateProcessor

_POLLING_NOT_IMPLEMENTED = (
    "run_ptb_lifecycle(enable_polling=True) is not implemented; "
    "rejecting before initialize"
)


@dataclass(frozen=True)
class PtbLifecycleResult:
    """Outcome of a helper run that returned (body completed)."""

    cleanup_actions: tuple[str, ...]
    leftover: tuple[str, ...]
    cleanup_errors: tuple[str, ...] = ()


def _flag(obj: Any, name: str) -> bool:
    return bool(getattr(obj, name, False))


def unsupported_application_reasons(app: Any) -> tuple[str, ...]:
    reasons: list[str] = []
    if getattr(app, "_job_queue", None) is not None:
        reasons.append("job_queue")
    if getattr(app, "persistence", None) is not None:
        reasons.append("persistence")
    if getattr(app, "updater", None) is None:
        reasons.append("updater.missing")
    processor = getattr(app, "update_processor", None)
    if not isinstance(processor, SimpleUpdateProcessor):
        reasons.append(f"processor={type(processor).__name__}")
    return tuple(reasons)


def _iter_bot_requests(bot: Any) -> list[tuple[str, Any]]:
    pair = getattr(bot, "_request", None)
    if isinstance(pair, tuple) and len(pair) >= 2:
        return [("get_updates_request", pair[0]), ("request", pair[1])]
    req = getattr(bot, "request", None)
    if req is not None:
        return [("request", req)]
    return []


def _httpx_open(req: Any) -> bool:
    client = getattr(req, "_client", None)
    return client is not None and not getattr(client, "is_closed", True)


def inspect_bot_httpx_leftover(bot: Any) -> tuple[str, ...]:
    leftover: list[str] = []
    items = _iter_bot_requests(bot)
    if not items:
        leftover.append("bot_requests.unavailable")
        return tuple(leftover)
    if len(items) == 1:
        leftover.append("get_updates_request.not_in_public_api")
    for label, req in items:
        if req is None:
            leftover.append(f"{label}.missing")
            continue
        if getattr(req, "shutdown", None) is None:
            leftover.append(f"{label}.no_shutdown_api")
        if _httpx_open(req):
            leftover.append(f"{label}.httpx_open")
        elif getattr(req, "_client", None) is None:
            leftover.append(f"{label}.no_client")
    return tuple(leftover)


def inspect_application_leftover(app: Any) -> tuple[str, ...]:
    leftover = list(inspect_bot_httpx_leftover(getattr(app, "bot", None)))
    updater = getattr(app, "updater", None)
    if updater is not None and getattr(updater, "running", False):
        leftover.append("updater.running")
    if getattr(app, "running", False):
        leftover.append("application.running")
    task = getattr(app, "_Application__update_fetcher_task", None)
    if task is not None and not task.done():
        leftover.append("update_fetcher_task")
    job_queue = getattr(app, "_job_queue", None)
    if job_queue is not None and getattr(job_queue, "scheduler", None) is not None:
        scheduler = job_queue.scheduler
        if getattr(scheduler, "running", False):
            leftover.append("job_queue.scheduler.running")
    return tuple(leftover)


def _error_label(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _attach_outcome(
    exc: BaseException,
    actions: list[str],
    leftover: tuple[str, ...],
    errors: list[BaseException],
) -> None:
    exc.ptb_cleanup_actions = tuple(actions)
    exc.ptb_leftover = leftover
    exc.ptb_cleanup_errors = tuple(_error_label(item) for item in errors)


async def _call_named(actions: list[str], errors: list[BaseException], name: str, awaitable) -> None:
    try:
        await awaitable
    except Exception as exc:
        errors.append(exc)
        actions.append(f"{name}.failed:{type(exc).__name__}")
    else:
        actions.append(name)


async def _close_open_http_clients(bot: Any, actions: list[str], errors: list[BaseException]) -> None:
    if bot is None:
        return
    items = _iter_bot_requests(bot)
    if not items:
        return
    for label, req in items:
        if req is None or not _httpx_open(req):
            continue
        shutdown = getattr(req, "shutdown", None)
        if shutdown is None:
            actions.append(f"{label}.no_shutdown_api")
            continue
        await _call_named(actions, errors, f"{label}.shutdown", shutdown())


async def _shutdown_bot_http_clients(bot: Any, actions: list[str], errors: list[BaseException]) -> None:
    if bot is None:
        actions.append("bot.unavailable")
        return
    if _flag(bot, "_requests_initialized"):
        await _call_named(actions, errors, "bot.shutdown", bot.shutdown())
        await _close_open_http_clients(bot, actions, errors)
        return
    await _close_open_http_clients(bot, actions, errors)
    if not _iter_bot_requests(bot):
        actions.append("bot_requests.unavailable")


async def _cleanup_application(app: Any) -> tuple[list[str], tuple[str, ...], list[BaseException]]:
    actions: list[str] = []
    errors: list[BaseException] = []
    updater = getattr(app, "updater", None)

    if updater is not None and getattr(updater, "running", False):
        await _call_named(actions, errors, "updater.stop", updater.stop())
    if getattr(app, "running", False):
        await _call_named(actions, errors, "app.stop", app.stop())

    if _flag(app, "_initialized"):
        await _call_named(actions, errors, "app.shutdown", app.shutdown())
    elif updater is not None and _flag(updater, "_initialized"):
        await _call_named(actions, errors, "updater.shutdown", updater.shutdown())
    else:
        await _shutdown_bot_http_clients(getattr(app, "bot", None), actions, errors)

    if updater is not None and getattr(updater, "running", False):
        await _call_named(actions, errors, "updater.stop", updater.stop())
    if getattr(app, "running", False):
        await _call_named(actions, errors, "app.stop", app.stop())
    await _close_open_http_clients(getattr(app, "bot", None), actions, errors)

    return actions, inspect_application_leftover(app), errors


def _raise_with_cleanup(
    primary: BaseException | None,
    errors: list[BaseException],
    actions: list[str],
    leftover: tuple[str, ...],
) -> None:
    exc_errors = [item for item in errors if isinstance(item, Exception)]
    group = ExceptionGroup("ptb lifecycle cleanup errors", exc_errors) if exc_errors else None
    if primary is not None:
        _attach_outcome(primary, actions, leftover, errors)
        if group is not None:
            raise primary from group
        raise primary
    if errors:
        exc = group if group is not None and len(exc_errors) > 1 else (exc_errors[0] if exc_errors else errors[0])
        _attach_outcome(exc, actions, leftover, errors)
        raise exc


async def _await_cleanup(app: Any) -> tuple[list[str], tuple[str, ...], list[BaseException], BaseException | None]:
    cleanup_task = asyncio.create_task(_cleanup_application(app), name="ptb-lifecycle-cleanup")
    cancelled: BaseException | None = None
    try:
        actions, leftover, errors = await asyncio.shield(cleanup_task)
    except asyncio.CancelledError as cancel:
        cancelled = cancel
        if not cleanup_task.done():
            actions, leftover, errors = await cleanup_task
        else:
            actions, leftover, errors = cleanup_task.result()
    return actions, leftover, errors, cancelled


async def run_ptb_lifecycle(
    app,
    *,
    stop: asyncio.Event,
    enable_polling: bool = False,
) -> PtbLifecycleResult:
    """Run initialize/start, wait for ``stop``, then stop/shutdown.

    ``enable_polling=True`` and unsupported Application graphs raise
    ``ValueError`` before ``initialize``.
    """

    if enable_polling:
        raise ValueError(_POLLING_NOT_IMPLEMENTED)

    unsupported = unsupported_application_reasons(app)
    if unsupported:
        raise ValueError(
            "run_ptb_lifecycle supports Application with SimpleUpdateProcessor, "
            "present Updater, no persistence, and no JobQueue extra; "
            f"rejecting before initialize: {', '.join(unsupported)}"
        )

    primary: BaseException | None = None
    try:
        await app.initialize()
        await app.start()
        await stop.wait()
    except asyncio.CancelledError as exc:
        primary = exc
    except Exception as exc:
        primary = exc

    actions, leftover, errors, cancelled = await _await_cleanup(app)
    if cancelled is not None:
        if primary is None:
            primary = cancelled
        primary.ptb_cancelled_during_cleanup = True

    if leftover and primary is None:
        primary = RuntimeError("ptb lifecycle leftover resources: " + ",".join(leftover))
    if primary is None and not errors:
        return PtbLifecycleResult(
            cleanup_actions=tuple(actions),
            leftover=leftover,
            cleanup_errors=(),
        )
    _raise_with_cleanup(primary, errors, actions, leftover)
    raise AssertionError("unreachable")
