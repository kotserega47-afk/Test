"""Selective JOB_REGISTRY bind for known script jobs. Import does not register."""

from __future__ import annotations

from collections.abc import Callable

from core.job_runner import Actor, JOB_REGISTRY
from integrations.script_jobs.identity import script_job_type

KNOWN_SCRIPT_KEYS: frozenset[str] = frozenset(
    {
        "hello_world",
        "operator_wallets_ready",
    }
)

_EXECUTORS: dict[str, Callable[[Actor], None]] = {}


class ScriptJobBindError(RuntimeError):
    """Raised when selective script registration is refused."""


def _make_executor(script_key: str) -> Callable[[Actor], None]:
    def _entry(actor: Actor) -> None:
        from integrations.script_jobs.runtime import run_script_job

        run_script_job(actor, script_key)

    _entry.__name__ = f"script_job_{script_key}"
    _entry.__qualname__ = f"script_job_{script_key}"
    return _entry


def register_script_job(script_key: str) -> Callable[[Actor], None]:
    """Bind one known script job onto ``core.job_runner.JOB_REGISTRY``.

    Does not import runtime/registry/operator_wallets_ready until the callable runs.
    Refuses an existing JOB_REGISTRY key whose value is not this executor,
    including an explicit ``None``.
    """
    key = (script_key or "").strip()
    if key not in KNOWN_SCRIPT_KEYS:
        raise ScriptJobBindError(f"unknown script_key: {script_key!r}")

    job_type = script_job_type(key)
    executor = _EXECUTORS.get(key)
    if executor is None:
        executor = _make_executor(key)
        _EXECUTORS[key] = executor

    if job_type in JOB_REGISTRY:
        existing = JOB_REGISTRY[job_type]
        if existing is executor:
            return executor
        raise ScriptJobBindError(
            f"conflicting JOB_REGISTRY entry for {job_type}: refusing overwrite"
        )
    JOB_REGISTRY[job_type] = executor
    return executor
