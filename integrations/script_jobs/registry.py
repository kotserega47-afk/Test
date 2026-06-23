"""Script jobs — explicit whitelist registry (no dynamic loading)."""

from __future__ import annotations

from typing import Final

from integrations.script_jobs.scripts.operator_wallets_ready import run_operator_wallets_ready
from integrations.script_jobs.types import ScriptExecutionContext, ScriptResult, ScriptSpec

SCRIPT_JOB_PREFIX: Final[str] = "script_job:"


def script_job_type(script_key: str) -> str:
  """Canonical scheduler / lock / JOB_REGISTRY identity for a whitelisted script."""
  return f"{SCRIPT_JOB_PREFIX}{script_key}"


def parse_script_job_type(job_type: str) -> str | None:
  if not job_type.startswith(SCRIPT_JOB_PREFIX):
    return None
  key = job_type[len(SCRIPT_JOB_PREFIX) :].strip()
  return key or None


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
