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
HARNESS = Path(__file__).resolve().parent / "antares_lifecycle_harness"
CHILD_TIMEOUT_SEC = 90
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
class LifecycleRun:
    returncode: int
    stdout: str
    stderr: str
    import_attempts: list[str]
    events: list[dict]
    report: dict
    harness_ready: bool


def _copy_tree(dest: Path) -> None:
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
    if not lines:
        return
    body = "".join(f"{key}={value}\n" for key, value in lines.items())
    (dest / ".env").write_text(body, encoding="utf-8")


def _read_json(path: Path, *, label: str, default):
    if not path.is_file():
        raise AssertionError(f"antares lifecycle {label} is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8") or json.dumps(default))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"antares lifecycle {label} is unreadable: {path}") from exc


def run_antares_lifecycle(
    sandbox: Path,
    *,
    scenario: str,
    process_env: dict[str, str] | None = None,
    dotenv_lines: dict[str, str] | None = None,
    workbook: Path | None = None,
) -> LifecycleRun:
    tree = sandbox / "tree"
    _copy_tree(tree)
    _write_env_file(tree, dotenv_lines)
    import_log = sandbox / "forbidden_imports.json"
    events_log = sandbox / "lifecycle_events.json"
    ready = sandbox / "harness_ready.txt"
    report_path = sandbox / "lifecycle_report.json"
    extra = {
        "ANTARES_LC_IMPORT_LOG": str(import_log),
        "ANTARES_LC_EVENTS_LOG": str(events_log),
        "ANTARES_LC_HARNESS_READY": str(ready),
        "ANTARES_LC_REPORT": str(report_path),
        "ANTARES_LC_SCENARIO": scenario,
        "TELEGRAM_CHAT_ID_ANALIZ": "antares-lifecycle-harness",
        "RULES_VALIDATE_AUDIT_JSONL": str(sandbox / "rules_validate_audit.jsonl"),
        "RULES_IDENTITY_REGISTRY_PATH": str(sandbox / "rules_identity_registry.v1.json"),
        "RULES_IDENTITY_SAVE": "0",
        "PYTHONPATH": str(HARNESS),
    }
    env = isolated_child_env(sandbox, pythonpath=str(HARNESS), extra=extra)
    if process_env:
        env.update(process_env)
    if workbook is not None:
        dest = Path(env.get("RULES_XLSX_PATH") or (sandbox / "rules.xlsx"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != Path(workbook).resolve():
            shutil.copy(workbook, dest)
    cmd = [sys.executable, "-m", "run_lifecycle"]
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
            f"antares lifecycle timed out after {CHILD_TIMEOUT_SEC}s scenario={scenario}\n"
            f"{exc.stdout or ''}{exc.stderr or ''}"
        ) from exc
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        raise AssertionError(missing_dependency_hint(proc.stderr))
    if not ready.is_file():
        raise AssertionError(
            "antares lifecycle harness did not confirm installation\n"
            f"{proc.stdout or ''}{proc.stderr or ''}"
        )
    attempts = [str(item) for item in _read_json(import_log, label="forbidden-import log", default=[])]
    events_raw = _read_json(events_log, label="events log", default=[])
    events = [item if isinstance(item, dict) else {"kind": str(item)} for item in events_raw]
    report = _read_json(report_path, label="report", default={})
    if not isinstance(report, dict):
        raise AssertionError("lifecycle report is not an object")
    return LifecycleRun(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        import_attempts=attempts if isinstance(attempts, list) else [],
        events=events,
        report=report,
        harness_ready=True,
    )
