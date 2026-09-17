"""Sitecustomize for JOB_REGISTRY / get_handlers registration dump.

Loaded only when this directory is first on PYTHONPATH.

Runs real integrations.tg_commands, integrations.raccoon_jobs (unless omitted),
integrations.script_jobs, and core.job_runner.JOB_REGISTRY. Downloaders,
analyzers, Telegram send, Playwright, and DB are import stubs. Does not
request_job or start scheduler threads.
"""

from __future__ import annotations

import builtins
import errno
import importlib.abc
import importlib.machinery
import os
import pathlib
import socket
import subprocess
import sys
import types
from pathlib import Path

_SANDBOX = Path(os.environ["LEGACY_SCHEDULER_SANDBOX"]).resolve()
_OMIT_RACCOON = os.environ.get("REGISTRATION_OMIT_RACCOON_JOBS", "").strip() in {"1", "true", "yes"}

_orig_makedirs = os.makedirs
_orig_path_exists = os.path.exists
_orig_path_isfile = os.path.isfile
_orig_open = builtins.open
_orig_path_mkdir = pathlib.Path.mkdir
_orig_path_open = pathlib.Path.open


def _blocked_internal(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("external boundary stubbed in registration test")


def _refuse(name: str):
    """Each call returns a distinct refusing callable."""

    def _fn(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError(f"{name} stubbed in registration test")

    _fn.__name__ = name
    _fn.__qualname__ = name
    return _fn


def _empty(*args, **kwargs):  # noqa: ANN002, ANN003
    return None


_INTERNAL_STUB_ATTRS: dict[str, dict] = {
    "core.schedules": {"load_schedules": lambda: [], "Schedule": type("Schedule", (), {})},
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
    "core.event_log": {"append_event": _empty},
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
    "core.access_guard": {
        "AccessContext": type("AccessContext", (), {}),
        "check_access": lambda *a, **k: True,
        "deny_message": lambda *a, **k: "",
    },
    "core.job_dispatch": {"dispatch_job_async": _blocked_internal},
    "core.lock_status": {
        "KNOWN_JOB_TYPES": (),
        "get_lock_status_for_job_types": lambda *a, **k: {},
    },
    "core.scheduler_health": {"get_scheduler_health_snapshot": lambda: {}},
    "core.scheduler_clocks_control": {"request_scheduler_clocks_reset": _empty},
    "integrations.telegram_bot": {
        "log_telegram_health_if_due": _empty,
        "send_message_sync": _blocked_internal,
        "send_message": _blocked_internal,
        "send_document_sync": _blocked_internal,
        "send_file_sync": _blocked_internal,
        "get_telegram_sender_health_snapshot": lambda: {},
    },
    "integrations.wallet_editor_auto_enable": {
        "run_auto_enable": _blocked_internal,
        "run_auto_enable_plan": _blocked_internal,
    },
    "integrations.downloader_wallets": {"run_wallet_cycle": _refuse("run_wallet_cycle")},
    "integrations.bakai_monitor_playwright": {
        "run_rate_monitor_safe": _refuse("run_rate_monitor_safe"),
    },
    "integrations.downloader": {"run_download": _blocked_internal},
    "analyzers.hourly_report": {"run_hourly_report": _blocked_internal},
    "analyzers.raccoon_daily_conversion": {"run_daily_conversion_report": _blocked_internal},
    "analyzers.raccoon_hourly_report": {"run_hourly_report": _blocked_internal},
    "integrations.raccoon_hourly_downloader": {"run_hourly_raccoon_cycle": _blocked_internal},
    "integrations.raccoon_wallet_downloader": {"run_raccoon_wallet_cycle": _blocked_internal},
    "integrations.telegram_routes": {
        "ROUTE_PLATFORM_HOURLY_REPORT": "hourly",
        "send_message_to_route": _blocked_internal,
        "routes_from_rules_v2_enabled": lambda: False,
    },
    "integrations.wallet_editor_tg": {"handle_wallet_editor_document": _blocked_internal},
    "integrations.wallet_editor_registry_refresh": {
        "run_wallet_editor_registry_refresh_job": _refuse(
            "run_wallet_editor_registry_refresh_job"
        ),
    },
    "integrations.wallet_editor_registry": {
        "build_registry_health_report": lambda *a, **k: {},
        "format_registry_health_report": lambda *a, **k: "",
        "replay_pending_outbox_records": _blocked_internal,
        "run_registry_outbox_replay_job": _refuse("run_registry_outbox_replay_job"),
    },
    "integrations.wallet_editor_registry_db": {},
    "integrations.wallet_editor_registry_db.registry_export_builder": {
        "RegistryExportArtifact": type("RegistryExportArtifact", (), {}),
        "RegistryExportBuilder": type("RegistryExportBuilder", (), {}),
        "build_registry_export_from_postgres": _blocked_internal,
        "format_registry_export_summary": lambda *a, **k: "",
    },
    "integrations.script_jobs.antares_wallets_export": {
        "download_antares_wallets_export": _blocked_internal,
    },
    "core.rules_v2.ops_rules_validate_summary": {
        "build_rules_validate_telegram_chunks_with_payload": lambda *a, **k: ([], None),
    },
    "core.rules_v2.rules_validate_audit": {
        "try_append_manual_validate_audit_from_payload": _empty,
    },
    "core.rules_v2.snapshot_fingerprint": {"rules_snapshot_fingerprint": lambda *a, **k: ""},
    "main": {
        "CONVERSION_COLUMNS": {"partner": "Партнер", "status": "Статус", "card": "Карта"},
    },
}

if _OMIT_RACCOON:
    _INTERNAL_STUB_ATTRS["integrations.raccoon_jobs"] = {}

_STUB_PACKAGES = {
    "integrations.wallet_editor_registry_db",
    "core.rules_v2",
    "integrations.script_jobs.scripts",
}


class _StubLoader(importlib.abc.Loader):
    def __init__(self, name: str) -> None:
        self.name = name

    def create_module(self, spec):  # noqa: ANN001
        mod = types.ModuleType(self.name)
        mod.__file__ = f"<registration-stub:{self.name}>"
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
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.resolve().is_relative_to(_SANDBOX):
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


def _cmd_parts(cmd) -> list[str]:  # noqa: ANN001
    if isinstance(cmd, (list, tuple)):
        return [str(x) for x in cmd]
    return [str(cmd)]


def _blocked_run(cmd, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    raise RuntimeError(f"subprocess.run blocked: {_cmd_parts(cmd)}")


class _BlockedPopen:
    def __init__(self, cmd, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError(f"subprocess.Popen blocked: {_cmd_parts(cmd)}")


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
    raise OSError("socket.connect blocked in registration test")


def _blocked_connect_ex(self, address):  # noqa: ANN001
    if _socketpair_depth:
        return _orig_connect_ex(self, address)
    return errno.ECONNREFUSED


def _blocked_create_connection(*args, **kwargs):  # noqa: ANN002, ANN003
    if _socketpair_depth:
        return _orig_create_connection(*args, **kwargs)
    raise OSError("create_connection blocked in registration test")


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
