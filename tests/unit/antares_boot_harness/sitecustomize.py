"""Sitecustomize for isolated Antares boot subprocesses.

Does not pre-insert telegram_bot/mixed/raccoon stubs into sys.modules.
Forbidden imports are recorded and raise ImportError.
Conflict injection runs once after the target module has fully loaded.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import logging
import os
import socket
import sys
import types
from pathlib import Path

_FORBIDDEN_EXACT = frozenset(
    {
        "integrations.telegram_bot",
        "integrations.tg_commands",
        "integrations.raccoon_jobs",
        "integrations.raccoon_wallet_downloader",
        "integrations.raccoon_hourly_downloader",
        "integrations.raccoon_daily_conversion",
    }
)

_WRAP_AFTER_LOAD = frozenset(
    {
        "core.job_runner",
        "core.access_rules",
        "core.rules_provider",
        "core.rules_v2.contract_publish",
        "modules.antares.handlers",
        "modules.antares.assembly",
        "dropbox",
        "psycopg",
        "psycopg2",
        "urllib.request",
        "http.client",
        "integrations.dropbox_watcher",
        "integrations.wallet_editor_registry_db.connection",
    }
)

_injected = {"registry": False, "bind": False, "assemble": False}
_orig_create_connection = socket.create_connection


def _import_log_path() -> str:
    return os.environ.get("ANTARES_BOOT_IMPORT_LOG", "")


def _events_path() -> str:
    return os.environ.get("ANTARES_BOOT_EVENTS_LOG", "")


def _ready_path() -> str:
    return os.environ.get("ANTARES_BOOT_HARNESS_READY", "")


def _write_json(path: str, payload: object) -> None:
    if not path:
        raise RuntimeError("antares boot harness log path is not set")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json_list(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        data = json.loads(handle.read() or "[]")
    if not isinstance(data, list):
        raise RuntimeError(f"antares boot harness log is not a list: {path}")
    return data


def _record_import(name: str) -> None:
    path = _import_log_path()
    data = _read_json_list(path)
    data.append(name)
    _write_json(path, data)


def _event(kind: str, **fields: object) -> None:
    path = _events_path()
    data = _read_json_list(path)
    payload = {"kind": kind, **fields}
    data.append(payload)
    _write_json(path, data)


def _is_forbidden(fullname: str) -> bool:
    if fullname in _FORBIDDEN_EXACT:
        return True
    return fullname.startswith("integrations.raccoon")


class _ForbidLoader(importlib.abc.Loader):
    def create_module(self, spec: importlib.machinery.ModuleSpec):
        _record_import(spec.name)
        raise ImportError(f"forbidden import: {spec.name}")

    def exec_module(self, module: types.ModuleType) -> None:
        raise ImportError(f"forbidden import: {getattr(module, '__name__', module)}")


class _ForbidFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        if _is_forbidden(fullname):
            return importlib.machinery.ModuleSpec(fullname, _ForbidLoader())
        return None


class _AfterLoadLoader(importlib.abc.Loader):
    def __init__(self, inner: importlib.abc.Loader, fullname: str) -> None:
        self.inner = inner
        self.fullname = fullname

    def create_module(self, spec: importlib.machinery.ModuleSpec):
        if hasattr(self.inner, "create_module"):
            return self.inner.create_module(spec)
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        self.inner.exec_module(module)
        _after_load(self.fullname, module)


class _AfterLoadFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        if fullname not in _WRAP_AFTER_LOAD:
            return None
        for finder in sys.meta_path:
            if finder is self or isinstance(finder, _ForbidFinder):
                continue
            find_spec = getattr(finder, "find_spec", None)
            if find_spec is None:
                continue
            spec = find_spec(fullname, path, target)
            if spec is None or spec.loader is None:
                continue
            spec.loader = _AfterLoadLoader(spec.loader, fullname)
            return spec
        return None


def _blocked(kind: str, message: str):
    def _raise(*_a, **_k):
        _event("blocked_external", boundary=kind)
        raise RuntimeError(message)

    return _raise


def _after_load(name: str, module: types.ModuleType) -> None:
    if name == "core.job_runner" and os.environ.get("ANTARES_BOOT_POLLUTE_REGISTRY") == "1":
        if not _injected["registry"]:
            module.JOB_REGISTRY["raccoon_hourly"] = lambda: None
            _injected["registry"] = True
            _event("conflict_injected", target="foreign_registry")
    elif name == "modules.antares.handlers" and os.environ.get("ANTARES_BOOT_POLLUTE_BIND") == "1":
        if not _injected["bind"]:
            from core.access_rules import AccessRules

            module.bind_rules(AccessRules("pollute-bind"))
            module.bind_logger(logging.getLogger("pollute-bind"))
            _injected["bind"] = True
            _event("conflict_injected", target="incompatible_bind")
    elif name == "modules.antares.assembly":
        if not _injected["assemble"]:
            orig = module.assemble_antares

            def _observe(*args, **kwargs):
                _event("assembly_called")
                try:
                    return orig(*args, **kwargs)
                except module.AntaresAssemblyError as exc:
                    _event("assembly_refused", reason=str(exc))
                    raise

            _observe.__name__ = orig.__name__
            _observe.__qualname__ = orig.__qualname__
            module.assemble_antares = _observe
            _injected["assemble"] = True
    elif name == "core.access_rules":
        orig_snap = module.AccessRules.get_snapshot

        def _observe_snapshot(self, force_sync: bool = False):
            _event("snapshot_called", force_sync=bool(force_sync))
            try:
                return orig_snap(self, force_sync=force_sync)
            except Exception as exc:
                _event(
                    "snapshot_failed",
                    exc_type=f"{type(exc).__module__}.{type(exc).__name__}",
                    exc_name=type(exc).__name__,
                )
                raise

        module.AccessRules.get_snapshot = _observe_snapshot
    elif name == "core.rules_v2.contract_publish":
        orig_eval = module.evaluate_snapshot_publish

        def _observe_publish(*args, **kwargs):
            decision = orig_eval(*args, **kwargs)
            _event(
                "publish_evaluated",
                publish_allowed=bool(decision.publish_allowed),
                has_blocking_contract=bool(decision.has_blocking_contract),
                blocking_issue_codes=list(decision.blocking_issue_codes),
                policy_mode=decision.policy_mode,
            )
            return decision

        module.evaluate_snapshot_publish = _observe_publish
    elif name == "core.rules_provider":
        def _blocked_download(*_a, **_k):
            _event("rules_download_attempted")
            raise RuntimeError("rules download blocked in antares boot harness")

        module._download_rules_workbook_atomic = _blocked_download
        module.download_file = _blocked_download

        orig_v2 = module.get_snapshot_v2

        def _observe_snapshot_v2(*, force_sync: bool = False):
            try:
                return orig_v2(force_sync=force_sync)
            except Exception as exc:
                if type(exc).__name__ == "ContractPublishRejected":
                    decision = getattr(exc, "decision", None)
                    codes = list(getattr(decision, "blocking_issue_codes", ()) or []) if decision is not None else []
                    _event(
                        "publish_rejected",
                        exc_type=f"{type(exc).__module__}.{type(exc).__name__}",
                        exc_name=type(exc).__name__,
                        blocking_issue_codes=codes,
                        policy_mode=getattr(decision, "policy_mode", None) if decision is not None else None,
                        force_sync=bool(force_sync),
                    )
                raise

        module.get_snapshot_v2 = _observe_snapshot_v2
    elif name == "dropbox":
        module.Dropbox = _blocked("dropbox.Dropbox", "Dropbox client blocked in antares boot harness")
    elif name in {"psycopg", "psycopg2"}:
        module.connect = _blocked(f"{name}.connect", f"{name}.connect blocked in antares boot harness")
    elif name == "urllib.request":
        module.urlopen = _blocked("urllib.request.urlopen", "urlopen blocked in antares boot harness")
    elif name == "http.client":
        orig_connect = module.HTTPConnection.connect

        def _http_connect(self, *a, **k):  # noqa: ANN001
            _event("blocked_external", boundary="http.client.HTTPConnection.connect")
            raise RuntimeError("HTTPConnection blocked in antares boot harness")

        module.HTTPConnection.connect = _http_connect
        del orig_connect
    elif name == "integrations.dropbox_watcher":
        module._get_dbx = _blocked("dropbox_watcher._get_dbx", "Dropbox client blocked in antares boot harness")
        orig_download = module.download_file

        def _blocked_watcher_download(*_a, **_k):
            _event("rules_download_attempted")
            raise RuntimeError("dropbox_watcher.download_file blocked in antares boot harness")

        module.download_file = _blocked_watcher_download
        del orig_download
    elif name == "integrations.wallet_editor_registry_db.connection":
        module.connect = _blocked(
            "registry_db.connection.connect",
            "postgres connect blocked in antares boot harness",
        )


def _blocked_create_connection(*args, **kwargs):
    _event("blocked_external", boundary="socket.create_connection")
    raise OSError("network blocked in antares boot harness")


def _install() -> None:
    import_log = _import_log_path()
    events_log = _events_path()
    ready = _ready_path()
    if not import_log or not events_log or not ready:
        raise RuntimeError("antares boot harness log paths are not set")
    _write_json(import_log, [])
    _write_json(events_log, [])

    sys.meta_path.insert(0, _ForbidFinder())
    sys.meta_path.insert(1, _AfterLoadFinder())

    socket.create_connection = _blocked_create_connection

    _PW = "playwright"
    _PW_SYNC = "playwright.sync_api"
    if _PW not in sys.modules:
        pw = types.ModuleType(_PW)
        sync = types.ModuleType(_PW_SYNC)

        class _Playwright:
            def __enter__(self):
                raise RuntimeError("playwright must not start during boot tests")

            def __exit__(self, *args):
                return False

        def sync_playwright():
            return _Playwright()

        sync.sync_playwright = sync_playwright
        pw.sync_api = sync
        sys.modules[_PW] = pw
        sys.modules[_PW_SYNC] = sync

    for name in _WRAP_AFTER_LOAD:
        loaded = sys.modules.get(name)
        if loaded is not None:
            _after_load(name, loaded)

    Path(ready).write_text("harness-installed\n", encoding="utf-8")
    _event("harness_installed")


_install()
