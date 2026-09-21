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
from modules.antares.application_lifecycle import inspect_application_leftover, run_ptb_lifecycle

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


def _flags(app) -> dict:
    bot = app.bot
    updater = app.updater
    task = getattr(app, "_Application__update_fetcher_task", None)
    return {
        "app_initialized": bool(getattr(app, "_initialized", False)),
        "app_running": bool(getattr(app, "running", False)),
        "bot_requests_initialized": bool(getattr(bot, "_requests_initialized", False)),
        "bot_initialized": bool(getattr(bot, "_bot_initialized", False)),
        "updater_initialized": bool(getattr(updater, "_initialized", False)) if updater else False,
        "updater_running": bool(getattr(updater, "running", False)) if updater else False,
        "fetcher_done": None if task is None else bool(task.done()),
        "job_queue": app.job_queue is not None,
        "leftover": list(inspect_application_leftover(app)),
    }


def _outcome_payload(exc: BaseException | None, result) -> dict:
    payload = {
        "scenario": _scenario(),
        "exc_type": None if exc is None else type(exc).__name__,
        "exc": None if exc is None else str(exc),
        "cause_type": None
        if exc is None or exc.__cause__ is None
        else type(exc.__cause__).__name__,
        "cleanup_actions": list(getattr(result, "cleanup_actions", ()))
        if result is not None
        else list(getattr(exc, "ptb_cleanup_actions", ()) if exc is not None else ()),
        "helper_leftover": list(getattr(result, "leftover", ()))
        if result is not None
        else list(getattr(exc, "ptb_leftover", ()) if exc is not None else ()),
    }
    return payload


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

    task: asyncio.Task
    helpers = []
    if scenario in {"whoami", "callback_error", "fail_cleanup_only"}:
        helpers.append(asyncio.create_task(_feed_whoami(), name="sandbox-feed-whoami"))
    if scenario == "cancel":
        helpers.append(asyncio.create_task(_cancel_after_start(), name="sandbox-cancel"))

    async def _lifecycle():
        return await run_ptb_lifecycle(app, stop=stop, enable_polling=enable_polling)

    task = asyncio.create_task(_lifecycle(), name="sandbox-run_ptb_lifecycle")
    try:
        result = await task
        for helper in helpers:
            await asyncio.wait_for(helper, timeout=15)
        return result
    except Exception:
        for helper in helpers:
            helper.cancel()
        raise
    finally:
        for helper in helpers:
            if not helper.done():
                helper.cancel()


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
    _event("child_ready", scenario=scenario, job_queue=app.job_queue is not None)

    enable_polling = scenario == "enable_polling"
    result = None
    exc: BaseException | None = None
    try:
        result = asyncio.run(_run_helper(app, enable_polling=enable_polling))
    except BaseException as raised:
        exc = raised
    report = _outcome_payload(exc, result)
    report.update(_flags(app))
    _write_report(report)
    _event("child_finished", **{k: report[k] for k in ("scenario", "exc_type", "cleanup_actions", "helper_leftover")})

    leftover = report.get("leftover") or report.get("helper_leftover") or []
    if scenario == "whoami" and exc is None and not leftover:
        print(f"{_OK} scenario=whoami leftover=0")
        raise SystemExit(0)
    if scenario == "whoami":
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
        "callback_error": None,
        "fail_cleanup_only": "RuntimeError",
    }
    if scenario not in expected_fail:
        print(f"unknown scenario {scenario!r}", file=sys.stderr)
        raise SystemExit(1)

    want = expected_fail[scenario]
    if scenario == "callback_error":
        # Helper should still stop cleanly after error handler; that is not callback success.
        kinds = {item.get("kind") for item in _read_events()}
        if "whoami_completed" in kinds or "antares lifecycle ok" in (sys.stdout and ""):
            print("callback_error must not record whoami_completed", file=sys.stderr)
            raise SystemExit(1)
        if "error_handler" not in kinds:
            print("callback_error missing error_handler", file=sys.stderr)
            raise SystemExit(1)
        if exc is None and not leftover:
            print(f"{_OK} scenario=callback_error error_handler=1 leftover=0")
            raise SystemExit(0)
        print("callback_error helper did not finish cleanly", file=sys.stderr)
        raise SystemExit(1)

    if exc is None:
        print(f"scenario {scenario} expected {want}, helper returned", file=sys.stderr)
        raise SystemExit(1)
    if type(exc).__name__ != want:
        print(f"scenario {scenario} expected {want}, got {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if scenario == "enable_polling":
        kinds = {item.get("kind") for item in _read_events()}
        if "application_initialize_called" in kinds:
            print("enable_polling must not initialize", file=sys.stderr)
            raise SystemExit(1)
    print(f"antares lifecycle expected-failure scenario={scenario} exc={want}")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
