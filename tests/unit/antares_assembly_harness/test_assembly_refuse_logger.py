from __future__ import annotations

import logging

import pytest

from core.access_rules import AccessRules
from core.job_runner import JOB_REGISTRY
from modules.antares import handlers
from modules.antares.assembly import AntaresAssemblyError, assemble_antares


def test_incompatible_bound_logger_refuse_without_rebind():
    rules = AccessRules("same-rules")
    first_log = logging.getLogger("antares-assembly-log-a")
    second_log = logging.getLogger("antares-assembly-log-b")
    handlers.bind_rules(rules)
    handlers.bind_logger(first_log)
    snapshot = dict(JOB_REGISTRY)
    with pytest.raises(AntaresAssemblyError, match="different logger"):
        assemble_antares(rules=rules, logger=second_log)
    assert handlers._rules is rules
    assert handlers._logger is first_log
    assert dict(JOB_REGISTRY) == snapshot
