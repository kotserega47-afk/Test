"""Dump assembled JOB_REGISTRY keys and get_handlers() after real registration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from core.job_runner import JOB_REGISTRY
from integrations.tg_commands import get_handlers
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

raccoon_mod = sys.modules.get("integrations.raccoon_jobs")
payload = {
    "keys": sorted(JOB_REGISTRY.keys()),
    "commands": commands,
    "document_handlers": document_handlers,
    "document_filter_is_all": document_is_all,
    "raccoon_jobs_file": getattr(raccoon_mod, "__file__", None),
}
out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print("registration_dump_ok")
