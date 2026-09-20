"""Sitecustomize for isolated Antares boot subprocesses.

Does not pre-insert telegram_bot/mixed/raccoon stubs into sys.modules.
Forbidden imports are recorded and raise ImportError.
"""

from __future__ import annotations

import builtins
import importlib.abc
import importlib.machinery
import json
import logging
import os
import sys
import types

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


def _log_path() -> str:
    return os.environ.get("ANTARES_BOOT_IMPORT_LOG", "")


def _record(name: str) -> None:
    path = _log_path()
    if not path:
        return
    try:
        if os.path.exists(path):
            data = json.loads(Path_read(path))
            if not isinstance(data, list):
                data = []
        else:
            data = []
    except Exception:
        data = []
    data.append(name)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except Exception:
        pass


def Path_read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _is_forbidden(fullname: str) -> bool:
    if fullname in _FORBIDDEN_EXACT:
        return True
    return fullname.startswith("integrations.raccoon")


class _ForbidLoader(importlib.abc.Loader):
    def create_module(self, spec: importlib.machinery.ModuleSpec):
        _record(spec.name)
        raise ImportError(f"forbidden import: {spec.name}")

    def exec_module(self, module: types.ModuleType) -> None:
        raise ImportError(f"forbidden import: {getattr(module, '__name__', module)}")


class _ForbidFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        if _is_forbidden(fullname):
            return importlib.machinery.ModuleSpec(fullname, _ForbidLoader())
        return None


sys.meta_path.insert(0, _ForbidFinder())

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

_orig_import = builtins.__import__


def _import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
    module = _orig_import(name, globals, locals, fromlist, level)
    if os.environ.get("ANTARES_BOOT_POLLUTE_REGISTRY") == "1":
        job_runner = sys.modules.get("core.job_runner")
        if job_runner is not None and "raccoon_hourly" not in job_runner.JOB_REGISTRY:
            job_runner.JOB_REGISTRY["raccoon_hourly"] = lambda: None
    if os.environ.get("ANTARES_BOOT_POLLUTE_BIND") == "1":
        handlers = sys.modules.get("modules.antares.handlers")
        if handlers is not None and getattr(handlers, "_rules", None) is None:
            from core.access_rules import AccessRules

            handlers.bind_rules(AccessRules("pollute-bind"))
            handlers.bind_logger(logging.getLogger("pollute-bind"))
    return module


builtins.__import__ = _import
