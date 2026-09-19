from __future__ import annotations

import logging

import pytest

from core.access_rules import AccessRules
from core.job_runner import JOB_REGISTRY
from modules.antares.assembly import AntaresAssemblyError, assemble_antares
from modules.antares.jobs import antares_job_executors


def test_register_failure_is_not_success():
    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-fail")

    def _boom(registry):
        registry["wallet"] = object()
        raise RuntimeError("forced import failure")

    import modules.antares.assembly as assembly

    original = assembly.register_jobs
    assembly.register_jobs = _boom
    try:
        with pytest.raises(AntaresAssemblyError, match="register failed"):
            assemble_antares(rules=rules, logger=logger)
        assert assemble_antares.__name__ == "assemble_antares"
        assert JOB_REGISTRY.get("wallet") is not None
        assert set(JOB_REGISTRY) != set(antares_job_executors()) | {"script_job:operator_wallets_ready"}
    finally:
        assembly.register_jobs = original
