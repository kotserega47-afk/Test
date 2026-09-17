"""Dump assembled JOB_REGISTRY keys and get_handlers() after real registration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from core.job_runner import JOB_REGISTRY
from integrations.downloader_wallets import run_wallet_cycle
from integrations.bakai_monitor_playwright import run_rate_monitor_safe
from integrations.tg_commands import get_handlers
from integrations.wallet_editor_registry import run_registry_outbox_replay_job
from integrations.wallet_editor_registry_refresh import run_wallet_editor_registry_refresh_job
from modules.antares.jobs import run_download_job, run_hourly_job
from telegram.ext import CommandHandler, MessageHandler, filters

out = Path(os.environ["REGISTRATION_DUMP_PATH"])

commands: list[str] = []
document_handlers = 0
document_is_all = False
for handler in get_handlers():
    if isinstance(handler, CommandHandler):
        commands.extend(sorted(handler.commands))
    elif isinstance(handler, MessageHandler):
        document_handlers += 1
        document_is_all = handler.filters is filters.Document.ALL

antares_mod = sys.modules.get("modules.antares.jobs")
raccoon_mod = sys.modules.get("integrations.raccoon_jobs")
stub_executors = (
    run_wallet_cycle,
    run_rate_monitor_safe,
    run_wallet_editor_registry_refresh_job,
    run_registry_outbox_replay_job,
)
payload = {
    "keys": sorted(JOB_REGISTRY.keys()),
    "commands": commands,
    "document_handlers": document_handlers,
    "document_filter_is_all": document_is_all,
    "raccoon_jobs_file": getattr(raccoon_mod, "__file__", None),
    "antares_jobs_file": getattr(antares_mod, "__file__", None),
    "hourly_is_run_hourly_job": JOB_REGISTRY.get("hourly") is run_hourly_job,
    "download_is_run_download_job": JOB_REGISTRY.get("download") is run_download_job,
    "wallet_is_run_wallet_cycle": JOB_REGISTRY.get("wallet") is run_wallet_cycle,
    "rate_is_run_rate_monitor_safe": JOB_REGISTRY.get("rate") is run_rate_monitor_safe,
    "refresh_is_lifecycle_job": JOB_REGISTRY.get("wallet_editor_registry_refresh")
    is run_wallet_editor_registry_refresh_job,
    "replay_is_outbox_job": JOB_REGISTRY.get("wallet_editor_registry_replay")
    is run_registry_outbox_replay_job,
    "stub_executors_pairwise_distinct": len({id(fn) for fn in stub_executors})
    == len(stub_executors),
}
out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print("registration_dump_ok")
