from __future__ import annotations

from core.job_runner import JOB_REGISTRY
from integrations.script_jobs.bind import KNOWN_SCRIPT_KEYS
from integrations.script_jobs.bootstrap import MIXED_SCRIPT_KEYS, register_all_script_jobs
from integrations.script_jobs.registry import SCRIPT_REGISTRY


def test_script_registry_contains_hello_world():
    assert "hello_world" in SCRIPT_REGISTRY
    spec = SCRIPT_REGISTRY["hello_world"]
    assert spec.script_key == "hello_world"
    assert spec.command_name == "run_script_hello"
    assert set(KNOWN_SCRIPT_KEYS) == set(SCRIPT_REGISTRY)
    assert set(MIXED_SCRIPT_KEYS) == set(SCRIPT_REGISTRY)
