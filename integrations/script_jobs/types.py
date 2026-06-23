"""Script jobs framework — shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from core.job_runner import Actor

ScriptStatus = Literal["ok", "failed", "skipped"]
ScriptSource = Literal["manual", "scheduled", "cli", "unknown"]


@dataclass(frozen=True, slots=True)
class ScriptResult:
  status: ScriptStatus
  text: str = ""
  files: tuple[str, ...] = ()
  metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScriptExecutionContext:
  actor: Actor
  script_key: str
  job_type: str
  source: ScriptSource
  chat_id: int | None = None
  route_key: str | None = None


@dataclass(frozen=True, slots=True)
class ScriptSpec:
  script_key: str
  command_name: str | None
  run: Callable[[ScriptExecutionContext], ScriptResult]
