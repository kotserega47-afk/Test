from __future__ import annotations

import logging

import pytest

from core.access_rules import AccessRules
from core.job_runner import JOB_REGISTRY
from modules.antares.assembly import AntaresAssemblyError, assemble_antares


def test_foreign_registry_key_refuses_without_mutation():
    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-foreign")
    JOB_REGISTRY["raccoon_wallet"] = object()
    snapshot = dict(JOB_REGISTRY)
    with pytest.raises(AntaresAssemblyError, match="foreign keys"):
        assemble_antares(rules=rules, logger=logger)
    assert dict(JOB_REGISTRY) == snapshot
    assert "raccoon_wallet" in JOB_REGISTRY
    assert "wallet" not in JOB_REGISTRY or JOB_REGISTRY.get("wallet") is snapshot.get("wallet")
