"""Early PROJECT_PROFILE gate: isolated subprocess + pure decision tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    with pytest.raises(Exception, match="Invalid project profile"):
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


def test_legacy_mixed_sources_still_register_known_jobs_and_commands() -> None:
    """Gate does not edit job/handler tables; mixed registration stays in tg_commands."""
    tg = (ROOT / "integrations/tg_commands.py").read_text(encoding="utf-8")
    raccoon = (ROOT / "integrations/raccoon_jobs.py").read_text(encoding="utf-8")
    for job in ("wallet", "hourly", "rate", "download"):
        assert f'"{job}"' in tg
    for job in ("raccoon_wallet", "raccoon_hourly", "raccoon_daily_conversion"):
        assert f'"{job}"' in raccoon
    for command in (
        "run_wallet",
        "run_hourly",
        "run_raccoon",
        "run_hourly_raccoon",
        "auto_enable_run",
        "registry_export",
    ):
        assert f'"{command}"' in tg
    sched = (ROOT / "scheduler.py").read_text(encoding="utf-8")
    assert "get_handlers" in sched
    assert "from integrations.tg_commands import get_handlers, RULES" in sched
