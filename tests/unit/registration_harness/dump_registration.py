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
from modules.antares import handlers as antares_handlers
from integrations import tg_commands as tg_commands_mod
from telegram.ext import CommandHandler, MessageHandler, filters

out = Path(os.environ["REGISTRATION_DUMP_PATH"])

commands: list[str] = []
document_handlers = 0
document_is_all = False
callback_by_command: dict[str, object] = {}
for handler in get_handlers():
    if isinstance(handler, CommandHandler):
        commands.extend(sorted(handler.commands))
        for name in handler.commands:
            callback_by_command[name] = handler.callback
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
    "run_wallet_callback_is_antares": callback_by_command.get("run_wallet") is antares_handlers.cmd_run_wallet,
    "run_hourly_callback_is_antares": callback_by_command.get("run_hourly") is antares_handlers.cmd_run_hourly,
    "run_download_callback_is_antares": callback_by_command.get("run_download")
    is antares_handlers.cmd_run_download,
    "run_rate_callback_is_antares": callback_by_command.get("run_rate") is antares_handlers.cmd_run_rate,
    "run_wallet_reexport_is_antares": tg_commands_mod.cmd_run_wallet is antares_handlers.cmd_run_wallet,
    "run_hourly_reexport_is_antares": tg_commands_mod.cmd_run_hourly is antares_handlers.cmd_run_hourly,
    "run_download_reexport_is_antares": tg_commands_mod.cmd_run_download is antares_handlers.cmd_run_download,
    "run_rate_reexport_is_antares": tg_commands_mod.cmd_run_rate is antares_handlers.cmd_run_rate,
    "operator_wallets_ready_callback_is_antares": callback_by_command.get("operator_wallets_ready")
    is antares_handlers.cmd_operator_wallets_ready,
    "wallet_editor_refresh_callback_is_antares": callback_by_command.get("wallet_editor_refresh")
    is antares_handlers.cmd_wallet_editor_refresh,
    "operator_wallets_ready_reexport_is_antares": tg_commands_mod.cmd_operator_wallets_ready
    is antares_handlers.cmd_operator_wallets_ready,
    "wallet_editor_refresh_reexport_is_antares": tg_commands_mod.cmd_wallet_editor_refresh
    is antares_handlers.cmd_wallet_editor_refresh,
    "registry_health_callback_is_antares": callback_by_command.get("registry_health")
    is antares_handlers.cmd_registry_health,
    "registry_replay_callback_is_antares": callback_by_command.get("registry_replay")
    is antares_handlers.cmd_registry_replay,
    "registry_health_reexport_is_antares": tg_commands_mod.cmd_registry_health
    is antares_handlers.cmd_registry_health,
    "registry_replay_reexport_is_antares": tg_commands_mod.cmd_registry_replay
    is antares_handlers.cmd_registry_replay,
    "registry_export_callback_is_antares": callback_by_command.get("registry_export")
    is antares_handlers.cmd_registry_export,
    "registry_export_reexport_is_antares": tg_commands_mod.cmd_registry_export
    is antares_handlers.cmd_registry_export,
    "run_raccoon_callback_is_tg": callback_by_command.get("run_raccoon") is tg_commands_mod.cmd_run_raccoon,
    "antares_run_callbacks_pairwise_distinct": len(
        {
            id(antares_handlers.cmd_run_wallet),
            id(antares_handlers.cmd_run_hourly),
            id(antares_handlers.cmd_run_download),
            id(antares_handlers.cmd_run_rate),
            id(antares_handlers.cmd_operator_wallets_ready),
            id(antares_handlers.cmd_wallet_editor_refresh),
            id(antares_handlers.cmd_registry_health),
            id(antares_handlers.cmd_registry_replay),
            id(antares_handlers.cmd_registry_export),
        }
    )
    == 9,
}
out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print("registration_dump_ok")
