"""Sitecustomize for isolated Antares PTB lifecycle subprocesses.

Allows Application initialize/start/stop/shutdown and Bot initialize/get_me.
Blocks polling, JobQueue.start, live network, Dropbox, PG, Playwright.
Synthetic HTTPXRequest.do_request only for getMe and sendMessage.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import os
import socket
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

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
        "modules.antares.application_lifecycle",
        "modules.antares.work_admission",
        "dropbox",
        "psycopg",
        "psycopg2",
        "urllib.request",
        "http.client",
        "integrations.dropbox_watcher",
        "integrations.wallet_editor_registry_db.connection",
        "telegram.ext._applicationbuilder",
        "telegram.ext._application",
        "telegram.ext._updater",
        "telegram.ext._jobqueue",
        "telegram.ext._baseupdateprocessor",
        "telegram.request._httpxrequest",
        "telegram._bot",
    }
)

_injected = {"registry": False, "bind": False, "assemble": False}
_orig_create_connection = socket.create_connection
_send_message_n = {"n": 0}
_httpx_shutdown_fail_obj = {"obj": None}


def _import_log_path() -> str:
    return os.environ.get("ANTARES_LC_IMPORT_LOG", "")


def _events_path() -> str:
    return os.environ.get("ANTARES_LC_EVENTS_LOG", "")


def _ready_path() -> str:
    return os.environ.get("ANTARES_LC_HARNESS_READY", "")


def _scenario() -> str:
    return (os.environ.get("ANTARES_LC_SCENARIO") or "whoami").strip()


def _admission_state() -> str | None:
    mod = sys.modules.get("modules.antares.work_admission")
    if mod is None:
        return None
    getter = getattr(mod, "bound_admission", None)
    if getter is None:
        return None
    admission = getter()
    if admission is None:
        return None
    return admission.state.value


def _write_json(path: str, payload: object) -> None:
    if not path:
        raise RuntimeError("antares lifecycle harness log path is not set")
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
        raise RuntimeError(f"antares lifecycle harness log is not a list: {path}")
    return data


def _record_import(name: str) -> None:
    path = _import_log_path()
    data = _read_json_list(path)
    data.append(name)
    _write_json(path, data)


def _event(kind: str, **fields: object) -> None:
    path = _events_path()
    data = _read_json_list(path)
    data.append({"kind": kind, **fields})
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


def _handler_payload(handler, *, group: object | None = None) -> dict:
    callback = getattr(handler, "callback", None)
    commands = sorted(str(item) for item in (getattr(handler, "commands", None) or ()))
    payload = {
        "handler_id": id(handler),
        "class": type(handler).__name__,
        "commands": commands,
        "callback": (
            f"{callback.__module__}.{callback.__qualname__}" if callback is not None else None
        ),
        "filters": str(getattr(handler, "filters", "") or ""),
    }
    if group is not None:
        payload["group"] = group
    return payload


def _reject_lifecycle(method: str, *, is_async: bool):
    message = f"{method} blocked in antares lifecycle harness"

    if is_async:

        async def _async(*_a, **_k):
            _event("forbidden_lifecycle", method=method)
            raise RuntimeError(message)

        return _async

    def _sync(*_a, **_k):
        _event("forbidden_lifecycle", method=method)
        raise RuntimeError(message)

    return _sync


def _wrap_reject(module: types.ModuleType, class_name: str, method: str, *, is_async: bool) -> None:
    cls = getattr(module, class_name, None)
    if cls is None or not hasattr(cls, method):
        return
    setattr(cls, method, _reject_lifecycle(f"{class_name}.{method}", is_async=is_async))


def _blocked(kind: str, message: str):
    def _raise(*_a, **_k):
        _event("blocked_external", boundary=kind)
        raise RuntimeError(message)

    return _raise


def _api_method(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def _after_load(name: str, module: types.ModuleType) -> None:
    if name == "core.job_runner" and os.environ.get("ANTARES_LC_POLLUTE_REGISTRY") == "1":
        if not _injected["registry"]:
            module.JOB_REGISTRY["raccoon_hourly"] = lambda: None
            _injected["registry"] = True
            _event("conflict_injected", target="foreign_registry")
    elif name == "modules.antares.handlers":
        orig_whoami = module.cmd_whoami

        async def _observe_whoami(update, context):
            _event("whoami_entered")
            if _scenario() == "callback_error":
                _event("whoami_raising")
                raise RuntimeError("injected whoami callback failure")
            await orig_whoami(update, context)
            _event("whoami_completed")

        module.cmd_whoami = _observe_whoami
    elif name == "modules.antares.assembly":
        if not _injected["assemble"]:
            orig = module.assemble_antares

            def _observe(*args, **kwargs):
                _event("assembly_called")
                try:
                    result = orig(*args, **kwargs)
                except module.AntaresAssemblyError as exc:
                    _event("assembly_refused", reason=str(exc))
                    raise
                _event(
                    "assembly_handlers",
                    handlers=[_handler_payload(handler) for handler in result.handlers],
                )
                return result

            module.assemble_antares = _observe
            _injected["assemble"] = True
    elif name == "modules.antares.work_admission":
        orig_bind = module.WorkAdmission._bind_instance
        orig_open = module.WorkAdmission.open
        orig_seal = module.WorkAdmission.seal

        def _observe_bind(self):
            orig_bind(self)
            _event("admission_state", at="bind", state=self.state.value)

        def _observe_open(self):
            orig_open(self)
            _event("admission_state", at="open", state=self.state.value)

        def _observe_seal(self):
            orig_seal(self)
            _event("admission_state", at="seal", state=self.state.value)

        module.WorkAdmission._bind_instance = _observe_bind
        module.WorkAdmission.open = _observe_open
        module.WorkAdmission.seal = _observe_seal
    elif name == "modules.antares.application_lifecycle":
        orig_cleanup = module._cleanup_application
        runs = {"n": 0}

        async def _count_cleanup(app):
            runs["n"] += 1
            _event(
                "ptb_cleanup_application",
                n=runs["n"],
                admission_state=_admission_state(),
            )
            return await orig_cleanup(app)

        module._cleanup_application = _count_cleanup
    elif name == "core.access_rules":
        orig_snap = module.AccessRules.get_snapshot

        def _observe_snapshot(self, force_sync: bool = False):
            _event("snapshot_called", force_sync=bool(force_sync))
            return orig_snap(self, force_sync=force_sync)

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
            raise RuntimeError("rules download blocked in antares lifecycle harness")

        module._download_rules_workbook_atomic = _blocked_download
        module.download_file = _blocked_download
    elif name == "dropbox":
        module.Dropbox = _blocked("dropbox.Dropbox", "Dropbox client blocked in antares lifecycle harness")
    elif name in {"psycopg", "psycopg2"}:
        module.connect = _blocked(f"{name}.connect", f"{name}.connect blocked in antares lifecycle harness")
    elif name == "urllib.request":
        module.urlopen = _blocked("urllib.request.urlopen", "urlopen blocked in antares lifecycle harness")
    elif name == "http.client":
        def _http_connect(self, *a, **k):  # noqa: ANN001
            _event("blocked_external", boundary="http.client.HTTPConnection.connect")
            raise RuntimeError("HTTPConnection blocked in antares lifecycle harness")

        module.HTTPConnection.connect = _http_connect
    elif name == "integrations.dropbox_watcher":
        module._get_dbx = _blocked("dropbox_watcher._get_dbx", "Dropbox client blocked in antares lifecycle harness")

        def _blocked_watcher_download(*_a, **_k):
            _event("rules_download_attempted")
            raise RuntimeError("dropbox_watcher.download_file blocked in antares lifecycle harness")

        module.download_file = _blocked_watcher_download
    elif name == "integrations.wallet_editor_registry_db.connection":
        module.connect = _blocked(
            "registry_db.connection.connect",
            "postgres connect blocked in antares lifecycle harness",
        )
    elif name == "telegram.ext._applicationbuilder":
        orig_build = module.ApplicationBuilder.build

        def _observe_build(self):
            _event("application_build_called")
            app = orig_build(self)
            _event("application_build_ok")
            return app

        module.ApplicationBuilder.build = _observe_build
    elif name == "telegram.ext._application":
        orig_add = module.Application.add_handler

        def _observe_add(self, handler, group=None, **kwargs):
            if group is None:
                group = getattr(module, "DEFAULT_GROUP", 0)
            payload = _handler_payload(handler, group=group)
            _event("handler_add_attempt", **payload)
            if kwargs:
                orig_add(self, handler, group=group, **kwargs)
            else:
                orig_add(self, handler, group)
            _event("handler_added", **payload)
            return None

        module.Application.add_handler = _observe_add

        orig_init = module.Application.initialize

        async def _observe_init(self):
            _event("application_initialize_called", admission_state=_admission_state())
            try:
                await orig_init(self)
            except Exception as exc:
                _event(
                    "application_initialize_failed",
                    exc_name=type(exc).__name__,
                    initialized=_flag_app(self),
                    admission_state=_admission_state(),
                )
                raise
            _event(
                "application_initialize_ok",
                initialized=_flag_app(self),
                admission_state=_admission_state(),
            )

        module.Application.initialize = _observe_init

        orig_start = module.Application.start

        async def _observe_start(self):
            _event("application_start_called", admission_state=_admission_state())
            if _scenario() == "fail_start":
                _event("application_start_injected_failure")
                raise RuntimeError("injected application start failure")
            try:
                await orig_start(self)
            except Exception as exc:
                _event(
                    "application_start_failed",
                    exc_name=type(exc).__name__,
                    admission_state=_admission_state(),
                )
                raise
            _event(
                "application_start_ok",
                running=bool(getattr(self, "running", False)),
                admission_state=_admission_state(),
            )

        module.Application.start = _observe_start

        orig_stop = module.Application.stop

        async def _observe_stop(self):
            _event("application_stop_called")
            if _scenario() == "double_cancel_cleanup":
                from cleanup_gate import gates

                entered, release = gates()
                entered.set()
                _event("cleanup_hold_entered", boundary="application.stop")
                await release.wait()
                _event("cleanup_hold_released", boundary="application.stop")
            await orig_stop(self)
            _event("application_stop_ok")

        module.Application.stop = _observe_stop

        orig_shutdown = module.Application.shutdown

        async def _observe_shutdown(self):
            _event("application_shutdown_called")
            if _scenario() in {"fail_cleanup_only", "fail_shutdown_before_cleanup"}:
                _event("application_shutdown_injected_failure")
                raise RuntimeError("injected application shutdown failure")
            await orig_shutdown(self)
            _event("application_shutdown_ok")

        module.Application.shutdown = _observe_shutdown

        _wrap_reject(module, "Application", "run_polling", is_async=False)
        _wrap_reject(module, "Application", "run_webhook", is_async=False)
    elif name == "telegram.ext._updater":
        _wrap_reject(module, "Updater", "start_polling", is_async=True)
        _wrap_reject(module, "Updater", "start_webhook", is_async=True)
    elif name == "telegram.ext._jobqueue":
        _wrap_reject(module, "JobQueue", "start", is_async=True)
    elif name == "telegram.ext._baseupdateprocessor":
        orig_proc = module.SimpleUpdateProcessor.initialize

        async def _observe_proc(self):
            _event("processor_initialize_called")
            if _scenario() == "fail_after_bot":
                _event("processor_initialize_injected_failure")
                raise RuntimeError("injected processor initialize failure")
            await orig_proc(self)
            _event("processor_initialize_ok")

        module.SimpleUpdateProcessor.initialize = _observe_proc
    elif name == "telegram.request._httpxrequest":
        orig_h_init = module.HTTPXRequest.initialize

        async def _observe_h_init(self):
            _event("httpx_initialize_called")
            if _scenario() in {"fail_requests", "fail_requests_and_cleanup", "cancel_during_initialize_cleanup"}:
                _event("httpx_initialize_injected_failure")
                raise RuntimeError("injected HTTPXRequest.initialize failure")
            await orig_h_init(self)
            _event("httpx_initialize_ok")

        module.HTTPXRequest.initialize = _observe_h_init

        orig_h_shut = module.HTTPXRequest.shutdown

        async def _observe_h_shut(self):
            _event("httpx_shutdown_called")
            if _scenario() == "cancel_during_initialize_cleanup":
                from cleanup_gate import gates

                entered, release = gates()
                if not entered.is_set():
                    entered.set()
                    _event("cleanup_hold_entered", boundary="HTTPXRequest.shutdown")
                    await release.wait()
                    _event("cleanup_hold_released", boundary="HTTPXRequest.shutdown")
                if _httpx_shutdown_fail_obj["obj"] is None:
                    _httpx_shutdown_fail_obj["obj"] = self
                if self is _httpx_shutdown_fail_obj["obj"]:
                    _event("httpx_shutdown_injected_failure")
                    raise RuntimeError("injected HTTPXRequest.shutdown failure")
            if _scenario() == "fail_requests_and_cleanup":
                if _httpx_shutdown_fail_obj["obj"] is None:
                    _httpx_shutdown_fail_obj["obj"] = self
                if self is _httpx_shutdown_fail_obj["obj"]:
                    _event("httpx_shutdown_injected_failure")
                    raise RuntimeError("injected HTTPXRequest.shutdown failure")
            await orig_h_shut(self)
            client = getattr(self, "_client", None)
            _event(
                "httpx_shutdown_ok",
                closed=bool(client is not None and getattr(client, "is_closed", False)),
            )

        module.HTTPXRequest.shutdown = _observe_h_shut

        orig_do = module.HTTPXRequest.do_request

        async def _synthetic_do(self, url, method, request_data=None, **kwargs):
            api = _api_method(str(url))
            _event("do_request", api_method=api, http_method=str(method))
            if api == "getMe":
                if _scenario() == "fail_get_me":
                    _event("get_me_injected_failure")
                    body = b'{"ok":false,"error_code":401,"description":"Unauthorized"}'
                    return 401, body
                body = (
                    b'{"ok":true,"result":{"id":123456,"is_bot":true,'
                    b'"first_name":"AntaresSandbox","username":"sandbox_bot"}}'
                )
                return 200, body
            if api == "sendMessage":
                _send_message_n["n"] += 1
                _event("send_message_synthetic", n=_send_message_n["n"])
                body = (
                    b'{"ok":true,"result":{"message_id":'
                    + str(_send_message_n["n"]).encode("ascii")
                    + b',"date":1,"chat":{"id":1,"type":"private"},'
                    b'"from":{"id":123456,"is_bot":true,"first_name":"AntaresSandbox"},'
                    b'"text":"ok"}}'
                )
                return 200, body
            _event("blocked_external", boundary=f"telegram.api.{api or 'unknown'}")
            raise RuntimeError(f"telegram method {api!r} blocked in antares lifecycle harness")

        module.HTTPXRequest.do_request = _synthetic_do
        del orig_do
    elif name == "telegram._bot":
        orig_bot_init = module.Bot.initialize

        async def _observe_bot_init(self):
            _event("bot_initialize_called")
            try:
                await orig_bot_init(self)
            except Exception as exc:
                _event(
                    "bot_initialize_failed",
                    exc_name=type(exc).__name__,
                    requests_initialized=bool(getattr(self, "_requests_initialized", False)),
                    bot_initialized=bool(getattr(self, "_bot_initialized", False)),
                )
                raise
            _event(
                "bot_initialize_ok",
                requests_initialized=bool(getattr(self, "_requests_initialized", False)),
                bot_initialized=bool(getattr(self, "_bot_initialized", False)),
            )

        module.Bot.initialize = _observe_bot_init

        orig_bot_shut = module.Bot.shutdown

        async def _observe_bot_shut(self):
            _event(
                "bot_shutdown_called",
                requests_initialized=bool(getattr(self, "_requests_initialized", False)),
            )
            await orig_bot_shut(self)
            _event(
                "bot_shutdown_ok",
                requests_initialized=bool(getattr(self, "_requests_initialized", False)),
            )

        module.Bot.shutdown = _observe_bot_shut


def _flag_app(app) -> bool:
    return bool(getattr(app, "_initialized", False))


def _blocked_create_connection(*args, **kwargs):
    _event("blocked_external", boundary="socket.create_connection")
    raise OSError("network blocked in antares lifecycle harness")


def _install() -> None:
    import_log = _import_log_path()
    events_log = _events_path()
    ready = _ready_path()
    if not import_log or not events_log or not ready:
        raise RuntimeError("antares lifecycle harness log paths are not set")
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
                raise RuntimeError("playwright must not start during lifecycle tests")

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
