"""Sandbox child: real Antares assemble/snapshot/Application + production helper.

Not a copy of run_ptb_lifecycle. Invoked as ``python -m run_lifecycle``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import Application

from apps.antares import _boot_prefix, _snapshot_local_rules
from modules.antares.application_lifecycle import run_ptb_lifecycle

_OK = "antares lifecycle ok"
_EVENTS = os.environ.get("ANTARES_LC_EVENTS_LOG", "")
_REPORT = os.environ.get("ANTARES_LC_REPORT", "")


def _scenario() -> str:
    return (os.environ.get("ANTARES_LC_SCENARIO") or "whoami").strip()


def _event(kind: str, **fields: object) -> None:
    if not _EVENTS:
        raise RuntimeError("ANTARES_LC_EVENTS_LOG is not set")
    path = Path(_EVENTS)
    raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    if not isinstance(raw, list):
        raise RuntimeError("events log is not a list")
    raw.append({"kind": kind, **fields})
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    os.replace(tmp, path)


def _write_report(payload: dict) -> None:
    if not _REPORT:
        raise RuntimeError("ANTARES_LC_REPORT is not set")
    path = Path(_REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _read_report() -> dict:
    path = Path(_REPORT)
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    return raw if isinstance(raw, dict) else {}


def _read_events() -> list[dict]:
    raw = json.loads(Path(_EVENTS).read_text(encoding="utf-8") or "[]")
    return [item if isinstance(item, dict) else {"kind": str(item)} for item in raw]


async def _wait_event(kind: str, timeout: float = 30.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        for item in _read_events():
            if item.get("kind") == kind:
                return item
        await asyncio.sleep(0.05)
    raise TimeoutError(f"timed out waiting for event {kind!r}")


def _pick_whoami_user(snap) -> int:
    for (chat_key, user_id), level in snap.access_map.items():
        if chat_key == "private" and int(level) >= 1:
            return int(user_id)
    raise RuntimeError("sandbox workbook has no private access row with level>=1")


def _whoami_update(bot, user_id: int) -> Update:
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Sandbox"},
            "text": "/whoami",
            "entities": [{"offset": 0, "length": 7, "type": "bot_command"}],
        },
    }
    update = Update.de_json(payload, bot)
    if update is None:
        raise RuntimeError("Update.de_json returned None")
    return update


def _observe_app(app, *, recorded_before_loop_close: bool) -> dict:
    """Child-local snapshot. Does not call production inspect helpers."""

    bot = app.bot
    updater = app.updater
    pair = getattr(bot, "_request", None)
    httpx_closed: list[object] = []
    if isinstance(pair, tuple):
        for req in pair[:2]:
            client = getattr(req, "_client", None)
            httpx_closed.append(None if client is None else bool(client.is_closed))
    fetcher = getattr(app, "_Application__update_fetcher_task", None)
    created = getattr(app, "_Application__create_task_tasks", None) or set()
    create_unfinished = [
        t.get_name() for t in list(created) if not t.done()
    ]
    current = None
    try:
        current = asyncio.current_task()
    except RuntimeError:
        current = None
    ptb_unfinished: list[str] = []
    runner_unfinished: list[str] = []
    try:
        loop = asyncio.get_running_loop()
        tasks = asyncio.all_tasks(loop)
    except RuntimeError:
        tasks = set()
        loop = None
    for task in tasks:
        if task is current or task.done():
            continue
        name = task.get_name()
        if name.startswith("sandbox-"):
            runner_unfinished.append(name)
        elif name.startswith("Application:") or name == "ptb-lifecycle-cleanup":
            ptb_unfinished.append(name)
    return {
        "recorded_before_loop_close": recorded_before_loop_close,
        "loop_running": loop is not None and loop.is_running(),
        "app_initialized": bool(getattr(app, "_initialized", False)),
        "app_running": bool(getattr(app, "running", False)),
        "bot_requests_initialized": bool(getattr(bot, "_requests_initialized", False)),
        "bot_initialized": bool(getattr(bot, "_bot_initialized", False)),
        "updater_initialized": bool(getattr(updater, "_initialized", False)) if updater else False,
        "updater_running": bool(getattr(updater, "running", False)) if updater else False,
        "fetcher_done": None if fetcher is None else bool(fetcher.done()),
        "httpx_closed": httpx_closed,
        "create_task_unfinished": create_unfinished,
        "ptb_unfinished_tasks": ptb_unfinished,
        "runner_unfinished_tasks": runner_unfinished,
        "job_queue_set": getattr(app, "_job_queue", None) is not None,
        "processor": type(getattr(app, "update_processor", None)).__name__,
    }


def _outcome_payload(exc: BaseException | None, result) -> dict:
    return {
        "scenario": _scenario(),
        "exc_type": None if exc is None else type(exc).__name__,
        "exc": None if exc is None else str(exc),
        "cause_type": None
        if exc is None or exc.__cause__ is None
        else type(exc.__cause__).__name__,
        "cancelled_during_cleanup": bool(getattr(exc, "ptb_cancelled_during_cleanup", False))
        if exc is not None
        else False,
        "cleanup_actions": list(getattr(result, "cleanup_actions", ()))
        if result is not None
        else list(getattr(exc, "ptb_cleanup_actions", ()) if exc is not None else ()),
        "helper_leftover": list(getattr(result, "leftover", ()))
        if result is not None
        else list(getattr(exc, "ptb_leftover", ()) if exc is not None else ()),
        "cleanup_errors": list(getattr(result, "cleanup_errors", ()))
        if result is not None
        else list(getattr(exc, "ptb_cleanup_errors", ()) if exc is not None else ()),
    }


async def _run_helper(app, *, enable_polling: bool) -> object:
    stop = asyncio.Event()
    scenario = _scenario()

    async def _on_error(update, context):
        err = context.error
        _event(
            "error_handler",
            exc_name=type(err).__name__ if err is not None else None,
            message=str(err) if err is not None else None,
        )

    app.add_error_handler(_on_error)

    async def _feed_whoami():
        await _wait_event("application_start_ok")
        snap = app.bot_data.get("antares_snap")
        user_id = _pick_whoami_user(snap)
        _event("whoami_user", user_id=user_id)
        await app.update_queue.put(_whoami_update(app.bot, user_id))
        _event("update_queued")
        wait_kind = "error_handler" if scenario == "callback_error" else "whoami_completed"
        await _wait_event(wait_kind)
        if scenario != "callback_error":
            await _wait_event("send_message_synthetic")
        _event("intake_stopped")
        stop.set()

    async def _cancel_after_start():
        await _wait_event("application_start_ok")
        _event("cancelling_lifecycle")
        task.cancel()

    async def _cancel_during_cleanup():
        await _wait_event("application_stop_called")
        _event("cancelling_during_cleanup")
        task.cancel()

    async def _wait_pred(pred, *, timeout: float = 10.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if pred():
                return
            await asyncio.sleep(0)
        raise TimeoutError("timed out waiting for cancel state")

    def _named_cleanup() -> asyncio.Task | None:
        for item in asyncio.all_tasks():
            if item.get_name() == "ptb-lifecycle-cleanup":
                return item
        return None

    async def _double_cancel_cleanup():
        from cleanup_gate import gates

        await _wait_event("cleanup_hold_entered")
        _event("first_cancel")
        task.cancel()
        await _wait_pred(
            lambda: len(getattr(_named_cleanup(), "ptb_caller_cancels", ()) or ()) >= 1
        )
        _event("first_cancel_consumed", n=len(getattr(_named_cleanup(), "ptb_caller_cancels", ())))
        task.cancel()
        _event("second_cancel")
        await _wait_pred(
            lambda: len(getattr(_named_cleanup(), "ptb_caller_cancels", ()) or ()) >= 2
        )
        _event("second_cancel_delivered", n=len(getattr(_named_cleanup(), "ptb_caller_cancels", ())))
        _, release = gates()
        release.set()

    async def _cancel_during_initialize_cleanup():
        from cleanup_gate import gates

        await _wait_event("cleanup_hold_entered")
        _event("cancelling_initialize_cleanup")
        task.cancel()
        await _wait_pred(
            lambda: len(getattr(_named_cleanup(), "ptb_caller_cancels", ()) or ()) >= 1
        )
        _event("initialize_cleanup_cancel_delivered")
        _, release = gates()
        release.set()

    helpers: list[asyncio.Task] = []
    if scenario in {"whoami", "callback_error", "fail_cleanup_only", "cancel_during_cleanup", "double_cancel_cleanup"}:
        helpers.append(asyncio.create_task(_feed_whoami(), name="sandbox-feed-whoami"))
    if scenario == "cancel":
        helpers.append(asyncio.create_task(_cancel_after_start(), name="sandbox-cancel"))
    if scenario == "cancel_during_cleanup":
        helpers.append(asyncio.create_task(_cancel_during_cleanup(), name="sandbox-cancel-cleanup"))
    if scenario == "double_cancel_cleanup":
        helpers.append(asyncio.create_task(_double_cancel_cleanup(), name="sandbox-double-cancel"))
    if scenario == "cancel_during_initialize_cleanup":
        helpers.append(asyncio.create_task(_cancel_during_initialize_cleanup(), name="sandbox-cancel-init-cleanup"))

    async def _lifecycle():
        admission = None
        if os.environ.get("ANTARES_LC_ADMISSION", "").strip() == "1":
            from modules.antares.work_admission import WorkAdmission

            admission = WorkAdmission()
            _event("admission_created")
        return await run_ptb_lifecycle(
            app,
            stop=stop,
            enable_polling=enable_polling,
            admission=admission,
        )

    task = asyncio.create_task(_lifecycle(), name="sandbox-run_ptb_lifecycle")
    helper_exc: BaseException | None = None
    result = None
    try:
        result = await task
    except BaseException as exc:
        helper_exc = exc
    if helpers:
        for helper in helpers:
            if not helper.done():
                helper.cancel()
        await asyncio.gather(*helpers, return_exceptions=True)
    live = _observe_app(app, recorded_before_loop_close=True)
    payload = _outcome_payload(helper_exc, result)
    payload["in_loop"] = live
    _write_report(payload)
    _event(
        "in_loop_state_recorded",
        fetcher_done=live.get("fetcher_done"),
        app_running=live.get("app_running"),
        updater_running=live.get("updater_running"),
        httpx_closed=live.get("httpx_closed"),
        ptb_unfinished_tasks=live.get("ptb_unfinished_tasks"),
    )
    if helper_exc is not None:
        raise helper_exc
    return result


def _build_app(assembled, token: str):
    app = Application.builder().token(token).concurrent_updates(True).build()
    for handler in assembled.handlers:
        app.add_handler(handler)
    return app


def main() -> None:
    scenario = _scenario()
    assembled, rules, token = _boot_prefix()
    snap = _snapshot_local_rules(rules)
    app = _build_app(assembled, token)
    app.bot_data["antares_snap"] = snap
    if scenario == "unsupported_job_queue":
        app._job_queue = object()
    if scenario == "unsupported_processor":
        app._update_processor = object()
    _event(
        "child_ready",
        scenario=scenario,
        job_queue_set=getattr(app, "_job_queue", None) is not None,
        processor=type(getattr(app, "update_processor", None)).__name__,
    )

    enable_polling = scenario == "enable_polling"
    result = None
    exc: BaseException | None = None
    try:
        result = asyncio.run(_run_helper(app, enable_polling=enable_polling))
    except BaseException as raised:
        exc = raised

    report = _read_report()
    if not report:
        report = _outcome_payload(exc, result)
    report["post_loop"] = _observe_app(app, recorded_before_loop_close=False)
    if "in_loop" not in report:
        report["in_loop"] = None
    _write_report(report)
    _event(
        "child_finished",
        scenario=report.get("scenario"),
        exc_type=report.get("exc_type"),
        cleanup_actions=report.get("cleanup_actions"),
        helper_leftover=report.get("helper_leftover"),
        cleanup_errors=report.get("cleanup_errors"),
    )

    leftover = (report.get("in_loop") or {}).get("httpx_closed")
    in_loop = report.get("in_loop") or {}
    kinds = {item.get("kind") for item in _read_events()}

    if scenario == "whoami" and exc is None:
        closed = in_loop.get("httpx_closed") or []
        if closed == [True, True] and not in_loop.get("ptb_unfinished_tasks"):
            print(f"{_OK} scenario=whoami leftover=0")
            raise SystemExit(0)
        print("antares lifecycle incomplete cleanup; success diagnostic withheld", file=sys.stderr)
        raise SystemExit(4)

    expected_fail = {
        "enable_polling": "ValueError",
        "fail_requests": "RuntimeError",
        "fail_requests_and_cleanup": "RuntimeError",
        "fail_get_me": "InvalidToken",
        "fail_after_bot": "RuntimeError",
        "fail_start": "RuntimeError",
        "cancel": "CancelledError",
        "cancel_during_cleanup": "CancelledError",
        "double_cancel_cleanup": "CancelledError",
        "cancel_during_initialize_cleanup": "RuntimeError",
        "callback_error": None,
        "fail_cleanup_only": "RuntimeError",
        "unsupported_job_queue": "ValueError",
        "unsupported_processor": "ValueError",
    }
    if scenario not in expected_fail:
        print(f"unknown scenario {scenario!r}", file=sys.stderr)
        raise SystemExit(1)

    want = expected_fail[scenario]
    if scenario == "callback_error":
        if "whoami_completed" in kinds:
            print("callback_error must not record whoami_completed", file=sys.stderr)
            raise SystemExit(1)
        if "error_handler" not in kinds:
            print("callback_error missing error_handler", file=sys.stderr)
            raise SystemExit(1)
        closed = in_loop.get("httpx_closed") or []
        if exc is None and closed == [True, True]:
            print("antares lifecycle helper-finished scenario=callback_error error_handler=1 leftover=0")
            raise SystemExit(0)
        print("callback_error helper did not finish cleanly", file=sys.stderr)
        raise SystemExit(1)

    if exc is None:
        print(f"scenario {scenario} expected {want}, helper returned", file=sys.stderr)
        raise SystemExit(1)
    if type(exc).__name__ != want:
        print(f"scenario {scenario} expected {want}, got {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if scenario in {"enable_polling", "unsupported_job_queue", "unsupported_processor"}:
        if "application_initialize_called" in kinds:
            print(f"{scenario} must not initialize", file=sys.stderr)
            raise SystemExit(1)
    print(f"antares lifecycle expected-failure scenario={scenario} exc={want}")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
