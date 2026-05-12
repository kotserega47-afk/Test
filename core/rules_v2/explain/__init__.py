"""C11.1 explainability — read-only trace contracts only.

No provider/runtime coupling; see ``types`` module for shallow-immutability and
v1 no-timestamp notes.
"""

from core.rules_v2.explain.types import (
    OP_GET_EXCLUSION,
    OP_GET_EXCLUSIONS,
    OP_GET_GROUP_MEMBERSHIPS,
    OP_GET_JOB_PARAM,
    OP_GET_PRIMARY_GROUP,
    OP_RESOLVE_LIMIT_RULE,
    OP_RESOLVE_PARTNER,
    OP_RESOLVE_THRESHOLD_RULE,
    PHASE_CANDIDATE_BUILT,
    PHASE_FALLBACK,
    PHASE_INDEX_LOOKUP,
    PHASE_INPUT_NORMALIZED,
    PHASE_RESULT,
    JsonPrimitive,
    ResolutionOpName,
    ResolutionOutcome,
    ResolutionPhaseName,
    ResolutionTraceStep,
    TraceSink,
    trace_step_to_jsonable,
    trace_steps_to_jsonable,
)

__all__ = [
    "OP_GET_EXCLUSION",
    "OP_GET_EXCLUSIONS",
    "OP_GET_GROUP_MEMBERSHIPS",
    "OP_GET_JOB_PARAM",
    "OP_GET_PRIMARY_GROUP",
    "OP_RESOLVE_LIMIT_RULE",
    "OP_RESOLVE_PARTNER",
    "OP_RESOLVE_THRESHOLD_RULE",
    "PHASE_CANDIDATE_BUILT",
    "PHASE_FALLBACK",
    "PHASE_INDEX_LOOKUP",
    "PHASE_INPUT_NORMALIZED",
    "PHASE_RESULT",
    "JsonPrimitive",
    "ResolutionOpName",
    "ResolutionOutcome",
    "ResolutionPhaseName",
    "ResolutionTraceStep",
    "TraceSink",
    "trace_step_to_jsonable",
    "trace_steps_to_jsonable",
]
