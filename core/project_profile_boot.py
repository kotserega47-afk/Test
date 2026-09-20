"""Process-level profile decision for the legacy scheduler entry.

The parser in ``project_profile`` stays pure. This module reads env and
decides whether ``scheduler.py`` may continue into mixed JOB_REGISTRY imports.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Final

from core.project_profile import InvalidProjectProfileError, parse_project_profile

LEGACY_MIXED: Final = "legacy_mixed"
ANTARES_ISOLATED: Final = "antares"
_EXIT_CODE: Final = 2


class UnwiredProjectProfileError(RuntimeError):
    """Explicit profile requested, but no isolated entry exists yet."""


class IsolatedAntaresProfileError(RuntimeError):
    """Isolated Antares entry refused this PROJECT_PROFILE value."""


def project_profile_env_value(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    env = os.environ if environ is None else environ
    if "PROJECT_PROFILE" not in env:
        return None
    return env["PROJECT_PROFILE"]


def decide_legacy_scheduler_boot(value: str | None) -> str:
    """Return ``legacy_mixed`` or raise. Does not import jobs or Telegram."""
    selection = parse_project_profile(value)
    if selection.implicit_default:
        return LEGACY_MIXED
    raise UnwiredProjectProfileError(
        f"PROJECT_PROFILE={selection.name!r} is not wired; "
        "refusing mixed JOB_REGISTRY. "
        "Unset PROJECT_PROFILE to use the documented legacy mixed scheduler."
    )


def enforce_legacy_scheduler_profile(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Gate for ``scheduler.py``. SystemExit on reject; parser never exits."""
    raw = project_profile_env_value(environ)
    try:
        return decide_legacy_scheduler_boot(raw)
    except (InvalidProjectProfileError, UnwiredProjectProfileError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(_EXIT_CODE) from exc


def decide_antares_isolated_boot(value: str | None) -> str:
    """Return ``antares`` only for an explicit parser match. Does not import jobs."""
    selection = parse_project_profile(value)
    if not selection.implicit_default and selection.name == "antares":
        return ANTARES_ISOLATED
    if selection.implicit_default:
        raise IsolatedAntaresProfileError(
            "PROJECT_PROFILE is unset or empty; "
            "isolated Antares entry requires PROJECT_PROFILE=antares."
        )
    raise IsolatedAntaresProfileError(
        f"PROJECT_PROFILE={selection.name!r} is not allowed "
        "for isolated Antares entry."
    )


def enforce_antares_isolated_profile(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Gate for ``apps.antares``. SystemExit on reject; parser never exits."""
    raw = project_profile_env_value(environ)
    try:
        return decide_antares_isolated_boot(raw)
    except (InvalidProjectProfileError, IsolatedAntaresProfileError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(_EXIT_CODE) from exc
