"""Parity tests: ``explain_resolve_limit_rule`` vs ``BaseRulesAccessor.resolve_limit_rule``."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.rules_v2.accessors import BaseRulesAccessor
from core.rules_v2.explain.replay import explain_resolve_limit_rule, limit_rule_from_explain_result
from core.rules_v2.explain.types import PHASE_INDEX_LOOKUP, PHASE_RESULT, trace_steps_to_jsonable
from core.rules_v2.indexes import build_indexes
from core.rules_v2.models import LimitRule, MetaInfo, PartnerDef, RulesSnapshotV2


def _snap(
    *,
    partners: dict[str, PartnerDef],
    limits: list[LimitRule],
) -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="explain_limit_test",
        updated_at=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
        updated_by="test",
    )
    return RulesSnapshotV2(meta=meta, partners=partners, limit_rules=limits)


def _assert_limit_parity(
    snap: RulesSnapshotV2,
    job_key: str,
    metric_key: str,
    *,
    partner_key: str | None = None,
    group_key: str | None = None,
    method_key: str | None = None,
) -> None:
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    steps = explain_resolve_limit_rule(
        snap,
        idx,
        job_key,
        metric_key,
        partner_key=partner_key,
        group_key=group_key,
        method_key=method_key,
    )
    assert steps[-1].phase == PHASE_RESULT
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)
    exp = acc.resolve_limit_rule(
        job_key,
        metric_key,
        partner_key=partner_key,
        group_key=group_key,
        method_key=method_key,
    )
    got = limit_rule_from_explain_result(snap, steps)
    assert (exp.rule_key if exp else None) == (got.rule_key if got else None)


def test_parity_partner_scope_wins_over_global() -> None:
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
    limits = [
        LimitRule(
            rule_key="L_partner",
            job_key="wallet",
            scope_type="partner",
            scope_key=pk,
            metric_key="m",
            method_key=None,
            limit_type="max",
            limit_value=1.0,
            enabled=True,
        ),
        LimitRule(
            rule_key="L_global",
            job_key="wallet",
            scope_type="global",
            scope_key="*",
            metric_key="m",
            method_key=None,
            limit_type="max",
            limit_value=9.0,
            enabled=True,
        ),
    ]
    _assert_limit_parity(_snap(partners=partners, limits=limits), "wallet", "m", partner_key=pk)


def test_precedence_partner_miss_then_group_hit_global_index_not_consulted() -> None:
    """Partner and group keys set; no partner rule in index; group hits; global exists but unused."""
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
    limits = [
        LimitRule(
            rule_key="L_group",
            job_key="wallet",
            scope_type="group",
            scope_key=gk,
            metric_key="m",
            method_key=None,
            limit_type="max",
            limit_value=2.0,
            enabled=True,
        ),
        LimitRule(
            rule_key="L_global",
            job_key="wallet",
            scope_type="global",
            scope_key="*",
            metric_key="m",
            method_key=None,
            limit_type="max",
            limit_value=9.0,
            enabled=True,
        ),
    ]
    snap = _snap(partners=partners, limits=limits)
    idx = build_indexes(snap)
    steps = explain_resolve_limit_rule(snap, idx, "wallet", "m", partner_key=pk, group_key=gk)

    lookups = [s for s in steps if s.phase == PHASE_INDEX_LOOKUP]
    hits = [s for s in lookups if s.outcome == "hit"]
    assert len(hits) == 1
    assert hits[0].inputs.get("rule_key") == "L_group"
    assert hits[0].candidates[0]["scope_type"] == "group"

    assert lookups[0].outcome == "miss"
    assert lookups[0].candidates[0]["scope_type"] == "partner"

    global_lookups = [
        s for s in lookups if s.index_key is not None and s.index_key[1] == "global"
    ]
    assert global_lookups == []

    _assert_limit_parity(snap, "wallet", "m", partner_key=pk, group_key=gk)


def test_parity_group_scope_when_partner_misses() -> None:
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
    limits = [
        LimitRule(
            rule_key="L_partner_disabled",
            job_key="wallet",
            scope_type="partner",
            scope_key=pk,
            metric_key="m",
            method_key=None,
            limit_type="max",
            limit_value=1.0,
            enabled=False,
        ),
        LimitRule(
            rule_key="L_group",
            job_key="wallet",
            scope_type="group",
            scope_key=gk,
            metric_key="m",
            method_key=None,
            limit_type="max",
            limit_value=2.0,
            enabled=True,
        ),
    ]
    _assert_limit_parity(_snap(partners=partners, limits=limits), "wallet", "m", partner_key=pk, group_key=gk)


def test_parity_method_specific_before_generic() -> None:
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
    limits = [
        LimitRule(
            rule_key="L_uni",
            job_key="wallet",
            scope_type="partner",
            scope_key=pk,
            metric_key="m",
            method_key="uni",
            limit_type="max",
            limit_value=3.0,
            enabled=True,
        ),
        LimitRule(
            rule_key="L_any",
            job_key="wallet",
            scope_type="partner",
            scope_key=pk,
            metric_key="m",
            method_key=None,
            limit_type="max",
            limit_value=4.0,
            enabled=True,
        ),
    ]
    _assert_limit_parity(_snap(partners=partners, limits=limits), "wallet", "m", partner_key=pk, method_key="uni")


def test_parity_miss_when_no_rule() -> None:
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
    _assert_limit_parity(_snap(partners=partners, limits=[]), "wallet", "missing_metric", partner_key=pk)


def test_index_key_matches_limit_rules_index_tuple() -> None:
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
    limits = [
        LimitRule(
            rule_key="L_uni",
            job_key="wallet",
            scope_type="partner",
            scope_key=pk,
            metric_key="m",
            method_key=None,
            limit_type="max",
            limit_value=3.0,
            enabled=True,
        ),
    ]
    snap = _snap(partners=partners, limits=limits)
    idx = build_indexes(snap)
    steps = explain_resolve_limit_rule(snap, idx, "wallet", "m", partner_key=pk, method_key=None)
    lookup_steps = [s for s in steps if s.phase == PHASE_INDEX_LOOKUP and s.outcome == "hit"]
    assert len(lookup_steps) == 1
    key = ("wallet", "partner", pk, "m", None)
    assert lookup_steps[0].index_key == key
    assert idx.limit_rules_index.get(key) is not None
