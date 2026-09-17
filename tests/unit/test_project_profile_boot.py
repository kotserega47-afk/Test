"""Early PROJECT_PROFILE gate: isolated subprocess + pure decision tests."""

from __future__ import annotations

import json
import os
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
    env = os.environ.copy()
    env.pop("PROJECT_PROFILE", None)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # Keep child from inheriting tokens / browser creds for safety.
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TG_BOT_TOKEN",
        "ANTARES_LOGIN",
        "ANTARES_PASSWORD",
        "DATABASE_URL",
        "RACCOON_LOGIN",
        "RACCOON_PASSWORD",
    ):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
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


_PARSER_BASE = "acfb9958df644679b85feecaf4e6a9acf65b884b"

_JOB_HANDLER_FILES = (
    "integrations/tg_commands.py",
    "integrations/raccoon_jobs.py",
    "integrations/downloader.py",
    "integrations/hourly_downloader.py",
    "integrations/raccoon_hourly_downloader.py",
    "integrations/raccoon_wallet_downloader.py",
    "automation/worker.py",
    "core/job_runner.py",
    "core/job_dispatch.py",
)

HARNESS_DIR = ROOT / "tests" / "unit" / "legacy_scheduler_harness"

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

_SCHEDULER_PYTHON: str | None = None


def _scheduler_python() -> str:
    """Use an interpreter that can import scheduler.py third-party deps."""
    global _SCHEDULER_PYTHON
    if _SCHEDULER_PYTHON:
        return _SCHEDULER_PYTHON
    candidates: list[list[str]] = [[sys.executable]]
    swapped = sys.executable.replace("Python313", "Python312")
    if swapped != sys.executable and Path(swapped).is_file():
        candidates.append([swapped])
    probe = "import pandas, requests, dotenv"
    for cmd in candidates:
        proc = subprocess.run(
            [*cmd, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if proc.returncode == 0:
            _SCHEDULER_PYTHON = cmd[0]
            return cmd[0]
    py = subprocess.run(
        ["py", "-3.12", "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if py.returncode == 0:
        # Resolve the 3.12 executable so env/sitecustomize stay simple.
        resolved = subprocess.run(
            ["py", "-3.12", "-c", "import sys; print(sys.executable)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        exe = (resolved.stdout or "").strip()
        if exe:
            _SCHEDULER_PYTHON = exe
            return exe
    raise RuntimeError(
        "No Python with pandas/requests/dotenv found for scheduler.py entry test."
    )


def _run_scheduler_py(extra_env: dict[str, str] | None) -> tuple[subprocess.CompletedProcess[str], dict]:
    with tempfile.TemporaryDirectory() as tmp:
        wiring_path = Path(tmp) / "wiring.json"
        env = os.environ.copy()
        env.pop("PROJECT_PROFILE", None)
        for key in (
            "TG_BOT_TOKEN",
            "ANTARES_LOGIN",
            "ANTARES_PASSWORD",
            "DATABASE_URL",
            "RACCOON_LOGIN",
            "RACCOON_PASSWORD",
        ):
            env.pop(key, None)
        env["PYTHONPATH"] = str(HARNESS_DIR) + os.pathsep + str(ROOT) + os.pathsep + env.get(
            "PYTHONPATH", ""
        )
        env["LEGACY_SCHEDULER_WIRING_PATH"] = str(wiring_path)
        env["TELEGRAM_BOT_TOKEN"] = "123456:legacy-scheduler-wiring-test"
        env["TELEGRAM_CHAT_ID_ANALIZ"] = "1"
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            [_scheduler_python(), str(ROOT / "scheduler.py")],
            cwd=str(ROOT),
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


def test_job_and_handler_files_unchanged_vs_parser_base() -> None:
    """Jobs/handlers preserved vs PR #5 base. File identity, not a runtime launch."""
    diff = subprocess.run(
        ["git", "diff", "--name-only", _PARSER_BASE, "--", *_JOB_HANDLER_FILES],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert diff.returncode == 0, diff.stderr
    assert diff.stdout.strip() == "", diff.stdout


@pytest.mark.parametrize("extra", [{}, {"PROJECT_PROFILE": ""}, {"PROJECT_PROFILE": "   "}])
def test_subprocess_legacy_scheduler_entrypoint_wiring(extra: dict[str, str]) -> None:
    """Run scheduler.py with explicit stubs. Proves gate + main wiring, not jobs."""
    proc, payload = _run_scheduler_py(extra)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert payload, "wiring dump missing — stubs did not run"
    assert payload.get("gate_passed") is True
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
    assert payload.get("playwright_install_stubbed") is True
    blocked_kinds = {item.get("kind") for item in payload.get("blocked") or [] if isinstance(item, dict)}
    assert "subprocess.run" in blocked_kinds
    # Stubs intercepted Chromium install; they do not prove a real browser/job run.
