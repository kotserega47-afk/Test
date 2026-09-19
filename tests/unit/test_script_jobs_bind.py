"""Selective script JOB_REGISTRY bind without package auto-registration."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.job_runner import Actor, JOB_REGISTRY
from integrations.script_jobs.bind import ScriptJobBindError, register_script_job
from integrations.script_jobs.bootstrap import register_all_script_jobs
from tests.unit.isolated_child_env import isolated_child_env, missing_dependency_hint

ROOT = Path(__file__).resolve().parents[2]

# Script-registration must not pull operator_wallets_ready → main. Importing
# JOB_REGISTRY still loads dropbox_watcher via core.job_runner → rules_provider
# (pre-existing; not a bind.py import).
_FORBIDDEN_AFTER_BIND = (
    "integrations.script_jobs.runtime",
    "integrations.script_jobs.registry",
    "integrations.script_jobs.scripts.operator_wallets_ready",
    "main",
    "analyzers.selector",
    "integrations.telegram_bot",
)


def _run_child(script: str, extra: dict[str, str] | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated_child_env(Path(tmp), extra=extra)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        raise AssertionError(missing_dependency_hint(proc.stderr))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_unknown_conflict_and_repeat_identity_in_fresh_process() -> None:
    script = """
import json
from core.job_runner import JOB_REGISTRY
from integrations.script_jobs.bind import ScriptJobBindError, register_script_job

before = dict(JOB_REGISTRY)
unknown_ok = False
try:
    register_script_job("not_a_script")
except ScriptJobBindError:
    unknown_ok = dict(JOB_REGISTRY) == before

first = register_script_job("hello_world")
second = register_script_job("hello_world")
identity_ok = first is second and JOB_REGISTRY["script_job:hello_world"] is first

sentinel = object()
JOB_REGISTRY["script_job:hello_world"] = sentinel
conflict_ok = False
try:
    register_script_job("hello_world")
except ScriptJobBindError:
    conflict_ok = JOB_REGISTRY["script_job:hello_world"] is sentinel

print(json.dumps({
    "unknown_ok": unknown_ok,
    "identity_ok": identity_ok,
    "conflict_ok": conflict_ok,
    "keys_after_bind": sorted(k for k in JOB_REGISTRY if k != "script_job:hello_world" or JOB_REGISTRY[k] is sentinel),
}))
"""
    payload = _run_child(script)
    assert payload["unknown_ok"] is True
    assert payload["identity_ok"] is True
    assert payload["conflict_ok"] is True


def test_mixed_bootstrap_in_fresh_process_registers_both_keys() -> None:
    script = f"""
import json, sys
from integrations.script_jobs.bootstrap import register_all_script_jobs
from core.job_runner import JOB_REGISTRY
register_all_script_jobs()
hello = JOB_REGISTRY["script_job:hello_world"]
ready = JOB_REGISTRY["script_job:operator_wallets_ready"]
register_all_script_jobs()
print(json.dumps({{
    "keys": sorted(JOB_REGISTRY),
    "same_callable": hello is ready,
    "repeat_same": JOB_REGISTRY["script_job:hello_world"] is hello and JOB_REGISTRY["script_job:operator_wallets_ready"] is ready,
    "loaded": sorted(m for m in sys.modules if m in {list(_FORBIDDEN_AFTER_BIND)!r}),
}}))
"""
    payload = _run_child(script)
    assert payload["keys"] == ["script_job:hello_world", "script_job:operator_wallets_ready"]
    assert payload["same_callable"] is False
    assert payload["repeat_same"] is True
    assert payload["loaded"] == []


def test_package_and_bind_import_do_not_register_jobs() -> None:
    script = """
import json, sys
import integrations.script_jobs as pkg
import integrations.script_jobs.bind as bind
from core.job_runner import JOB_REGISTRY
print(json.dumps({
    "keys": sorted(JOB_REGISTRY),
    "pkg_file": pkg.__file__,
    "bind_file": bind.__file__,
}))
"""
    payload = _run_child(script)
    assert payload["keys"] == []
    assert payload["pkg_file"].replace("\\", "/").endswith("integrations/script_jobs/__init__.py")
    assert payload["bind_file"].replace("\\", "/").endswith("integrations/script_jobs/bind.py")


def test_selective_bind_adds_only_chosen_key_without_heavy_imports() -> None:
    script = f"""
import json, sys
from integrations.script_jobs.bind import register_script_job
from core.job_runner import JOB_REGISTRY
register_script_job("operator_wallets_ready")
print(json.dumps({{
    "keys": sorted(JOB_REGISTRY),
    "loaded": sorted(m for m in sys.modules if m in {list(_FORBIDDEN_AFTER_BIND)!r}),
}}))
"""
    payload = _run_child(script)
    assert payload["keys"] == ["script_job:operator_wallets_ready"]
    assert payload["loaded"] == []


def test_repeat_bind_keeps_executor_identity() -> None:
    first = register_script_job("hello_world")
    second = register_script_job("hello_world")
    assert first is second
    assert JOB_REGISTRY["script_job:hello_world"] is first


def test_unknown_key_does_not_change_registry() -> None:
    before = dict(JOB_REGISTRY)
    with pytest.raises(ScriptJobBindError, match="unknown script_key"):
        register_script_job("not_a_script")
    assert dict(JOB_REGISTRY) == before


def test_conflict_does_not_overwrite_registry() -> None:
    job_type = "script_job:hello_world"
    sentinel = object()
    previous = JOB_REGISTRY.get(job_type)
    JOB_REGISTRY[job_type] = sentinel
    try:
        with pytest.raises(ScriptJobBindError, match="conflicting"):
            register_script_job("hello_world")
        assert JOB_REGISTRY[job_type] is sentinel
    finally:
        if previous is None:
            JOB_REGISTRY.pop(job_type, None)
        else:
            JOB_REGISTRY[job_type] = previous


def test_registered_executor_forwards_actor_and_script_key() -> None:
    executor = register_script_job("operator_wallets_ready")
    actor = Actor(kind="tg", chat_id=11, user_id=22)
    with patch("integrations.script_jobs.runtime.run_script_job") as run:
        executor(actor)
    run.assert_called_once_with(actor, "operator_wallets_ready")


def test_registered_executor_propagates_runtime_error() -> None:
    executor = register_script_job("hello_world")
    with patch(
        "integrations.script_jobs.runtime.run_script_job",
        side_effect=RuntimeError("script boom"),
    ):
        with pytest.raises(RuntimeError, match="script boom"):
            executor(Actor(kind="cli"))


def test_mixed_bootstrap_registers_distinct_executors() -> None:
    register_all_script_jobs()
    hello = JOB_REGISTRY["script_job:hello_world"]
    ready = JOB_REGISTRY["script_job:operator_wallets_ready"]
    assert hello is not ready
    again_hello = register_script_job("hello_world")
    again_ready = register_script_job("operator_wallets_ready")
    assert again_hello is hello
    assert again_ready is ready
    captured: list[tuple] = []

    def _record(actor, script_key):
        captured.append((actor, script_key))

    with patch("integrations.script_jobs.runtime.run_script_job", side_effect=_record):
        actor_a = Actor(kind="cli")
        actor_b = Actor(kind="tg", chat_id=1, user_id=2)
        hello(actor_a)
        ready(actor_b)
    assert captured == [(actor_a, "hello_world"), (actor_b, "operator_wallets_ready")]


def test_package_init_source_does_not_import_runtime_or_registry() -> None:
    src = (ROOT / "integrations" / "script_jobs" / "__init__.py").read_text(encoding="utf-8")
    assert "script_jobs.runtime" not in src
    assert "script_jobs.registry" not in src
    bind_src = (ROOT / "integrations" / "script_jobs" / "bind.py").read_text(encoding="utf-8")
    assert "dropbox_watcher" not in bind_src
    assert "telegram_bot" not in bind_src
    assert "script_jobs.runtime" not in bind_src.split("def _make_executor", 1)[0]
    runtime = (ROOT / "integrations" / "script_jobs" / "runtime.py").read_text(encoding="utf-8")
    assert "register_script_jobs()" not in runtime
