"""C11.2 — explicit read-only replay for resolver paths (no accessors / no I/O).

**Architecture (non-runtime)**

Replay is **explicit opt-in**: nothing in production resolve paths automatically
enables it; callers use these APIs only when a step-by-step trace is needed.
Replay is **not** runtime instrumentation (no always-on tracing layer yet).

The implementation reuses shared **normalization** helpers aligned with
accessors; that import chain can transitively load **pandas** (e.g. via
``core.datetime_utils``). That is acceptable because replay is **outside** the
runtime hot path and does not alter resolve semantics—it only mirrors them as
read-only steps.

**Invariant — ordering / semantics**

Replay here must mirror ``BaseRulesAccessor`` resolution **exactly** (see
``accessors.py``): same scope-candidate order, same ``seen`` de-duplication for
limit ``(method_key, None)``, same index keys, same enabled checks. Any change
to accessor precedence or lookup rules **must** be reflected in this module and
validated by parity tests under ``tests/rules_v2/explain/``.

**Note:** JSON-serialisability of steps is enforced in tests (not inside
``explain_*`` return paths) so this module stays free of extra work on each call
and is not tied to production runtime execution.
"""

from __future__ import annotations

from typing import Any

from core.rules_v2.explain.types import (
    OP_RESOLVE_LIMIT_RULE,
    OP_RESOLVE_PARTNER,
    PHASE_CANDIDATE_BUILT,
    PHASE_FALLBACK,
    PHASE_INDEX_LOOKUP,
    PHASE_INPUT_NORMALIZED,
    PHASE_RESULT,
    ResolutionTraceStep,
)
from core.rules_v2.indexes import RulesIndexes
from core.rules_v2.models import LimitRule, PartnerDef, RulesSnapshotV2
from core.rules_v2.normalizers import extract_partner_code, normalize_key


def _norm_str(value: Any) -> str:
    return str(value or "").strip()


def _norm_method_key(value: Any) -> str | None:
    raw = str(value).strip().lower() if value is not None else ""
    return raw or None


def _build_scope_candidates(
    *,
    partner_key: str | None,
    group_key: str | None,
) -> list[tuple[str, str]]:
    """Mirror ``BaseRulesAccessor._build_scope_candidates``."""

    candidates: list[tuple[str, str]] = []
    if partner_key:
        candidates.append(("partner", str(partner_key)))
    if group_key:
        candidates.append(("group", str(group_key)))
    candidates.append(("global", "*"))
    return candidates


def explain_resolve_partner(
    snapshot: RulesSnapshotV2,
    indexes: RulesIndexes,
    raw_partner: str | None,
    *,
    expect_raw_excel_partner_label: bool = False,
) -> tuple[ResolutionTraceStep, ...]:
    """Replay ``BaseRulesAccessor.resolve_partner`` as ordered ``ResolutionTraceStep`` s.

    Runtime result is not returned; the final ``PHASE_RESULT`` step encodes hit/miss
    via ``outcome`` and ``result_ref`` (``partner_key`` when hit, else ``None``).
    """

    steps: list[ResolutionTraceStep] = []

    def _finish(outcome: str, ref: str | None) -> tuple[ResolutionTraceStep, ...]:
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_PARTNER,
                phase=PHASE_RESULT,
                outcome=outcome,  # type: ignore[arg-type]
                inputs={},
                candidates=(),
                index_key=None,
                result_ref=ref,
            )
        )
        return tuple(steps)

    if not raw_partner:
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_PARTNER,
                phase=PHASE_INPUT_NORMALIZED,
                outcome="miss",
                inputs={"reason": "empty_raw"},
                candidates=(),
                index_key=None,
                result_ref=None,
            )
        )
        return _finish("miss", None)

    raw = str(raw_partner).strip()
    if not raw:
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_PARTNER,
                phase=PHASE_INPUT_NORMALIZED,
                outcome="miss",
                inputs={"reason": "blank_after_strip", "raw_repr": str(raw_partner)[:200]},
                candidates=(),
                index_key=None,
                result_ref=None,
            )
        )
        return _finish("miss", None)

    steps.append(
        ResolutionTraceStep(
            op=OP_RESOLVE_PARTNER,
            phase=PHASE_INPUT_NORMALIZED,
            outcome="hit",
            inputs={
                "raw_repr": str(raw_partner)[:200],
                "stripped": raw,
                "expect_raw_excel_partner_label": bool(expect_raw_excel_partner_label),
                "label_has_open_paren": "(" in raw,
            },
            candidates=(),
            index_key=None,
            result_ref=None,
        )
    )

    partner_code = extract_partner_code(raw)
    if partner_code and partner_code in indexes.partners_by_code:
        partner_key = indexes.partners_by_code[partner_code]
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_PARTNER,
                phase=PHASE_INDEX_LOOKUP,
                outcome="hit",
                inputs={"via": "partners_by_code", "partner_code": partner_code},
                candidates=({"mapped_partner_key": partner_key},),
                index_key=(partner_code,),
                result_ref=None,
            )
        )
        partner = snapshot.partners.get(partner_key)
        if partner and partner.enabled:
            steps.append(
                ResolutionTraceStep(
                    op=OP_RESOLVE_PARTNER,
                    phase=PHASE_CANDIDATE_BUILT,
                    outcome="hit",
                    inputs={"partner_key": partner_key, "via": "snapshot_partners_enabled"},
                    candidates=(),
                    index_key=None,
                    result_ref=None,
                )
            )
            return _finish("hit", partner_key)
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_PARTNER,
                phase=PHASE_CANDIDATE_BUILT,
                outcome="miss",
                inputs={
                    "partner_key": partner_key,
                    "via": "snapshot_partners",
                    "reason": "missing_or_disabled",
                },
                candidates=(),
                index_key=None,
                result_ref=None,
            )
        )

    partner_key = normalize_key(raw)
    steps.append(
        ResolutionTraceStep(
            op=OP_RESOLVE_PARTNER,
            phase=PHASE_FALLBACK,
            outcome="hit",
            inputs={"via": "normalize_key", "normalized_key": partner_key},
            candidates=(),
            index_key=None,
            result_ref=None,
        )
    )
    if partner_key in indexes.partners_by_key:
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_PARTNER,
                phase=PHASE_INDEX_LOOKUP,
                outcome="hit",
                inputs={"via": "partners_by_key"},
                candidates=(),
                index_key=(partner_key,),
                result_ref=None,
            )
        )
        partner = snapshot.partners.get(partner_key)
        if partner and partner.enabled:
            steps.append(
                ResolutionTraceStep(
                    op=OP_RESOLVE_PARTNER,
                    phase=PHASE_CANDIDATE_BUILT,
                    outcome="hit",
                    inputs={"partner_key": partner_key, "via": "snapshot_partners_enabled"},
                    candidates=(),
                    index_key=None,
                    result_ref=None,
                )
            )
            return _finish("hit", partner_key)
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_PARTNER,
                phase=PHASE_CANDIDATE_BUILT,
                outcome="miss",
                inputs={
                    "partner_key": partner_key,
                    "via": "snapshot_partners",
                    "reason": "missing_or_disabled",
                },
                candidates=(),
                index_key=None,
                result_ref=None,
            )
        )
    else:
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_PARTNER,
                phase=PHASE_INDEX_LOOKUP,
                outcome="miss",
                inputs={"via": "partners_by_key"},
                candidates=(),
                index_key=(partner_key,),
                result_ref=None,
            )
        )

    return _finish("miss", None)


def explain_resolve_limit_rule(
    snapshot: RulesSnapshotV2,
    indexes: RulesIndexes,
    job_key: str,
    metric_key: str,
    *,
    partner_key: str | None = None,
    group_key: str | None = None,
    method_key: str | None = None,
) -> tuple[ResolutionTraceStep, ...]:
    """Replay ``BaseRulesAccessor.resolve_limit_rule`` as ordered steps.

    Final ``PHASE_RESULT`` uses ``result_ref = rule.rule_key`` on hit (model has no ``id``).
    """

    steps: list[ResolutionTraceStep] = []

    def _finish(outcome: str, ref: str | None) -> tuple[ResolutionTraceStep, ...]:
        steps.append(
            ResolutionTraceStep(
                op=OP_RESOLVE_LIMIT_RULE,
                phase=PHASE_RESULT,
                outcome=outcome,  # type: ignore[arg-type]
                inputs={},
                candidates=(),
                index_key=None,
                result_ref=ref,
            )
        )
        return tuple(steps)

    job_k = _norm_str(job_key)
    metric_k = _norm_str(metric_key)
    method_k = _norm_method_key(method_key)

    steps.append(
        ResolutionTraceStep(
            op=OP_RESOLVE_LIMIT_RULE,
            phase=PHASE_INPUT_NORMALIZED,
            outcome="hit",
            inputs={
                "job_key": job_k,
                "metric_key": metric_k,
                "partner_key": partner_key or "",
                "group_key": group_key or "",
                "method_key": "" if method_k is None else method_k,
            },
            candidates=tuple(
                {"scope_type": st, "scope_key": sk}
                for st, sk in _build_scope_candidates(partner_key=partner_key, group_key=group_key)
            ),
            index_key=None,
            result_ref=None,
        )
    )

    seen: set[tuple[str, str, str, str, str | None]] = set()

    for scope_type, scope_key in _build_scope_candidates(partner_key=partner_key, group_key=group_key):
        for candidate_method in (method_k, None):
            key = (job_k, scope_type, scope_key, metric_k, candidate_method)
            if key in seen:
                continue
            seen.add(key)

            rule = indexes.limit_rules_index.get(key)
            if rule and rule.enabled:
                steps.append(
                    ResolutionTraceStep(
                        op=OP_RESOLVE_LIMIT_RULE,
                        phase=PHASE_INDEX_LOOKUP,
                        outcome="hit",
                        inputs={"rule_key": rule.rule_key},
                        candidates=({"scope_type": scope_type, "scope_key": scope_key},),
                        index_key=tuple(key),
                        result_ref=None,
                    )
                )
                return _finish("hit", rule.rule_key)
            steps.append(
                ResolutionTraceStep(
                    op=OP_RESOLVE_LIMIT_RULE,
                    phase=PHASE_INDEX_LOOKUP,
                    outcome="miss",
                    inputs={
                        "reason": "no_rule_or_disabled" if rule and not rule.enabled else "no_rule",
                    },
                    candidates=({"scope_type": scope_type, "scope_key": scope_key},),
                    index_key=tuple(key),
                    result_ref=None,
                )
            )

    return _finish("miss", None)


def partner_def_from_explain_result(
    snapshot: RulesSnapshotV2,
    steps: tuple[ResolutionTraceStep, ...],
) -> PartnerDef | None:
    """Derive accessor-equivalent partner from the final ``PHASE_RESULT`` step."""

    for s in reversed(steps):
        if s.phase == PHASE_RESULT and s.outcome == "hit" and s.result_ref:
            p = snapshot.partners.get(str(s.result_ref))
            return p if p and p.enabled else None
    return None


def limit_rule_from_explain_result(
    snapshot: RulesSnapshotV2,
    steps: tuple[ResolutionTraceStep, ...],
) -> LimitRule | None:
    """Derive accessor-equivalent limit rule from the final ``PHASE_RESULT`` step."""

    for s in reversed(steps):
        if s.phase == PHASE_RESULT and s.outcome == "hit" and s.result_ref:
            rk = str(s.result_ref)
            for r in snapshot.limit_rules:
                if r.rule_key == rk and r.enabled:
                    return r
    return None
