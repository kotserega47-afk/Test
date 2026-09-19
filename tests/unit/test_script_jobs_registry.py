from __future__ import annotations

from tests.unit.telegram_bot_import_stub import install_telegram_bot_stub

install_telegram_bot_stub()

from core.job_runner import JOB_REGISTRY
from integrations.script_jobs.bind import KNOWN_SCRIPT_KEYS
from integrations.script_jobs.bootstrap import MIXED_SCRIPT_KEYS, register_all_script_jobs
from integrations.script_jobs.identity import parse_script_job_type, script_job_type
from integrations.script_jobs.registry import SCRIPT_REGISTRY


def test_script_registry_contains_hello_world():
    assert "hello_world" in SCRIPT_REGISTRY
    spec = SCRIPT_REGISTRY["hello_world"]
    assert spec.script_key == "hello_world"
    assert spec.command_name == "run_script_hello"
    assert set(KNOWN_SCRIPT_KEYS) == set(SCRIPT_REGISTRY)
    assert set(MIXED_SCRIPT_KEYS) == set(SCRIPT_REGISTRY)


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
