"""Script jobs — explicit whitelist registry (no dynamic loading)."""

from __future__ import annotations

from integrations.script_jobs.identity import SCRIPT_JOB_PREFIX, parse_script_job_type, script_job_type
from integrations.script_jobs.scripts.operator_wallets_ready import run_operator_wallets_ready
from integrations.script_jobs.types import ScriptExecutionContext, ScriptResult, ScriptSpec

__all__ = (
    "SCRIPT_JOB_PREFIX",
    "SCRIPT_REGISTRY",
    "parse_script_job_type",
    "script_job_type",
)


def _hello_world_run(context: ScriptExecutionContext) -> ScriptResult:
  return ScriptResult(
    status="ok",
    text=f"hello_world ok (source={context.source})",
    metadata={"script_key": context.script_key},
  )


SCRIPT_REGISTRY: dict[str, ScriptSpec] = {
  "hello_world": ScriptSpec(
    script_key="hello_world",
    command_name="run_script_hello",
    run=_hello_world_run,
  ),
  "operator_wallets_ready": ScriptSpec(
    script_key="operator_wallets_ready",
    command_name="operator_wallets_ready",
    run=run_operator_wallets_ready,
  ),
}
