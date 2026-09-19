from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from telegram.ext import CommandHandler, MessageHandler, filters

from core.access_rules import AccessRules
from core.job_runner import JOB_REGISTRY
from modules.antares.assembly import ANTARES_ASSEMBLY_JOB_TYPES, assemble_antares
from modules.antares.document_ingest import handle_wallet_editor_document
from modules.antares.jobs import antares_job_executors
from modules.antares import handlers

_FORBIDDEN = (
    "integrations.tg_commands",
    "integrations.raccoon_jobs",
    "integrations.raccoon_wallet_downloader",
    "integrations.raccoon_hourly_downloader",
)
_FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "behavior_baseline"


def test_assemble_binds_real_registry_and_handlers():
    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-test")
    result = assemble_antares(rules=rules, logger=logger)

    assert result.rules is rules
    assert result.logger is logger
    expected_cmds = json.loads((_FIXTURE / "expected_antares_tg_commands.json").read_text(encoding="utf-8"))
    commands = []
    documents = []
    for handler in result.handlers:
        if isinstance(handler, CommandHandler):
            commands.extend(sorted(handler.commands))
        elif isinstance(handler, MessageHandler):
            documents.append(handler)
    assert commands == expected_cmds["commands"]
    assert len(documents) == 1
    assert documents[0].filters == filters.Document.ALL
    assert documents[0].callback is handle_wallet_editor_document

    assert set(JOB_REGISTRY) == ANTARES_ASSEMBLY_JOB_TYPES
    expected_exec = antares_job_executors()
    for key, executor in expected_exec.items():
        assert JOB_REGISTRY[key] is executor
    assert "script_job:hello_world" not in JOB_REGISTRY
    import core.job_runner as job_runner_mod

    assert job_runner_mod.request_job.__globals__["JOB_REGISTRY"] is job_runner_mod.JOB_REGISTRY
    assert job_runner_mod.JOB_REGISTRY is JOB_REGISTRY

    script_type = "script_job:operator_wallets_ready"
    assert script_type in JOB_REGISTRY
    first_executors = {key: JOB_REGISTRY[key] for key in ANTARES_ASSEMBLY_JOB_TYPES}
    assert set(first_executors) == ANTARES_ASSEMBLY_JOB_TYPES

    again = assemble_antares(rules=rules, logger=logger)
    assert set(JOB_REGISTRY) == ANTARES_ASSEMBLY_JOB_TYPES
    for key, executor in first_executors.items():
        assert JOB_REGISTRY[key] is executor
    for key, executor in expected_exec.items():
        assert JOB_REGISTRY[key] is executor
    assert again.handlers
    assert handlers._rules is rules
    assert handlers._logger is logger

    loaded = [name for name in _FORBIDDEN if name in sys.modules]
    assert loaded == []
    for name in _FORBIDDEN:
        assert name not in sys.modules
