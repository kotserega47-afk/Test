"""Sitecustomize for Platform payin compare subprocess.

Loaded when this directory is first on PYTHONPATH, before
analyzers.raccoon_hourly_report. Stubs only telegram_bot and
rules_provider (and dotenv if touched). Does not replace
aggregate_payin_by_method / format_report / normalize_partner_name.
"""

from __future__ import annotations

import atexit
import builtins
import errno
import importlib.abc
import importlib.machinery
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import types
from pathlib import Path

_SANDBOX = Path(os.environ["LEGACY_SCHEDULER_SANDBOX"]).resolve()
_EVENTS_PATH = Path(os.environ["PLATFORM_COMPARE_EVENTS_PATH"])

EVENTS: dict = {
    "blocked": [],
    "thread_rejected": [],
    "send_message_sync_calls": 0,
    "get_rules_snapshot_calls": 0,
    "dotenv_load_calls": 0,
    "stub_modules": [],
}

_orig_makedirs = os.makedirs
_orig_path_exists = os.path.exists
_orig_path_isfile = os.path.isfile
_orig_open = builtins.open
_orig_path_mkdir = pathlib.Path.mkdir
_orig_path_open = pathlib.Path.open


def _dump() -> None:
    _EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _EVENTS_PATH.write_text(json.dumps(EVENTS, ensure_ascii=False), encoding="utf-8")


atexit.register(_dump)


def _refuse_send(*args, **kwargs):  # noqa: ANN002, ANN003
    EVENTS["send_message_sync_calls"] += 1
    EVENTS["blocked"].append({"kind": "send_message_sync"})
    raise RuntimeError("send_message_sync blocked in platform compare")


def _refuse_rules(*args, **kwargs):  # noqa: ANN002, ANN003
    EVENTS["get_rules_snapshot_calls"] += 1
    EVENTS["blocked"].append({"kind": "get_rules_snapshot"})
    raise RuntimeError("get_rules_snapshot blocked in platform compare")


def _refuse_dotenv(*args, **kwargs):  # noqa: ANN002, ANN003
    EVENTS["dotenv_load_calls"] += 1
    EVENTS["blocked"].append({"kind": "load_dotenv"})
    return False


_STUBS: dict[str, dict] = {
    "integrations.telegram_bot": {
        "send_message_sync": _refuse_send,
        "send_message": _refuse_send,
        "send_document_sync": _refuse_send,
        "send_file_sync": _refuse_send,
    },
    "core.rules_provider": {
        "get_rules_snapshot": _refuse_rules,
    },
    "dotenv": {
        "load_dotenv": _refuse_dotenv,
        "find_dotenv": lambda *a, **k: "",
    },
}


class _StubLoader(importlib.abc.Loader):
    def __init__(self, name: str) -> None:
        self.name = name

    def create_module(self, spec):  # noqa: ANN001
        mod = types.ModuleType(self.name)
        mod.__file__ = f"<platform-compare-stub:{self.name}>"
        for key, value in _STUBS.get(self.name, {}).items():
            setattr(mod, key, value)
        EVENTS["stub_modules"].append(self.name)
        return mod

    def exec_module(self, module) -> None:  # noqa: ANN001
        return None


class _StubFinder:
    def find_spec(self, fullname, path, target=None):  # noqa: ANN001
        if fullname not in _STUBS:
            return None
        return importlib.machinery.ModuleSpec(fullname, _StubLoader(fullname), is_package=False)


sys.meta_path.insert(0, _StubFinder())


def _redirect_fs_path(path) -> str:  # noqa: ANN001
    raw = os.fspath(path)
    try:
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.resolve().is_relative_to(_SANDBOX):
            return raw
    except (OSError, RuntimeError, ValueError):
        pass
    if "logs" in Path(raw).parts:
        parts = list(Path(raw).parts)
        idx = parts.index("logs")
        return str(_SANDBOX.joinpath(*parts[idx:]))
    norm = raw.replace("\\", "/")
    if len(norm) >= 2 and norm[1] == ":":
        norm = norm[2:]
    if not norm.startswith("/"):
        first = norm.split("/", 1)[0]
        if first in {"downloads", "state"}:
            return str(_SANDBOX / raw)
        return raw
    for prefix in ("/tmp", "/app", "/data"):
        if norm == prefix or norm.startswith(prefix + "/"):
            return str(_SANDBOX / norm.lstrip("/"))
    return raw


def _makedirs(name, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    return _orig_makedirs(_redirect_fs_path(name), *args, **kwargs)


def _exists(path):  # noqa: ANN001
    return _orig_path_exists(_redirect_fs_path(path))


def _isfile(path):  # noqa: ANN001
    return _orig_path_isfile(_redirect_fs_path(path))


def _open(file, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    if isinstance(file, (str, os.PathLike)):
        file = _redirect_fs_path(file)
    return _orig_open(file, *args, **kwargs)


def _path_mkdir(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    return _orig_path_mkdir(pathlib.Path(_redirect_fs_path(self)), *args, **kwargs)


def _path_open(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    return _orig_path_open(pathlib.Path(_redirect_fs_path(self)), *args, **kwargs)


os.makedirs = _makedirs  # type: ignore[assignment]
os.path.exists = _exists  # type: ignore[assignment]
os.path.isfile = _isfile  # type: ignore[assignment]
builtins.open = _open  # type: ignore[assignment]
pathlib.Path.mkdir = _path_mkdir  # type: ignore[assignment]
pathlib.Path.open = _path_open  # type: ignore[assignment]


def _blocked_run(cmd, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    EVENTS["blocked"].append({"kind": "subprocess.run", "cmd": str(cmd)})
    raise RuntimeError(f"subprocess.run blocked in platform compare: {cmd}")


class _BlockedPopen:
    def __init__(self, cmd, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        EVENTS["blocked"].append({"kind": "subprocess.Popen", "cmd": str(cmd)})
        raise RuntimeError(f"subprocess.Popen blocked in platform compare: {cmd}")


subprocess.run = _blocked_run  # type: ignore[assignment]
subprocess.Popen = _BlockedPopen  # type: ignore[misc, assignment]

_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_create_connection = socket.create_connection
_orig_socketpair = socket.socketpair
_socketpair_depth = 0


def _blocked_connect(self, address):  # noqa: ANN001
    if _socketpair_depth:
        return _orig_connect(self, address)
    EVENTS["blocked"].append({"kind": "socket.connect", "address": str(address)})
    raise OSError("socket.connect blocked in platform compare")


def _blocked_connect_ex(self, address):  # noqa: ANN001
    if _socketpair_depth:
        return _orig_connect_ex(self, address)
    EVENTS["blocked"].append({"kind": "socket.connect_ex", "address": str(address)})
    return errno.ECONNREFUSED


def _blocked_create_connection(*args, **kwargs):  # noqa: ANN002, ANN003
    if _socketpair_depth:
        return _orig_create_connection(*args, **kwargs)
    EVENTS["blocked"].append({"kind": "create_connection", "args": str(args)})
    raise OSError("create_connection blocked in platform compare")


def _socketpair(*args, **kwargs):  # noqa: ANN002, ANN003
    global _socketpair_depth
    _socketpair_depth += 1
    try:
        return _orig_socketpair(*args, **kwargs)
    finally:
        _socketpair_depth -= 1


socket.socket.connect = _blocked_connect  # type: ignore[method-assign]
socket.socket.connect_ex = _blocked_connect_ex  # type: ignore[method-assign]
socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
socket.socketpair = _socketpair  # type: ignore[assignment]


def _thread_start(self):  # noqa: ANN001
    tgt = getattr(self, "_target", None)
    name = getattr(tgt, "__name__", "") if tgt else ""
    display = name or str(self.name or "")
    EVENTS["thread_rejected"].append(display)
    _dump()
    raise RuntimeError(f"Thread.start blocked in platform compare: {display!r}")


threading.Thread.start = _thread_start  # type: ignore[method-assign]
