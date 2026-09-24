"""Isolated Antares process entry. Assembles handlers/jobs and exits. No polling."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.antares_sender_ownership import (
    AntaresSenderOwnershipAttestation,
    claim_antares_sender_ownership,
)
from core.project_profile_boot import enforce_antares_isolated_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _REPO_ROOT / ".env"

_BOOT_OK = "antares boot ok"
_RUN_OK = "antares run ok"


@dataclass(frozen=True, slots=True)
class AntaresBootPrefix:
    """Boot context after profile enforce + ownership claim + assembly."""

    assembled: Any
    rules: Any
    token: str
    sender_ownership: AntaresSenderOwnershipAttestation


def _fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _command(argv: list[str]) -> str:
    if not argv or argv == ["boot"]:
        return "boot"
    if argv == ["run"]:
        return "run"
    _fail("unknown antares argument(s): " + " ".join(argv))


def _local_workbook_path() -> Path:
    """Match ``rules_provider._try_local_workbook_path`` normalization; refuse otherwise."""

    raw = (os.getenv("RULES_XLSX_PATH") or "").strip()
    if not raw:
        _fail("RULES_XLSX_PATH is not set")
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() != ".xlsx":
        _fail(f"RULES_XLSX_PATH must be a .xlsx file: {raw}")
    if not path.is_file():
        _fail(f"RULES_XLSX_PATH is not an existing file: {path}")
    return path


def _handler_counts(assembled) -> tuple[int, int]:
    from telegram.ext import CommandHandler, MessageHandler

    n_cmd = sum(1 for handler in assembled.handlers if isinstance(handler, CommandHandler))
    n_doc = sum(1 for handler in assembled.handlers if isinstance(handler, MessageHandler))
    return n_cmd, n_doc


def _print_boot_ok(assembled) -> None:
    from core.job_runner import JOB_REGISTRY

    n_cmd, n_doc = _handler_counts(assembled)
    job_keys = ",".join(sorted(JOB_REGISTRY))
    print(
        f"{_BOOT_OK} commands={n_cmd} document={n_doc} "
        f"jobs={len(JOB_REGISTRY)} keys={job_keys}"
    )


def _print_run_ok(assembled, snap) -> None:
    n_cmd, n_doc = _handler_counts(assembled)
    print(
        f"{_RUN_OK} local_rules source={snap.source} "
        f"commands={n_cmd} document={n_doc} handlers={len(assembled.handlers)}"
    )


def _boot_prefix() -> AntaresBootPrefix:
    enforce_antares_isolated_profile()
    # Claim before dotenv/assemble/sender import (TASK-45/46).
    sender_ownership = claim_antares_sender_ownership()
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
    return AntaresBootPrefix(
        assembled=assembled,
        rules=rules,
        token=token,
        sender_ownership=sender_ownership,
    )


def _snapshot_local_rules(rules):
    _local_workbook_path()
    try:
        return rules.get_snapshot(force_sync=True)
    except Exception as exc:
        _fail(f"antares local rules failed: {type(exc).__name__}: {exc}")


def _build_and_attach(assembled, token: str) -> None:
    from telegram.ext import Application

    try:
        app = Application.builder().token(token).concurrent_updates(True).build()
    except Exception as exc:
        _fail(f"antares application build failed: {type(exc).__name__}: {exc}")
    try:
        for handler in assembled.handlers:
            app.add_handler(handler)
    except Exception as exc:
        _fail(f"antares handler attach failed: {type(exc).__name__}: {exc}")


def main() -> None:
    mode = _command(sys.argv[1:])
    boot = _boot_prefix()
    if mode == "boot":
        _print_boot_ok(boot.assembled)
        return
    snap = _snapshot_local_rules(boot.rules)
    _build_and_attach(boot.assembled, boot.token)
    _print_run_ok(boot.assembled, snap)


if __name__ == "__main__":
    main()
