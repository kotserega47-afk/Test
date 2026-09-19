from __future__ import annotations

import logging

import pytest

from core.access_rules import AccessRules
from modules.antares import handlers
from modules.antares.assembly import AntaresAssemblyError, assemble_antares


def test_incompatible_bound_rules_refuse_without_rebind():
    first = AccessRules("first")
    second = AccessRules("second")
    logger = logging.getLogger("antares-assembly-bind")
    handlers.bind_rules(first)
    handlers.bind_logger(logger)
    with pytest.raises(AntaresAssemblyError, match="different AccessRules"):
        assemble_antares(rules=second, logger=logger)
    assert handlers._rules is first
    assert handlers._logger is logger


def test_foreign_registry_mapping_refused():
    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-mapping")
    with pytest.raises(AntaresAssemblyError, match="JOB_REGISTRY"):
        assemble_antares(rules=rules, logger=logger, registry={})
