"""Isolated Antares process entry. Assembles handlers/jobs and exits. No polling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from core.project_profile_boot import enforce_antares_isolated_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _REPO_ROOT / ".env"


def _fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    enforce_antares_isolated_profile()
    load_dotenv(dotenv_path=_ENV_PATH, override=False)

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        _fail("TELEGRAM_BOT_TOKEN is not set")

    from core.access_rules import AccessRules
    from modules.antares.assembly import AntaresAssemblyError, assemble_antares
    from utils.log_profiles import LOG_PROFILES
    from utils.loggers import get_logger

    rules = AccessRules(os.getenv("RULES_XLSX_PATH", "").strip())
    icon, name = LOG_PROFILES["MAIN"]
    logger = get_logger(name, icon)

    try:
        assembled = assemble_antares(rules=rules, logger=logger)
    except AntaresAssemblyError as exc:
        _fail(f"antares assembly failed: {exc}")
    except Exception as exc:
        _fail(f"antares assembly failed: {type(exc).__name__}: {exc}")

    from telegram.ext import CommandHandler, MessageHandler

    from core.job_runner import JOB_REGISTRY

    n_cmd = sum(1 for handler in assembled.handlers if isinstance(handler, CommandHandler))
    n_doc = sum(1 for handler in assembled.handlers if isinstance(handler, MessageHandler))
    job_keys = ",".join(sorted(JOB_REGISTRY))
    print(
        f"antares boot ok commands={n_cmd} document={n_doc} "
        f"jobs={len(JOB_REGISTRY)} keys={job_keys}"
    )


if __name__ == "__main__":
    main()
