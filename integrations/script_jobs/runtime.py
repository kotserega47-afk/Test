"""Script jobs — execution runtime."""

from __future__ import annotations

import logging
from typing import Callable

from core.config_manager import get_job_params
from core.job_runner import JOB_REGISTRY, Actor

from integrations.script_jobs.delivery import deliver_script_result
from integrations.script_jobs.registry import SCRIPT_REGISTRY, script_job_type
from integrations.script_jobs.types import ScriptExecutionContext, ScriptResult, ScriptSource

log = logging.getLogger(__name__)


class UnknownScriptError(ValueError):
  """Raised when script_key is not in SCRIPT_REGISTRY."""


def _resolve_source(actor: Actor) -> ScriptSource:
  kind = (actor.kind or "").strip().lower()
  if kind == "tg":
    return "manual"
  if kind == "scheduler":
    return "scheduled"
  if kind == "cli":
    return "cli"
  return "unknown"


def build_execution_context(script_key: str, actor: Actor) -> ScriptExecutionContext:
  job_type = script_job_type(script_key)
  params = get_job_params(job=job_type)

  route_key = (params.get("telegram_route_report") or "").strip() or None
  chat_id: int | None = None
  source = _resolve_source(actor)

  if actor.kind == "tg" and actor.chat_id is not None:
    chat_id = int(actor.chat_id)

  return ScriptExecutionContext(
    actor=actor,
    script_key=script_key,
    job_type=job_type,
    source=source,
    chat_id=chat_id,
    route_key=route_key,
  )


def run_script(script_key: str, context: ScriptExecutionContext) -> ScriptResult:
  """Resolve whitelisted script, execute, deliver result. Fail closed on unknown key."""
  spec = SCRIPT_REGISTRY.get(script_key)
  if spec is None:
    log.warning("[script_jobs] unknown script_key=%s", script_key)
    raise UnknownScriptError(f"unknown script_key: {script_key}")

  if context.script_key != script_key:
    raise ValueError(
      f"context.script_key mismatch: expected={script_key} got={context.script_key}"
    )

  try:
    result = spec.run(context)
  except Exception as exc:
    log.exception("[script_jobs] script failed script_key=%s", script_key)
    result = ScriptResult(
      status="failed",
      text=f"Script failed: {type(exc).__name__}",
      metadata={"error_class": type(exc).__name__},
    )

  deliver_script_result(context, result)
  return result


def run_script_job(actor: Actor, script_key: str) -> None:
  """JOB_REGISTRY entry point for script_job:<key> (via request_job + actor)."""
  context = build_execution_context(script_key, actor)
  result = run_script(script_key, context)
  if result.status == "failed":
    raise RuntimeError(result.text or f"script_job:{script_key} failed")


def _make_job_registry_entry(script_key: str) -> Callable[[Actor], None]:
  """Closure binds script_key; request_job passes actor explicitly (no globals)."""

  def _entry(actor: Actor) -> None:
    run_script_job(actor, script_key)

  return _entry


def register_script_jobs() -> None:
  """Register script_job:<key> handlers into JOB_REGISTRY.

  Lock strategy: each job_type is ``script_job:<script_key>``. job_runner locks by
  job_type, so ``_safe_job_name`` yields distinct files (e.g. script_job_hello_world.lock)
  without changing wallet/hourly/rate/download lock paths.
  """
  for script_key, spec in SCRIPT_REGISTRY.items():
    if spec.script_key != script_key:
      raise ValueError(f"SCRIPT_REGISTRY key mismatch: {script_key} != {spec.script_key}")
    job_type = script_job_type(script_key)
    JOB_REGISTRY[job_type] = _make_job_registry_entry(script_key)
    log.info("[script_jobs] registered job_type=%s command=%s", job_type, spec.command_name)


register_script_jobs()
