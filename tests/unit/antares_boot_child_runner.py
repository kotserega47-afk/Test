from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.unit.isolated_child_env import (
    child_subprocess_kwargs,
    isolated_child_env,
    missing_dependency_hint,
)

ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).resolve().parent / "antares_boot_harness"
CHILD_TIMEOUT_SEC = 120
_COPY_DIRS = (
    "apps",
    "core",
    "modules",
    "integrations",
    "transport",
    "utils",
    "analyzers",
    "reporters",
    "automation",
    "observability",
)


@dataclass(frozen=True)
class BootRun:
    returncode: int
    stdout: str
    stderr: str
    import_attempts: list[str]


def _copy_boot_tree(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in _COPY_DIRS:
        src = ROOT / name
        if not src.is_dir():
            continue
        shutil.copytree(
            src,
            dest / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".git"),
            dirs_exist_ok=True,
        )


def _write_env_file(dest: Path, lines: dict[str, str] | None) -> None:
    path = dest / ".env"
    if not lines:
        return
    body = "".join(f"{key}={value}\n" for key, value in lines.items())
    path.write_text(body, encoding="utf-8")


def run_antares_boot(
    sandbox: Path,
    *,
    process_env: dict[str, str] | None = None,
    dotenv_lines: dict[str, str] | None = None,
    pollute_registry: bool = False,
    pollute_bind: bool = False,
) -> BootRun:
    tree = sandbox / "tree"
    _copy_boot_tree(tree)
    _write_env_file(tree, dotenv_lines)
    import_log = sandbox / "forbidden_imports.json"
    extra = {
        "ANTARES_BOOT_IMPORT_LOG": str(import_log),
        "TELEGRAM_CHAT_ID_ANALIZ": "antares-boot-harness",
    }
    if pollute_registry:
        extra["ANTARES_BOOT_POLLUTE_REGISTRY"] = "1"
    if pollute_bind:
        extra["ANTARES_BOOT_POLLUTE_BIND"] = "1"
    env = isolated_child_env(
        sandbox,
        pythonpath=str(HARNESS),
        extra=extra,
    )
    if process_env:
        env.update(process_env)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "apps.antares"],
            cwd=str(tree),
            env=env,
            capture_output=True,
            text=True,
            timeout=CHILD_TIMEOUT_SEC,
            check=False,
            **child_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"antares boot timed out after {CHILD_TIMEOUT_SEC}s\n"
            f"{exc.stdout or ''}{exc.stderr or ''}"
        ) from exc
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        raise AssertionError(missing_dependency_hint(proc.stderr))
    attempts: list[str] = []
    if import_log.exists():
        raw = json.loads(import_log.read_text(encoding="utf-8") or "[]")
        if isinstance(raw, list):
            attempts = [str(item) for item in raw]
    return BootRun(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        import_attempts=attempts,
    )
