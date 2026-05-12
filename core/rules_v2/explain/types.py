"""C11.1 — resolution trace contract (explain layer).

Part of **C11 explainability** (see ``CONTRACT_V2.md`` §23): this module defines
trace **contracts** only; **C11.2 replay** lives in ``replay`` and is opt-in.

Explain contracts are **read-only** descriptions of resolution: they do not
mutate ``RulesSnapshotV2``, ``RulesIndexes``, or any runtime/provider state.

This module intentionally has **no coupling** to ``rules_provider``, accessors,
diagnostics (C6/C7b), or analyzers/reporters.

``ResolutionTraceStep`` uses ``frozen=True``; immutability of the *dataclass
fields* is shallow — nested ``Mapping`` / dict-like values are not deeply
frozen. v1 steps carry **no timestamps** and no wall-clock fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal, Mapping, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Stable string constants (single source for op / phase literals)
# ---------------------------------------------------------------------------

OP_RESOLVE_PARTNER: Final[str] = "rules.resolve_partner"
OP_RESOLVE_LIMIT_RULE: Final[str] = "rules.resolve_limit_rule"
OP_RESOLVE_THRESHOLD_RULE: Final[str] = "rules.resolve_threshold_rule"
OP_GET_JOB_PARAM: Final[str] = "rules.get_job_param"
OP_GET_GROUP_MEMBERSHIPS: Final[str] = "rules.get_group_memberships"
OP_GET_PRIMARY_GROUP: Final[str] = "rules.get_primary_group"
OP_GET_EXCLUSIONS: Final[str] = "rules.get_exclusions"
OP_GET_EXCLUSION: Final[str] = "rules.get_exclusion"

PHASE_INPUT_NORMALIZED: Final[str] = "input_normalized"
PHASE_CANDIDATE_BUILT: Final[str] = "candidate_built"
PHASE_INDEX_LOOKUP: Final[str] = "index_lookup"
PHASE_FALLBACK: Final[str] = "fallback"
PHASE_RESULT: Final[str] = "result"

ResolutionOutcome = Literal["hit", "miss", "skip"]

ResolutionOpName = Literal[
    "rules.resolve_partner",
    "rules.resolve_limit_rule",
    "rules.resolve_threshold_rule",
    "rules.get_job_param",
    "rules.get_group_memberships",
    "rules.get_primary_group",
    "rules.get_exclusions",
    "rules.get_exclusion",
]

ResolutionPhaseName = Literal[
    "input_normalized",
    "candidate_built",
    "index_lookup",
    "fallback",
    "result",
]

JsonPrimitive = str | int | float | bool | None


def _json_primitive(x: Any) -> JsonPrimitive:
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    raise TypeError(f"ResolutionTraceStep: JSON-unsafe value type {type(x)!r}")


@dataclass(frozen=True, slots=True)
class ResolutionTraceStep:
    """One explainability step (v1: no timestamps, no snapshot/index handles)."""

    op: ResolutionOpName
    phase: ResolutionPhaseName
    outcome: ResolutionOutcome
    # TODO(C11.2): optional deep immutability / canonicalization for nested maps
    # (MappingProxyType, sorted candidate keys) if drift-safe replay requires it.
    inputs: Mapping[str, JsonPrimitive]
    candidates: tuple[Mapping[str, str | None], ...]
    index_key: tuple[str | None, ...] | None
    result_ref: str | None


@runtime_checkable
class TraceSink(Protocol):
    """Optional collector for trace steps (explicit instance; no ContextVar in C11.1)."""

    def emit(self, step: ResolutionTraceStep, /) -> None: ...


def trace_step_to_jsonable(step: ResolutionTraceStep) -> dict[str, Any]:
    """Serialize *step* to a plain JSON-compatible dict (tuple ``index_key`` → list)."""

    inputs_out: dict[str, Any] = {str(k): _json_primitive(v) for k, v in step.inputs.items()}
    candidates_out: list[dict[str, Any]] = []
    for row in step.candidates:
        candidates_out.append({str(k): (None if v is None else str(v)) for k, v in row.items()})
    if step.index_key is None:
        index_out = None
    else:
        index_out = [None if x is None else str(x) for x in step.index_key]
    return {
        "op": str(step.op),
        "phase": str(step.phase),
        "outcome": str(step.outcome),
        "inputs": inputs_out,
        "candidates": candidates_out,
        "index_key": index_out,
        "result_ref": None if step.result_ref is None else str(step.result_ref),
    }


def trace_steps_to_jsonable(steps: tuple[ResolutionTraceStep, ...] | list[ResolutionTraceStep]) -> list[dict[str, Any]]:
    """Serialize an ordered sequence of steps."""

    return [trace_step_to_jsonable(s) for s in steps]
