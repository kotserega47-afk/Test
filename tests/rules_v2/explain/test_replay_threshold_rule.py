"""Parity tests: ``explain_resolve_threshold_rule`` vs ``BaseRulesAccessor.resolve_threshold_rule``."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.rules_v2.accessors import BaseRulesAccessor
from core.rules_v2.explain.replay import (
    explain_resolve_threshold_rule,
    threshold_rule_from_explain_result,
)
from core.rules_v2.explain.types import PHASE_INDEX_LOOKUP, PHASE_RESULT, trace_steps_to_jsonable
from core.rules_v2.indexes import build_indexes
from core.rules_v2.models import MetaInfo, PartnerDef, RulesSnapshotV2, ThresholdRule


def _snap(
    *,
    partners: dict[str, PartnerDef],
    thresholds: list[ThresholdRule],
) -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="explain_threshold_test",
        updated_at=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
        updated_by="test",
    )
    return RulesSnapshotV2(meta=meta, partners=partners, threshold_rules=thresholds)


def _assert_threshold_parity(
    snap: RulesSnapshotV2,
    job_key: str,
    metric_key: str,
    *,
    partner_key: str | None = None,
    group_key: str | None = None,
) -> None:
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    steps = explain_resolve_threshold_rule(
        snap,
        idx,
        job_key,
        metric_key,
        partner_key=partner_key,
        group_key=group_key,
    )
    assert steps[-1].phase == PHASE_RESULT
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)
    exp = acc.resolve_threshold_rule(
        job_key,
        metric_key,
        partner_key=partner_key,
        group_key=group_key,
    )
    got = threshold_rule_from_explain_result(snap, steps)
    assert (exp.rule_key if exp else None) == (got.rule_key if got else None)


def _thr(
    rule_key: str,
    *,
    job_key: str,
    scope_type: str,
    scope_key: str,
    metric_key: str,
) -> ThresholdRule:
    return ThresholdRule(
        rule_key=rule_key,
        job_key=job_key,
        scope_type=scope_type,
        scope_key=scope_key,
        metric_key=metric_key,
        threshold_min=0.0,
        threshold_max=None,
        enabled=True,
    )


def test_partner_hit_before_global() -> None:
    pk = "pk1"
    partners = {
        pk: PartnerDef(
            partner_key=pk,
            partner_code="1",
            source_name=None,
            display_name="P",
            enabled=True,
        ),
    }
    thresholds = [
        _thr("T_partner", job_key="wallet", scope_type="partner", scope_key=pk, metric_key="m"),
        _thr("T_global", job_key="wallet", scope_type="global", scope_key="*", metric_key="m"),
    ]
    _assert_threshold_parity(_snap(partners=partners, thresholds=thresholds), "wallet", "m", partner_key=pk)


def test_partner_miss_group_hit_global_not_consulted() -> None:
    pk = "pk1"
    gk = "grp1"
    partners = {
        pk: PartnerDef(
            partner_key=pk,
            partner_code="1",
            source_name=None,
            display_name="P",
            enabled=True,
        ),
    }
    thresholds = [
        _thr("T_group", job_key="wallet", scope_type="group", scope_key=gk, metric_key="m"),
        _thr("T_global", job_key="wallet", scope_type="global", scope_key="*", metric_key="m"),
    ]
    snap = _snap(partners=partners, thresholds=thresholds)
    idx = build_indexes(snap)
    steps = explain_resolve_threshold_rule(snap, idx, "wallet", "m", partner_key=pk, group_key=gk)

    lookups = [s for s in steps if s.phase == PHASE_INDEX_LOOKUP]
    hits = [s for s in lookups if s.outcome == "hit"]
    assert len(hits) == 1
    assert hits[0].inputs.get("rule_key") == "T_group"
    assert hits[0].candidates[0]["scope_type"] == "group"

    assert lookups[0].outcome == "miss"
    assert lookups[0].candidates[0]["scope_type"] == "partner"

    global_lookups = [s for s in lookups if s.index_key is not None and s.index_key[1] == "global"]
    assert global_lookups == []

    _assert_threshold_parity(snap, "wallet", "m", partner_key=pk, group_key=gk)


def test_only_global_hit() -> None:
    thresholds = [_thr("T_g", job_key="wallet", scope_type="global", scope_key="*", metric_key="x")]
    _assert_threshold_parity(_snap(partners={}, thresholds=thresholds), "wallet", "x")


def test_complete_miss() -> None:
    thresholds = [_thr("T_g", job_key="wallet", scope_type="global", scope_key="*", metric_key="other")]
    snap = _snap(partners={}, thresholds=thresholds)
    idx = build_indexes(snap)
    steps = explain_resolve_threshold_rule(snap, idx, "wallet", "missing_metric")
    assert steps[-1].phase == PHASE_RESULT
    assert steps[-1].outcome == "miss"
    assert steps[-1].result_ref is None
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)

    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    assert acc.resolve_threshold_rule("wallet", "missing_metric") is None
    assert threshold_rule_from_explain_result(snap, steps) is None
