"""Run isolated Antares assembly tests in a child process.

The child sitecustomize stubs Telegram send and Playwright. Timeout is a
failure, not success. Does not change parent SIGINT handling.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.unit.isolated_child_env import (
    child_subprocess_kwargs,
    isolated_child_env,
    missing_dependency_hint,
)

ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).resolve().parent / "antares_assembly_harness"
CHILD_TIMEOUT_SEC = 120


def run_assembly_harness_pytest(test_filename: str) -> None:
    target = HARNESS / test_filename
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated_child_env(
            Path(tmp),
            pythonpath=str(HARNESS) + os.pathsep + str(ROOT),
        )
        env["ANTARES_ASSEMBLY_HARNESS"] = "1"
        env.setdefault("TELEGRAM_CHAT_ID_ANALIZ", "antares-assembly-harness")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(target), "-q", "--tb=short"],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT_SEC,
                check=False,
                **child_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                f"assembly harness timed out after {CHILD_TIMEOUT_SEC}s: {test_filename}\n"
                f"{exc.stdout or ''}{exc.stderr or ''}"
            ) from exc
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        raise AssertionError(missing_dependency_hint(proc.stderr))
    assert proc.returncode == 0, proc.stdout + proc.stderr
