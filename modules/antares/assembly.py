"""Isolated Antares handler/job assembly. Does not start polling or jobs."""

from __future__ import annotations

from dataclasses import dataclass

from core.job_runner import JOB_REGISTRY
from integrations.script_jobs.bind import register_script_job
from integrations.script_jobs.identity import script_job_type
from modules.antares import handlers
from modules.antares.jobs import ANTARES_JOB_KEYS, antares_job_executors, register_jobs

ANTARES_ASSEMBLY_JOB_TYPES = frozenset(ANTARES_JOB_KEYS | {"script_job:operator_wallets_ready"})

OPERATOR_SCRIPT_KEY = "operator_wallets_ready"


class AntaresAssemblyError(RuntimeError):
    """Raised when isolated Antares assembly is refused or fails."""


@dataclass(frozen=True)
class AntaresAssembly:
    rules: object
    logger: object
    handlers: list


def _refuse(message: str) -> None:
    raise AntaresAssemblyError(message)


def _check_registry_identity(registry: object) -> None:
    if registry is not JOB_REGISTRY:
        _refuse("assembly registry must be core.job_runner.JOB_REGISTRY")


def _check_bind_compatible(rules: object, logger: object) -> None:
    if rules is None:
        _refuse("assembly requires AccessRules")
    if logger is None:
        _refuse("assembly requires a logger")
    bound_rules = handlers._rules
    bound_logger = handlers._logger
    if bound_rules is not None and bound_rules is not rules:
        _refuse("antares handlers already bound to a different AccessRules instance")
    if bound_logger is not None and bound_logger is not logger:
        _refuse("antares handlers already bound to a different logger")


def _check_job_registry_preconditions() -> None:
    extra = set(JOB_REGISTRY) - ANTARES_ASSEMBLY_JOB_TYPES
    if extra:
        _refuse(f"JOB_REGISTRY has foreign keys: {sorted(extra)}")

    expected = antares_job_executors()
    for key, executor in expected.items():
        if key not in JOB_REGISTRY:
            continue
        existing = JOB_REGISTRY[key]
        if existing is not executor:
            _refuse(f"conflicting JOB_REGISTRY entry for {key}: refusing overwrite")

    job_type = script_job_type(OPERATOR_SCRIPT_KEY)
    if job_type not in JOB_REGISTRY:
        return
    from integrations.script_jobs import bind as script_bind

    identity = script_bind._EXECUTORS.get(OPERATOR_SCRIPT_KEY)
    existing = JOB_REGISTRY[job_type]
    if identity is None or existing is not identity:
        _refuse(f"conflicting JOB_REGISTRY entry for {job_type}: refusing overwrite")


def assemble_antares(*, rules: object, logger: object, registry=JOB_REGISTRY) -> AntaresAssembly:
    """Bind Antares jobs/handlers onto the process JOB_REGISTRY. Does not run jobs."""
    _check_registry_identity(registry)
    _check_bind_compatible(rules, logger)
    _check_job_registry_preconditions()

    handlers.bind_rules(rules)
    handlers.bind_logger(logger)

    try:
        register_jobs(JOB_REGISTRY)
        register_script_job(OPERATOR_SCRIPT_KEY)
    except AntaresAssemblyError:
        raise
    except Exception as exc:
        raise AntaresAssemblyError("assembly register failed") from exc

    assembled_handlers = handlers.get_antares_handlers()
    return AntaresAssembly(rules=rules, logger=logger, handlers=assembled_handlers)
