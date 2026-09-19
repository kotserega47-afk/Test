from __future__ import annotations

import logging

import pytest

from core.access_rules import AccessRules
from core.job_runner import JOB_REGISTRY
from modules.antares import handlers
from modules.antares.assembly import AntaresAssemblyError, assemble_antares


def test_none_executor_refuses_without_overwrite():
    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-none")
    JOB_REGISTRY["wallet"] = None
    with pytest.raises(AntaresAssemblyError, match="conflicting"):
        assemble_antares(rules=rules, logger=logger)
    assert JOB_REGISTRY["wallet"] is None
    assert set(JOB_REGISTRY) == {"wallet"}
    assert handlers._rules is None
    assert handlers._logger is None


def test_script_none_and_foreign_callable_refuse_without_mutation():
    from integrations.script_jobs.identity import script_job_type
    from modules.antares import handlers

    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-script")
    key = script_job_type("operator_wallets_ready")

    JOB_REGISTRY.clear()
    JOB_REGISTRY[key] = None
    snapshot = dict(JOB_REGISTRY)
    with pytest.raises(AntaresAssemblyError, match="conflicting"):
        assemble_antares(rules=rules, logger=logger)
    assert dict(JOB_REGISTRY) == snapshot
    assert JOB_REGISTRY[key] is None
    assert handlers._rules is None
    assert handlers._logger is None

    foreign = object()
    JOB_REGISTRY.clear()
    JOB_REGISTRY[key] = foreign
    snapshot = dict(JOB_REGISTRY)
    with pytest.raises(AntaresAssemblyError, match="conflicting"):
        assemble_antares(rules=rules, logger=logger)
    assert dict(JOB_REGISTRY) == snapshot
    assert JOB_REGISTRY[key] is foreign
    assert handlers._rules is None
    assert handlers._logger is None
