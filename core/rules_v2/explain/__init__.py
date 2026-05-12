"""C11 explainability — trace contracts (C11.1) and explicit replay (C11.2).

**C11.1 — trace contracts** live in ``types``: ``ResolutionTraceStep``, op/phase
constants, JSON helpers. No provider coupling.

**C11.2 — explicit replay** lives in ``replay``: optional functions that mirror
``BaseRulesAccessor`` resolution as ordered steps. They are exposed from this
package but **loaded lazily** (see ``__getattr__`` below).

**Invariant — package import surface**

The default import of ``core.rules_v2.explain`` must stay **lightweight**: only
``types`` symbols are eager-imported. **Replay APIs may pull heavier transitive
dependencies** (e.g. normalization → pandas) when first accessed; callers who
need a minimal import graph should import ``core.rules_v2.explain.types``
directly and avoid touching ``explain_resolve_*`` / ``*_from_explain_result``
until necessary.

Lazy replay loading keeps ``import core.rules_v2.explain.types`` from loading
``replay`` (and thus not ``normalizers`` / ``datetime_utils`` / pandas) until
replay symbols are used from the package namespace.
"""

from __future__ import annotations

from typing import Any

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
    "explain_resolve_limit_rule",
    "explain_resolve_partner",
    "limit_rule_from_explain_result",
    "partner_def_from_explain_result",
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


def __getattr__(name: str) -> Any:
    if name in (
        "explain_resolve_limit_rule",
        "explain_resolve_partner",
        "limit_rule_from_explain_result",
        "partner_def_from_explain_result",
    ):
        from core.rules_v2.explain import replay as _replay

        return getattr(_replay, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
