from __future__ import annotations

from core.job_runner import JOB_REGISTRY
from integrations.script_jobs.bootstrap import register_all_script_jobs
from integrations.script_jobs.identity import parse_script_job_type, script_job_type
from tests.unit.script_jobs_child_runner import run_import_harness_pytest


def test_script_job_type_format():
    assert script_job_type("hello_world") == "script_job:hello_world"
    assert parse_script_job_type("script_job:hello_world") == "hello_world"
    assert parse_script_job_type("wallet") is None


def test_job_registry_contains_script_job_hello_world():
    register_all_script_jobs()
    assert "script_job:hello_world" in JOB_REGISTRY
    assert callable(JOB_REGISTRY["script_job:hello_world"])


def test_bootstrap_does_not_remove_existing_job_registry_entries():
    sentinel = object()
    missing = "wallet" not in JOB_REGISTRY
    previous = None if missing else JOB_REGISTRY["wallet"]
    JOB_REGISTRY["wallet"] = sentinel
    try:
        register_all_script_jobs()
        assert JOB_REGISTRY["wallet"] is sentinel
        assert "script_job:hello_world" in JOB_REGISTRY
        assert "script_job:operator_wallets_ready" in JOB_REGISTRY
    finally:
        if missing:
            JOB_REGISTRY.pop("wallet", None)
        else:
            JOB_REGISTRY["wallet"] = previous


def test_script_registry_contents_in_import_harness():
    run_import_harness_pytest("test_script_jobs_registry.py")
