"""Script job type identity. Import does not register JOB_REGISTRY entries."""

from __future__ import annotations

from typing import Final

SCRIPT_JOB_PREFIX: Final[str] = "script_job:"


def script_job_type(script_key: str) -> str:
    """Canonical scheduler / lock / JOB_REGISTRY identity for a whitelisted script."""
    return f"{SCRIPT_JOB_PREFIX}{script_key}"


def parse_script_job_type(job_type: str) -> str | None:
    if not job_type.startswith(SCRIPT_JOB_PREFIX):
        return None
    key = job_type[len(SCRIPT_JOB_PREFIX) :].strip()
    return key or None
