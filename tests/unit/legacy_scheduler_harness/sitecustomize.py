"""Sitecustomize for `python scheduler.py` legacy wiring tests.

Loaded only when this directory is first on PYTHONPATH. Stubs prove
handler/scheduler/worker wiring; they do not prove business results.
"""

from __future__ import annotations

import atexit
import builtins
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

WIRING: dict = {
    "gate_passed": True,
    "handlers_added": 0,
    "handler_commands": [],
    "handler_types": [],
    "ensure_worker_started": False,
    "thread_targets": [],
    "schedule_loop_start_skipped": False,
    "run_polling": False,
    "rules_snapshot": False,
    "rules_force_sync": False,
    "blocked": [],
    "playwright_install_stubbed": False,
}

_WIRING_PATH = Path(os.environ["LEGACY_SCHEDULER_WIRING_PATH"])


def _dump() -> None:
    _WIRING_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WIRING_PATH.write_text(json.dumps(WIRING, ensure_ascii=False), encoding="utf-8")


atexit.register(_dump)

# --- subprocess: Chromium install is a no-op; everything else is blocked ---
_orig_run = subprocess.run
_orig_popen = subprocess.Popen


def _cmd_parts(cmd) -> list[str]:  # noqa: ANN001
    if isinstance(cmd, (list, tuple)):
        return [str(x) for x in cmd]
    return [str(cmd)]


def _blocked_run(cmd, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    parts = _cmd_parts(cmd)
    joined = " ".join(parts)
    WIRING["blocked"].append({"kind": "subprocess.run", "cmd": parts})
    if any("playwright" in p for p in parts) and any("install" in p for p in parts):
        WIRING["playwright_install_stubbed"] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")
    raise RuntimeError(f"subprocess.run blocked: {joined}")


class _BlockedPopen:
    def __init__(self, cmd, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        parts = _cmd_parts(cmd)
        WIRING["blocked"].append({"kind": "subprocess.Popen", "cmd": parts})
        raise RuntimeError(f"subprocess.Popen blocked: {parts}")


subprocess.run = _blocked_run  # type: ignore[assignment]
subprocess.Popen = _BlockedPopen  # type: ignore[misc, assignment]

# --- network / sockets ---
_orig_connect = socket.socket.connect


def _is_loopback(address) -> bool:  # noqa: ANN001
    host = address[0] if isinstance(address, tuple) else address
    return host in {"127.0.0.1", "::1", "localhost"}


def _blocked_connect(self, address):  # noqa: ANN001
    if _is_loopback(address):
        return _orig_connect(self, address)
    WIRING["blocked"].append({"kind": "socket.connect", "address": str(address)})
    raise OSError("socket.connect blocked in legacy scheduler test")


socket.socket.connect = _blocked_connect  # type: ignore[method-assign]

_orig_create_connection = socket.create_connection


def _blocked_create_connection(*args, **kwargs):  # noqa: ANN002, ANN003
    address = args[0] if args else kwargs.get("address")
    if address is not None and _is_loopback(address):
        return _orig_create_connection(*args, **kwargs)
    WIRING["blocked"].append({"kind": "create_connection", "args": str(args)})
    raise OSError("create_connection blocked in legacy scheduler test")


socket.create_connection = _blocked_create_connection  # type: ignore[assignment]

# --- threads: record wiring; do not run scheduler/worker loops ---
_orig_thread_start = threading.Thread.start


def _thread_start(self):  # noqa: ANN001
    tgt = getattr(self, "_target", None)
    name = getattr(tgt, "__name__", "") if tgt else ""
    display = name or (self.name or "")
    WIRING["thread_targets"].append(display)
    if name in {"schedule_loop", "worker_loop", "_loop_runner"} or str(self.name or "").startswith(
        "wallet-editor-worker"
    ):
        if name == "schedule_loop":
            WIRING["schedule_loop_start_skipped"] = True
        return None
    return _orig_thread_start(self)


threading.Thread.start = _thread_start  # type: ignore[method-assign]


def _patch_telegram(ext) -> None:  # noqa: ANN001
    if getattr(ext, "_legacy_scheduler_test_patched", False):
        return
    app_cls = ext.Application
    orig_add = app_cls.add_handler

    def add_handler(self, handler, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        WIRING["handlers_added"] += 1
        cmds = getattr(handler, "commands", None)
        if cmds:
            WIRING["handler_commands"].extend(sorted(str(c) for c in cmds))
        else:
            WIRING["handler_types"].append(type(handler).__name__)
        return orig_add(self, handler, *args, **kwargs)

    def run_polling(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        WIRING["run_polling"] = True
        _dump()

    app_cls.add_handler = add_handler
    app_cls.run_polling = run_polling
    ext._legacy_scheduler_test_patched = True


def _patch_worker(mod) -> None:  # noqa: ANN001
    orig = mod.ensure_worker_started

    def wrapped() -> None:
        WIRING["ensure_worker_started"] = True
        orig()

    mod.ensure_worker_started = wrapped


def _patch_access_rules(mod) -> None:  # noqa: ANN001
    def fake_get_snapshot(self, force_sync: bool = False):  # noqa: ANN001
        WIRING["rules_snapshot"] = True
        WIRING["rules_force_sync"] = bool(force_sync)
        return object()

    mod.AccessRules.get_snapshot = fake_get_snapshot


def _patch_telegram_bot(mod) -> None:  # noqa: ANN001
    def blocked_send(*args, **kwargs):  # noqa: ANN002, ANN003
        WIRING["blocked"].append({"kind": "telegram_send"})
        raise RuntimeError("telegram send blocked in legacy scheduler test")

    for name in ("send_message_sync", "send_message", "send_document_sync"):
        if hasattr(mod, name):
            setattr(mod, name, blocked_send)


def _patch_db(mod) -> None:  # noqa: ANN001
    def blocked(*args, **kwargs):  # noqa: ANN002, ANN003
        WIRING["blocked"].append({"kind": "db_connect", "module": mod.__name__})
        raise RuntimeError("database connect blocked in legacy scheduler test")

    if hasattr(mod, "connect"):
        mod.connect = blocked


def _patch_httpx(mod) -> None:  # noqa: ANN001
    if getattr(mod, "_legacy_scheduler_test_patched", False):
        return

    def blocked_send(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        WIRING["blocked"].append({"kind": "httpx", "url": str(getattr(request, "url", ""))})
        raise RuntimeError("httpx blocked in legacy scheduler test")

    client = getattr(mod, "Client", None)
    async_client = getattr(mod, "AsyncClient", None)
    if client is not None and hasattr(client, "send"):
        client.send = blocked_send
    if async_client is not None and hasattr(async_client, "send"):
        async def async_blocked(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            blocked_send(self, request, *args, **kwargs)

        async_client.send = async_blocked
    mod._legacy_scheduler_test_patched = True


def _patch_requests(mod) -> None:  # noqa: ANN001
    if getattr(mod, "_legacy_scheduler_test_patched", False):
        return

    def blocked(*args, **kwargs):  # noqa: ANN002, ANN003
        WIRING["blocked"].append({"kind": "requests"})
        raise RuntimeError("requests blocked in legacy scheduler test")

    for name in ("get", "post", "put", "patch", "delete", "request", "head"):
        if hasattr(mod, name):
            setattr(mod, name, blocked)
    session = getattr(mod, "Session", None)
    if session is not None and hasattr(session, "request"):
        session.request = blocked
    mod._legacy_scheduler_test_patched = True


_real_import = builtins.__import__
_seen: set[str] = set()


def _import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
    mod = _real_import(name, globals, locals, fromlist, level)
    _maybe_patch(name, sys.modules.get(name) or mod)
    if name == "telegram" or name.startswith("telegram."):
        ext = sys.modules.get("telegram.ext")
        if ext is not None:
            _maybe_patch("telegram.ext", ext)
    if name == "httpx" or name.startswith("httpx"):
        hx = sys.modules.get("httpx")
        if hx is not None:
            _maybe_patch("httpx", hx)
    return mod


def _maybe_patch(name: str, mod) -> None:  # noqa: ANN001
    if mod is None or name in _seen:
        return
    if name == "telegram.ext":
        _patch_telegram(mod)
        _seen.add(name)
    elif name == "automation.worker":
        _patch_worker(mod)
        _seen.add(name)
    elif name == "core.access_rules":
        _patch_access_rules(mod)
        _seen.add(name)
    elif name == "integrations.telegram_bot":
        _patch_telegram_bot(mod)
        _seen.add(name)
    elif name in {"psycopg2", "psycopg", "asyncpg"}:
        _patch_db(mod)
        _seen.add(name)
    elif name == "httpx":
        _patch_httpx(mod)
        _seen.add(name)
    elif name == "requests":
        _patch_requests(mod)
        _seen.add(name)


builtins.__import__ = _import
