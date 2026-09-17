"""Early PROJECT_PROFILE gate: isolated subprocess + pure decision tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from core.project_profile import InvalidProjectProfileError
from core.project_profile_boot import (
    LEGACY_MIXED,
    UnwiredProjectProfileError,
    decide_legacy_scheduler_boot,
    enforce_legacy_scheduler_profile,
    project_profile_env_value,
)
from tests.unit.isolated_child_env import isolated_child_env, missing_dependency_hint

ROOT = Path(__file__).resolve().parents[2]

_REJECT_IMPORT = """
import json
import sys
mods_before = set(sys.modules)
try:
    import scheduler  # noqa: F401
except SystemExit as exc:
    code = exc.code
    if code is None:
        code = 0
    print(json.dumps({
        "exit": int(code) if isinstance(code, int) else 1,
        "tg_commands": "integrations.tg_commands" in sys.modules,
        "downloader": "integrations.downloader" in sys.modules,
        "telegram_ext": "telegram.ext" in sys.modules,
        "playwright": any(n.startswith("playwright") for n in sys.modules),
        "worker": "automation.worker" in sys.modules,
        "job_runner": "core.job_runner" in sys.modules,
        "boot": "core.project_profile_boot" in sys.modules,
    }))
    raise SystemExit(code)
print(json.dumps({"exit": 0, "imported": True}))
"""

_LEGACY_BOOT_ONLY = """
from core.project_profile_boot import LEGACY_MIXED, enforce_legacy_scheduler_profile
mode = enforce_legacy_scheduler_profile()
assert mode == LEGACY_MIXED
print("legacy_ok")
"""

_PARSER_CLEAN_IMPORT = """
from core.project_profile import parse_project_profile, InvalidProjectProfileError
assert parse_project_profile(None).implicit_default is True
assert parse_project_profile("raccoon").name == "raccoon"
try:
    parse_project_profile("Antares")
except InvalidProjectProfileError:
    print("parser_ok")
else:
    raise SystemExit("expected InvalidProjectProfileError")
"""


def _run_python(script: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated_child_env(Path(tmp), with_harness=False, extra=extra_env)
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )


def test_decide_legacy_for_missing_and_blank() -> None:
    assert decide_legacy_scheduler_boot(None) == LEGACY_MIXED
    assert decide_legacy_scheduler_boot("") == LEGACY_MIXED
    assert decide_legacy_scheduler_boot("  \t") == LEGACY_MIXED


@pytest.mark.parametrize("name", ["antares", "raccoon", "wr"])
def test_decide_rejects_explicit_unwired_profiles(name: str) -> None:
    with pytest.raises(UnwiredProjectProfileError, match="refusing mixed JOB_REGISTRY"):
        decide_legacy_scheduler_boot(name)


def test_decide_rejects_unknown_via_parser() -> None:
    with pytest.raises(InvalidProjectProfileError, match="Invalid project profile"):
        decide_legacy_scheduler_boot("Antares")


def test_env_reader_distinguishes_missing_and_empty() -> None:
    assert project_profile_env_value({}) is None
    assert project_profile_env_value({"PROJECT_PROFILE": ""}) == ""
    assert project_profile_env_value({"PROJECT_PROFILE": "wr"}) == "wr"


def test_enforce_legacy_does_not_exit() -> None:
    assert enforce_legacy_scheduler_profile(environ={}) == LEGACY_MIXED
    assert enforce_legacy_scheduler_profile(environ={"PROJECT_PROFILE": "  "}) == LEGACY_MIXED


def test_enforce_exits_nonzero_for_unwired() -> None:
    with pytest.raises(SystemExit) as exc:
        enforce_legacy_scheduler_profile(environ={"PROJECT_PROFILE": "antares"})
    assert exc.value.code == 2


def _assert_reject_subprocess(profile: str) -> None:
    proc = _run_python(_REJECT_IMPORT, {"PROJECT_PROFILE": profile})
    assert proc.returncode == 2, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["exit"] == 2
    assert payload["tg_commands"] is False
    assert payload["downloader"] is False
    assert payload["telegram_ext"] is False
    assert payload["playwright"] is False
    assert payload["worker"] is False
    assert payload["boot"] is True
    assert "integrations.downloader" not in proc.stderr
    combined = proc.stderr + proc.stdout
    if profile in {"antares", "raccoon", "wr"}:
        assert "refusing mixed JOB_REGISTRY" in combined
        assert f"PROJECT_PROFILE='{profile}'" in combined or f'PROJECT_PROFILE="{profile}"' in combined
    else:
        assert "Invalid project profile" in combined


@pytest.mark.parametrize("profile", ["not-a-profile", "Antares", "ANTARES"])
def test_subprocess_unknown_profile_stops_before_side_effects(profile: str) -> None:
    _assert_reject_subprocess(profile)


@pytest.mark.parametrize("profile", ["antares", "raccoon", "wr"])
def test_subprocess_explicit_unwired_profile_stops_before_side_effects(profile: str) -> None:
    _assert_reject_subprocess(profile)


@pytest.mark.parametrize("extra", [{}, {"PROJECT_PROFILE": ""}, {"PROJECT_PROFILE": "   "}])
def test_subprocess_legacy_boot_without_importing_scheduler(extra: dict[str, str]) -> None:
    proc = _run_python(_LEGACY_BOOT_ONLY, extra)
    assert proc.returncode == 0, proc.stderr
    assert "legacy_ok" in proc.stdout


def test_subprocess_parser_import_without_reload() -> None:
    proc = _run_python(_PARSER_CLEAN_IMPORT)
    assert proc.returncode == 0, proc.stderr
    assert "parser_ok" in proc.stdout


def test_scheduler_calls_gate_before_runtime_imports() -> None:
    text = (ROOT / "scheduler.py").read_text(encoding="utf-8")
    gate_at = text.find("enforce_legacy_scheduler_profile()")
    assert gate_at != -1
    assert gate_at < text.find("from telegram.ext")
    assert gate_at < text.find("integrations.tg_commands")
    assert gate_at < text.find("automation.worker")
    assert gate_at < text.find("utils.logger")


_EXPECTED_COMMANDS = {
    "start",
    "help",
    "status",
    "run_wallet",
    "run_hourly",
    "run_raccoon",
    "run_hourly_raccoon",
    "auto_enable_run",
    "registry_export",
}


def _run_scheduler_py(extra_env: dict[str, str] | None) -> tuple[subprocess.CompletedProcess[str], dict]:
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        wiring_path = sandbox / "wiring.json"
        extra = {
            "LEGACY_SCHEDULER_WIRING_PATH": str(wiring_path),
            "TELEGRAM_BOT_TOKEN": "123456:legacy-scheduler-wiring-test",
            "TELEGRAM_CHAT_ID_ANALIZ": "1",
        }
        if extra_env:
            extra.update(extra_env)
        env = isolated_child_env(sandbox, with_harness=True, extra=extra)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scheduler.py")],
            cwd=str(sandbox),
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        payload: dict = {}
        if wiring_path.is_file():
            payload = json.loads(wiring_path.read_text(encoding="utf-8"))
        return proc, payload


@pytest.mark.parametrize("extra", [{}, {"PROJECT_PROFILE": ""}, {"PROJECT_PROFILE": "   "}])
def test_subprocess_legacy_scheduler_entrypoint_wiring(extra: dict[str, str]) -> None:
    """Run scheduler.py with explicit stubs. Proves main wiring, not jobs."""
    proc, payload = _run_scheduler_py(extra)
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        pytest.fail(missing_dependency_hint(proc.stderr))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert payload, "wiring dump missing — stubs did not run"
    assert "gate_passed" not in payload
    assert payload.get("handlers_added", 0) >= 20
    commands = set(payload.get("handler_commands") or [])
    assert _EXPECTED_COMMANDS <= commands
    assert "MessageHandler" in (payload.get("handler_types") or [])
    assert payload.get("ensure_worker_started") is True
    assert "schedule_loop" in (payload.get("thread_targets") or [])
    assert payload.get("schedule_loop_start_skipped") is True
    assert payload.get("run_polling") is True
    assert payload.get("rules_snapshot") is True
    assert payload.get("rules_force_sync") is True
    # downloader/playwright/worker internals are stubbed; this is wiring, not job results.


_HARNESS_GUARD_SCRIPT = r"""
import json
import socket
import threading

events = []

def unexpected():
    events.append("ran")

try:
    threading.Thread(target=unexpected, name="sneaky-test-thread").start()
except RuntimeError as exc:
    events.append("thread_rejected")
    events.append(str(exc))
else:
    raise SystemExit("unknown thread was allowed to start")

try:
    socket.create_connection(("example.invalid", 80), timeout=1)
except OSError:
    events.append("create_connection_blocked")
else:
    raise SystemExit("create_connection was allowed")

sock = socket.socket()
try:
    sock.connect(("1.1.1.1", 53))
except OSError:
    events.append("connect_blocked")
else:
    raise SystemExit("connect was allowed")
finally:
    sock.close()

sock = socket.socket()
rc = sock.connect_ex(("1.1.1.1", 53))
sock.close()
if rc == 0:
    raise SystemExit("connect_ex succeeded")
events.append("connect_ex_blocked")

listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(1)
port = listener.getsockname()[1]
client = socket.socket()
try:
    client.connect(("127.0.0.1", port))
except OSError:
    events.append("loopback_tcp_blocked")
else:
    raise SystemExit("loopback TCP connect was allowed")
finally:
    client.close()
    listener.close()

a, b = socket.socketpair()
a.close()
b.close()
events.append("socketpair_ok")
print(json.dumps(events))
"""


def test_harness_rejects_unknown_thread_and_blocks_network() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        wiring_path = sandbox / "wiring.json"
        env = isolated_child_env(
            sandbox,
            with_harness=True,
            extra={"LEGACY_SCHEDULER_WIRING_PATH": str(wiring_path)},
        )
        proc = subprocess.run(
            [sys.executable, "-c", _HARNESS_GUARD_SCRIPT],
            cwd=str(sandbox),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        events = json.loads(proc.stdout.strip().splitlines()[-1])
        assert "thread_rejected" in events
        assert "sneaky-test-thread" in " ".join(events) or "unexpected" in " ".join(events).lower()
        assert "create_connection_blocked" in events
        assert "connect_blocked" in events
        assert "connect_ex_blocked" in events
        assert "loopback_tcp_blocked" in events
        assert "socketpair_ok" in events
        assert "ran" not in events
        payload = json.loads(wiring_path.read_text(encoding="utf-8"))
        kinds = {item.get("kind") for item in payload.get("blocked") or [] if isinstance(item, dict)}
        assert "socket.connect" in kinds
        assert "socket.connect_ex" in kinds
        assert "create_connection" in kinds
        assert payload.get("rejected_threads")
