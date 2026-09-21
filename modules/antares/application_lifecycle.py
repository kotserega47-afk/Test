"""Manual PTB Application lifecycle for isolated Antares.

Caller owns the ``Application`` and the running event loop. This helper only
``await``s on that loop. It does not call ``sys.exit`` / ``os._exit`` and does
not close the loop.

Order: ``initialize`` → ``start`` → wait ``stop`` → ``stop`` → ``shutdown``.
``enable_polling=True`` is rejected before ``initialize``. Polling is not
implemented in this module.

PTB 22.8 internals used here (not monkeypatched globally):

- ``Application._initialized`` — ``Application.shutdown`` is a no-op until True.
  A no-op shutdown is **not** recorded as cleanup.
- ``Bot._requests_initialized`` — ``Bot.shutdown`` is a no-op until True.
- ``Bot._request`` — ``(getUpdates client, general API client)``. Public
  ``Bot.request`` is only the general client. After a failed
  ``HTTPXRequest.initialize`` (flag still false) both objects are closed via
  public ``BaseRequest.shutdown`` without calling ``Bot.initialize`` again.
- ``Updater._initialized`` — ``Updater.shutdown`` is a no-op until True and
  then calls ``Bot.shutdown``.
- ``Application._update_processor`` — default ``SimpleUpdateProcessor``
  initialize/shutdown are no-ops; those calls are not recorded as cleanup.

JobQueue extra is not handled: if ``app.job_queue`` is set this helper still
relies on ``Application.start``/``stop``. That extra path is **not** claimed
tested in this tree (APS is not installed on the Cursor 3.12.10 interpreter).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

_POLLING_NOT_IMPLEMENTED = (
    "run_ptb_lifecycle(enable_polling=True) is not implemented; "
    "rejecting before initialize"
)


@dataclass(frozen=True)
class PtbLifecycleResult:
    """Outcome of a helper run that returned (body completed)."""

    cleanup_actions: tuple[str, ...]
    leftover: tuple[str, ...]


def _flag(obj: Any, name: str) -> bool:
    return bool(getattr(obj, name, False))


def _iter_bot_requests(bot: Any) -> list[tuple[str, Any]]:
    pair = getattr(bot, "_request", None)
    if isinstance(pair, tuple) and len(pair) >= 2:
        return [("get_updates_request", pair[0]), ("request", pair[1])]
    req = getattr(bot, "request", None)
    if req is not None:
        return [("request", req)]
    return []


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
        shutdown = getattr(req, "shutdown", None)
        if shutdown is None:
            leftover.append(f"{label}.no_shutdown_api")
        client = getattr(req, "_client", None)
        if client is None:
            leftover.append(f"{label}.no_client")
        elif not getattr(client, "is_closed", True):
            leftover.append(f"{label}.httpx_open")
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
    job_queue = getattr(app, "job_queue", None)
    if job_queue is not None and getattr(job_queue, "scheduler", None) is not None:
        scheduler = job_queue.scheduler
        if getattr(scheduler, "running", False):
            leftover.append("job_queue.scheduler.running")
    return tuple(leftover)


def _attach_outcome(exc: BaseException, actions: list[str], leftover: tuple[str, ...]) -> None:
    exc.ptb_cleanup_actions = tuple(actions)
    exc.ptb_leftover = leftover


async def _call_named(actions: list[str], errors: list[Exception], name: str, awaitable) -> None:
    try:
        await awaitable
    except Exception as exc:
        errors.append(exc)
        actions.append(f"{name}.failed:{type(exc).__name__}")
    else:
        actions.append(name)


async def _shutdown_bot_http_clients(bot: Any, actions: list[str], errors: list[Exception]) -> None:
    if bot is None:
        actions.append("bot.unavailable")
        return
    if _flag(bot, "_requests_initialized"):
        await _call_named(actions, errors, "bot.shutdown", bot.shutdown())
        return
    items = _iter_bot_requests(bot)
    if not items:
        actions.append("bot_requests.unavailable")
        return
    for label, req in items:
        shutdown = getattr(req, "shutdown", None)
        if shutdown is None:
            actions.append(f"{label}.no_shutdown_api")
            continue
        await _call_named(actions, errors, f"{label}.shutdown", shutdown())


async def _cleanup_application(app: Any) -> tuple[list[str], tuple[str, ...], list[Exception]]:
    actions: list[str] = []
    errors: list[Exception] = []
    updater = getattr(app, "updater", None)

    if updater is not None and getattr(updater, "running", False):
        await _call_named(actions, errors, "updater.stop", updater.stop())
    if getattr(app, "running", False):
        await _call_named(actions, errors, "app.stop", app.stop())

    if _flag(app, "_initialized"):
        await _call_named(actions, errors, "app.shutdown", app.shutdown())
        return actions, inspect_application_leftover(app), errors

    if updater is not None and _flag(updater, "_initialized"):
        await _call_named(actions, errors, "updater.shutdown", updater.shutdown())
    else:
        await _shutdown_bot_http_clients(getattr(app, "bot", None), actions, errors)

    return actions, inspect_application_leftover(app), errors


def _raise_with_cleanup(primary: BaseException | None, errors: list[Exception], actions: list[str], leftover: tuple[str, ...]) -> None:
    if primary is not None:
        _attach_outcome(primary, actions, leftover)
        if errors:
            group = ExceptionGroup("ptb lifecycle cleanup errors", errors)
            raise primary from group
        raise primary
    if errors:
        exc = errors[0] if len(errors) == 1 else ExceptionGroup("ptb lifecycle cleanup errors", errors)
        _attach_outcome(exc, actions, leftover)
        raise exc


async def run_ptb_lifecycle(
    app,
    *,
    stop: asyncio.Event,
    enable_polling: bool = False,
) -> PtbLifecycleResult:
    """Run initialize/start, wait for ``stop``, then stop/shutdown.

    ``enable_polling=True`` raises ``ValueError`` before ``initialize``.
    """

    if enable_polling:
        raise ValueError(_POLLING_NOT_IMPLEMENTED)

    primary: BaseException | None = None
    try:
        await app.initialize()
        await app.start()
        await stop.wait()
    except asyncio.CancelledError as exc:
        primary = exc
    except Exception as exc:
        primary = exc

    actions, leftover, errors = await _cleanup_application(app)
    if leftover and primary is None:
        primary = RuntimeError("ptb lifecycle leftover resources: " + ",".join(leftover))
    if primary is None and not errors:
        return PtbLifecycleResult(cleanup_actions=tuple(actions), leftover=leftover)
    _raise_with_cleanup(primary, errors, actions, leftover)
    raise AssertionError("unreachable")
