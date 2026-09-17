"""Sitecustomize for `python scheduler.py` legacy wiring tests.

Loaded only when this directory is first on PYTHONPATH.

Boundary: this process executes real scheduler.py through main() and
get_handlers() from integrations.tg_commands. Heavy internals (downloaders,
rules_provider, worker engine, WE/DB, telegram_bot) are explicit import
stubs. Observed add_handler / ensure_worker_started / schedule_loop /
run_polling prove wiring, not business results. gate_passed is not recorded.
"""

from __future__ import annotations

import atexit
import builtins
import errno
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
from pathlib import Path

WIRING: dict = {
    "handlers_added": 0,
    "handler_commands": [],
    "handler_types": [],
    "ensure_worker_started": False,
    "thread_targets": [],
    "rejected_threads": [],
    "schedule_loop_start_skipped": False,
    "run_polling": False,
    "rules_snapshot": False,
    "rules_force_sync": False,
    "blocked": [],
    "playwright_install_stubbed": False,
    "dotenv_load_attempted": False,
}

_WIRING_PATH = Path(os.environ["LEGACY_SCHEDULER_WIRING_PATH"])
_SANDBOX = Path(os.environ["LEGACY_SCHEDULER_SANDBOX"]).resolve()

_ALLOWED_THREAD_TARGETS = frozenset({"schedule_loop", "worker_loop", "_loop_runner"})

_orig_makedirs = os.makedirs
_orig_path_exists = os.path.exists
_orig_path_isfile = os.path.isfile
_orig_open = builtins.open
_orig_path_mkdir = pathlib.Path.mkdir
_orig_path_open = pathlib.Path.open


def _dump() -> None:
    _WIRING_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WIRING_PATH.write_text(json.dumps(WIRING, ensure_ascii=False), encoding="utf-8")


atexit.register(_dump)

import importlib.abc
import importlib.machinery
import types


def _blocked_internal(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("internal dependency stubbed in legacy scheduler test")


def _empty(*args, **kwargs):  # noqa: ANN002, ANN003
    return None


_INTERNAL_STUB_ATTRS: dict[str, dict] = {
    "core.schedules": {
        "load_schedules": lambda: [],
        "Schedule": type("Schedule", (), {}),
    },
    "core.rules_provider": {
        "get_snapshot_v2": _blocked_internal,
        "get_rules_snapshot": _blocked_internal,
        "invalidate_rules_v2_cache": _empty,
        "_rules_dropbox_path": lambda: "/rules.xlsx",
    },
    "core.config_manager": {
        "get_job_params": lambda *a, **k: {},
        "get_job_param": lambda *a, **k: None,
        "get_job_params_overrides": lambda *a, **k: {},
    },
    "core.state_store": {
        "state_get": lambda *a, **k: None,
        "state_update": lambda *a, **k: None,
    },
    "core.job_runner": {
        "Actor": type("Actor", (), {"__init__": lambda self, **k: None}),
        "JOB_REGISTRY": {},
        "get_status": lambda *a, **k: {},
        "request_job": _blocked_internal,
    },
    "automation.worker": {
        "ensure_worker_started": _empty,
    },
    "core.access_rules": {
        "AccessRules": type(
            "AccessRules",
            (),
            {
                "__init__": lambda self, rules_env_path=None: None,
                "get_snapshot": lambda self, force_sync=False: object(),
                "invalidate": lambda self: None,
            },
        ),
    },
    "integrations.telegram_bot": {
        "log_telegram_health_if_due": _empty,
        "send_message_sync": _blocked_internal,
        "send_message": _blocked_internal,
        "send_document_sync": _blocked_internal,
        "get_telegram_sender_health_snapshot": lambda: {},
    },
    "integrations.wallet_editor_auto_enable": {
        "run_auto_enable": _blocked_internal,
        "run_auto_enable_plan": _blocked_internal,
    },
    "integrations.downloader_wallets": {"run_wallet_cycle": _blocked_internal},
    "integrations.bakai_monitor_playwright": {"run_rate_monitor_safe": _blocked_internal},
    "integrations.downloader": {"run_download": _blocked_internal},
    "integrations.raccoon_jobs": {},
    "integrations.script_jobs": {},
    "analyzers.hourly_report": {"run_hourly_report": _blocked_internal},
    "integrations.telegram_routes": {
        "ROUTE_PLATFORM_HOURLY_REPORT": "hourly",
        "send_message_to_route": _blocked_internal,
        "routes_from_rules_v2_enabled": lambda: False,
    },
    "integrations.wallet_editor_tg": {
        "handle_wallet_editor_document": _blocked_internal,
    },
    "integrations.wallet_editor_registry_refresh": {
        "run_wallet_editor_registry_refresh_job": _blocked_internal,
    },
    "integrations.wallet_editor_registry": {
        "build_registry_health_report": lambda *a, **k: {},
        "format_registry_health_report": lambda *a, **k: "",
        "replay_pending_outbox_records": _blocked_internal,
        "run_registry_outbox_replay_job": _blocked_internal,
    },
    "integrations.wallet_editor_registry_db": {},
    "integrations.wallet_editor_registry_db.registry_export_builder": {
        "RegistryExportArtifact": type("RegistryExportArtifact", (), {}),
        "RegistryExportBuilder": type("RegistryExportBuilder", (), {}),
        "build_registry_export_from_postgres": _blocked_internal,
        "format_registry_export_summary": lambda *a, **k: "",
    },
    "core.rules_v2.ops_rules_validate_summary": {
        "build_rules_validate_telegram_chunks_with_payload": lambda *a, **k: ([], None),
    },
    "core.rules_v2.rules_validate_audit": {
        "try_append_manual_validate_audit_from_payload": _empty,
    },
}

_STUB_PACKAGES = {"integrations.wallet_editor_registry_db", "core.rules_v2"}


class _StubLoader(importlib.abc.Loader):
    def __init__(self, name: str) -> None:
        self.name = name

    def create_module(self, spec):  # noqa: ANN001
        mod = types.ModuleType(self.name)
        mod.__file__ = f"<legacy-scheduler-stub:{self.name}>"
        for key, value in _INTERNAL_STUB_ATTRS.get(self.name, {}).items():
            setattr(mod, key, value)
        return mod

    def exec_module(self, module) -> None:  # noqa: ANN001
        return None


class _StubFinder:
    def find_spec(self, fullname, path, target=None):  # noqa: ANN001
        if fullname not in _INTERNAL_STUB_ATTRS:
            return None
        return importlib.machinery.ModuleSpec(
            fullname,
            _StubLoader(fullname),
            is_package=fullname in _STUB_PACKAGES,
        )


sys.meta_path.insert(0, _StubFinder())


def _redirect_fs_path(path) -> str:  # noqa: ANN001
    raw = os.fspath(path)
    try:
        resolved_sandbox = _SANDBOX
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.resolve().is_relative_to(resolved_sandbox):
            return raw
    except (OSError, RuntimeError, ValueError):
        pass
    norm = raw.replace("\\", "/")
    if len(norm) >= 2 and norm[1] == ":":
        norm = norm[2:]
    if not norm.startswith("/"):
        first = norm.split("/", 1)[0]
        if first in {"logs", "downloads", "state"}:
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
    target = pathlib.Path(_redirect_fs_path(self))
    return _orig_path_mkdir(target, *args, **kwargs)


def _path_open(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    target = pathlib.Path(_redirect_fs_path(self))
    return _orig_path_open(target, *args, **kwargs)


os.makedirs = _makedirs  # type: ignore[assignment]
os.path.exists = _exists  # type: ignore[assignment]
os.path.isfile = _isfile  # type: ignore[assignment]
builtins.open = _open  # type: ignore[assignment]
pathlib.Path.mkdir = _path_mkdir  # type: ignore[assignment]
pathlib.Path.open = _path_open  # type: ignore[assignment]

# --- subprocess ---
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

# --- network: only socketpair may use real local sockets ---
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_create_connection = socket.create_connection
_orig_socketpair = socket.socketpair
_socketpair_depth = 0


def _blocked_connect(self, address):  # noqa: ANN001
    if _socketpair_depth:
        return _orig_connect(self, address)
    WIRING["blocked"].append({"kind": "socket.connect", "address": str(address)})
    raise OSError("socket.connect blocked in legacy scheduler test")


def _blocked_connect_ex(self, address):  # noqa: ANN001
    if _socketpair_depth:
        return _orig_connect_ex(self, address)
    WIRING["blocked"].append({"kind": "socket.connect_ex", "address": str(address)})
    return errno.ECONNREFUSED


def _blocked_create_connection(*args, **kwargs):  # noqa: ANN002, ANN003
    if _socketpair_depth:
        return _orig_create_connection(*args, **kwargs)
    WIRING["blocked"].append({"kind": "create_connection", "args": str(args)})
    raise OSError("create_connection blocked in legacy scheduler test")


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

# --- threads: record expected targets; never start; reject unknown ---
def _thread_start(self):  # noqa: ANN001
    tgt = getattr(self, "_target", None)
    name = getattr(tgt, "__name__", "") if tgt else ""
    thread_name = str(self.name or "")
    display = name or thread_name
    allowed = name in _ALLOWED_THREAD_TARGETS or thread_name.startswith(
        "wallet-editor-worker"
    )
    if allowed:
        WIRING["thread_targets"].append(display)
        if name == "schedule_loop":
            WIRING["schedule_loop_start_skipped"] = True
        return None
    WIRING["rejected_threads"].append(display)
    _dump()
    raise RuntimeError(
        f"unexpected Thread.start rejected in legacy scheduler test: {display!r}"
    )


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
        WIRING["blocked"].append({"kind": "db_connect", "module": getattr(mod, "__name__", "")})
        raise RuntimeError("database connect blocked in legacy scheduler test")

    if hasattr(mod, "connect"):
        mod.connect = blocked


def _patch_dotenv(mod) -> None:  # noqa: ANN001
    def load_dotenv(*args, **kwargs):  # noqa: ANN002, ANN003
        WIRING["dotenv_load_attempted"] = True
        return False

    mod.load_dotenv = load_dotenv
    if hasattr(mod, "find_dotenv"):
        mod.find_dotenv = lambda *a, **k: ""  # noqa: ANN002, ANN003


_real_import = builtins.__import__
_seen: set[str] = set()


def _import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
    mod = _real_import(name, globals, locals, fromlist, level)
    _maybe_patch(name, sys.modules.get(name) or mod)
    if name == "telegram" or name.startswith("telegram."):
        ext = sys.modules.get("telegram.ext")
        if ext is not None:
            _maybe_patch("telegram.ext", ext)
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
    elif name == "dotenv":
        _patch_dotenv(mod)
        _seen.add(name)


builtins.__import__ = _import
