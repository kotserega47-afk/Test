"""Minimal child env for profile/gate subprocess tests. Not production."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = Path(__file__).resolve().parent / "legacy_scheduler_harness"
REGISTRATION_HARNESS_DIR = Path(__file__).resolve().parent / "registration_harness"

_SYSTEM_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_ARCHITEW6432",
)


def isolated_child_env(
    sandbox: Path,
    *,
    with_harness: bool = False,
    with_registration_harness: bool = False,
    pythonpath: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build env from a system allowlist plus explicit test values only."""
    sandbox = sandbox.resolve()
    tmp = sandbox / "tmp"
    home = sandbox / "home"
    appdata = sandbox / "AppData"
    local = sandbox / "LocalAppData"
    state = sandbox / "state"
    logs = sandbox / "logs"
    downloads = sandbox / "downloads"
    for path in (tmp, home, appdata, local, state, logs, downloads):
        path.mkdir(parents=True, exist_ok=True)

    env: dict[str, str] = {}
    for key in _SYSTEM_ALLOWLIST:
        value = os.environ.get(key)
        if value:
            env[key] = value

    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["APPDATA"] = str(appdata)
    env["LOCALAPPDATA"] = str(local)
    env["TEMP"] = str(tmp)
    env["TMP"] = str(tmp)
    env["TMPDIR"] = str(tmp)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    elif with_registration_harness:
        env["PYTHONPATH"] = str(REGISTRATION_HARNESS_DIR) + os.pathsep + str(ROOT)
    elif with_harness:
        env["PYTHONPATH"] = str(HARNESS_DIR) + os.pathsep + str(ROOT)
    else:
        env["PYTHONPATH"] = str(ROOT)
    env["LEGACY_SCHEDULER_SANDBOX"] = str(sandbox)
    env["STATE_DIR"] = str(state)
    env["RULES_XLSX_PATH"] = str(sandbox / "rules.xlsx")
    env["RULES_LOCAL_PATH"] = str(sandbox / "rules" / "rules.xlsx")
    if extra:
        env.update(extra)
    return env


def missing_dependency_hint(stderr: str) -> str:
    return (
        f"sys.executable={sys.executable} is missing a module required to run "
        "this subprocess. Install project dependencies into this same interpreter. "
        "Tests do not switch Python versions and do not skip.\n"
        f"{stderr}"
    )
