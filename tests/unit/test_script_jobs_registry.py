from __future__ import annotations

import integrations.script_jobs  # noqa: F401 — bootstrap JOB_REGISTRY
from core.job_runner import JOB_REGISTRY
from integrations.script_jobs import (
    SCRIPT_REGISTRY,
    parse_script_job_type,
    script_job_type,
)


def test_script_registry_contains_hello_world():
    assert "hello_world" in SCRIPT_REGISTRY
    spec = SCRIPT_REGISTRY["hello_world"]
    assert spec.script_key == "hello_world"
    assert spec.command_name == "run_script_hello"


def test_script_job_type_format():
    assert script_job_type("hello_world") == "script_job:hello_world"
    assert parse_script_job_type("script_job:hello_world") == "hello_world"
    assert parse_script_job_type("wallet") is None


def test_job_registry_contains_script_job_hello_world():
    assert "script_job:hello_world" in JOB_REGISTRY
    assert callable(JOB_REGISTRY["script_job:hello_world"])


def test_existing_job_registry_entries_still_present():
    for jt in ("wallet", "hourly", "rate", "download"):
        assert jt in JOB_REGISTRY
