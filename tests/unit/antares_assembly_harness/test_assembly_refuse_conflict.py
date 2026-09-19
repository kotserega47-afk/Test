from __future__ import annotations

import logging

import pytest

from core.access_rules import AccessRules
from core.job_runner import JOB_REGISTRY
from modules.antares.assembly import AntaresAssemblyError, assemble_antares


def test_none_executor_refuses_without_overwrite():
    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-none")
    JOB_REGISTRY["wallet"] = None
    with pytest.raises(AntaresAssemblyError, match="conflicting"):
        assemble_antares(rules=rules, logger=logger)
    assert JOB_REGISTRY["wallet"] is None
    assert set(JOB_REGISTRY) == {"wallet"}
