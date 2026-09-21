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
    events: list[dict]
    harness_ready: bool


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


def _read_required_json_list(path: Path, *, label: str) -> list:
    if not path.is_file():
        raise AssertionError(f"antares boot {label} is missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    except OSError as exc:
        raise AssertionError(f"antares boot {label} is unreadable: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AssertionError(f"antares boot {label} is not valid JSON: {path}") from exc
    if not isinstance(raw, list):
        raise AssertionError(f"antares boot {label} is not a list: {path}")
    return raw


def run_antares_boot(
    sandbox: Path,
    *,
    argv: list[str] | None = None,
    process_env: dict[str, str] | None = None,
    dotenv_lines: dict[str, str] | None = None,
    pollute_registry: bool = False,
    pollute_bind: bool = False,
    workbook: Path | None = None,
    pop_env: tuple[str, ...] = (),
) -> BootRun:
    tree = sandbox / "tree"
    _copy_boot_tree(tree)
    _write_env_file(tree, dotenv_lines)
    import_log = sandbox / "forbidden_imports.json"
    events_log = sandbox / "boot_events.json"
    ready = sandbox / "harness_ready.txt"
    extra = {
        "ANTARES_BOOT_IMPORT_LOG": str(import_log),
        "ANTARES_BOOT_EVENTS_LOG": str(events_log),
        "ANTARES_BOOT_HARNESS_READY": str(ready),
        "TELEGRAM_CHAT_ID_ANALIZ": "antares-boot-harness",
        "RULES_VALIDATE_AUDIT_JSONL": str(sandbox / "rules_validate_audit.jsonl"),
        "RULES_IDENTITY_REGISTRY_PATH": str(sandbox / "rules_identity_registry.v1.json"),
        "RULES_IDENTITY_SAVE": "0",
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
    if workbook is not None:
        dest = Path(env.get("RULES_XLSX_PATH") or (sandbox / "rules.xlsx"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(workbook, dest)
    for key in pop_env:
        env.pop(key, None)
    cmd = [sys.executable, "-m", "apps.antares", *(argv or [])]
    try:
        proc = subprocess.run(
            cmd,
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
    if not ready.is_file():
        raise AssertionError(
            "antares boot harness did not confirm installation\n"
            f"{proc.stdout or ''}{proc.stderr or ''}"
        )
    attempts = [str(item) for item in _read_required_json_list(import_log, label="forbidden-import log")]
    events_raw = _read_required_json_list(events_log, label="events log")
    events = [item if isinstance(item, dict) else {"kind": str(item)} for item in events_raw]
    return BootRun(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        import_attempts=attempts,
        events=events,
        harness_ready=True,
    )
