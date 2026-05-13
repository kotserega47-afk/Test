"""Tests: ``explain_get_job_param`` vs ``BaseRulesAccessor.get_job_param``."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.rules_v2.accessors import BaseRulesAccessor
from core.rules_v2.explain.replay import explain_get_job_param
from core.rules_v2.explain.types import PHASE_INDEX_LOOKUP, PHASE_RESULT, trace_steps_to_jsonable
from core.rules_v2.indexes import build_indexes
from core.rules_v2.models import JobParam, MetaInfo, PartnerDef, RulesSnapshotV2


def _snap(
    *,
    partners: dict[str, PartnerDef],
    params: list[JobParam],
) -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="explain_job_param_test",
        updated_at=datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc),
        updated_by="test",
    )
    return RulesSnapshotV2(meta=meta, partners=partners, job_params=params)


def _jp(
    *,
    job_key: str,
    scope_type: str,
    scope_key: str,
    param_key: str,
    value,
    enabled: bool = True,
) -> JobParam:
    return JobParam(
        job_key=job_key,
        scope_type=scope_type,
        scope_key=scope_key,
        param_key=param_key,
        value_type="str",
        value=value,
        enabled=enabled,
    )


def test_partner_hit_before_group_and_global() -> None:
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
    params = [
        _jp(job_key="wallet", scope_type="partner", scope_key=pk, param_key="p1", value="from_partner"),
        _jp(job_key="wallet", scope_type="group", scope_key="grp1", param_key="p1", value="from_group"),
        _jp(job_key="wallet", scope_type="global", scope_key="*", param_key="p1", value="from_global"),
    ]
    snap = _snap(partners=partners, params=params)
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)

    steps = explain_get_job_param(snap, idx, "wallet", "p1", partner_key=pk, group_key="grp1")
    assert acc.get_job_param("wallet", "p1", partner_key=pk, group_key="grp1") == "from_partner"

    lookups = [s for s in steps if s.phase == PHASE_INDEX_LOOKUP]
    assert lookups[0].outcome == "hit"
    assert lookups[0].candidates[0]["scope_type"] == "partner"
    assert len([x for x in lookups if x.outcome == "hit"]) == 1

    assert steps[-1].phase == PHASE_RESULT
    assert steps[-1].outcome == "hit"
    assert steps[-1].inputs.get("value_source") == "snapshot"
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)


def test_partner_miss_group_hit() -> None:
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
    params = [
        _jp(job_key="wallet", scope_type="group", scope_key=gk, param_key="p1", value="g"),
    ]
    snap = _snap(partners=partners, params=params)
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)

    steps = explain_get_job_param(snap, idx, "wallet", "p1", partner_key=pk, group_key=gk)
    assert acc.get_job_param("wallet", "p1", partner_key=pk, group_key=gk) == "g"

    lookups = [s for s in steps if s.phase == PHASE_INDEX_LOOKUP]
    assert lookups[0].outcome == "miss"
    assert lookups[1].outcome == "hit"
    assert steps[-1].outcome == "hit"
    assert steps[-1].inputs.get("value_source") == "snapshot"
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)


def test_global_hit() -> None:
    params = [_jp(job_key="wallet", scope_type="global", scope_key="*", param_key="p1", value="glob")]
    snap = _snap(partners={}, params=params)
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    steps = explain_get_job_param(snap, idx, "wallet", "p1")

    assert acc.get_job_param("wallet", "p1") == "glob"
    hits = [s for s in steps if s.phase == PHASE_INDEX_LOOKUP and s.outcome == "hit"]
    assert len(hits) == 1
    assert hits[0].index_key == ("wallet", "global", "*", "p1")
    assert steps[-1].inputs.get("value_source") == "snapshot"
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)


def test_complete_miss_returns_default() -> None:
    snap = _snap(partners={}, params=[])
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    steps = explain_get_job_param(snap, idx, "wallet", "missing", default=99)

    assert acc.get_job_param("wallet", "missing", default=99) == 99
    assert steps[-1].phase == PHASE_RESULT
    assert steps[-1].outcome == "miss"
    assert steps[-1].inputs.get("value_source") == "default"
    assert all(s.outcome == "miss" for s in steps if s.phase == PHASE_INDEX_LOOKUP)
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)


def test_snapshot_value_none_with_default_none_trace_hit() -> None:
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
    params = [_jp(job_key="wallet", scope_type="partner", scope_key=pk, param_key="nullish", value=None)]
    snap = _snap(partners=partners, params=params)
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    steps = explain_get_job_param(snap, idx, "wallet", "nullish", partner_key=pk, default=None)

    assert acc.get_job_param("wallet", "nullish", partner_key=pk, default=None) is None
    assert steps[-1].outcome == "hit"
    assert steps[-1].inputs.get("value_source") == "snapshot"
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)


def test_miss_with_default_none_trace_miss_and_default_source() -> None:
    snap = _snap(partners={}, params=[])
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    steps = explain_get_job_param(snap, idx, "wallet", "nope", default=None)

    assert acc.get_job_param("wallet", "nope", default=None) is None
    assert steps[-1].outcome == "miss"
    assert steps[-1].inputs.get("value_source") == "default"
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)


def test_parity_accessor_value_matches_final_trace_semantics() -> None:
    """Final returned value matches accessor; trace encodes source without storing values."""

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
    params = [_jp(job_key="wallet", scope_type="partner", scope_key=pk, param_key="k", value={"nested": 1})]
    snap = _snap(partners=partners, params=params)
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)

    steps = explain_get_job_param(snap, idx, "wallet", "k", partner_key=pk, default="fallback")
    exp = acc.get_job_param("wallet", "k", partner_key=pk, default="fallback")

    assert exp == {"nested": 1}
    assert steps[-1].outcome == "hit"
    assert steps[-1].inputs == {"value_source": "snapshot"}
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)

    steps_miss = explain_get_job_param(snap, idx, "wallet", "absent", partner_key=pk, default={"d": 2})
    exp_miss = acc.get_job_param("wallet", "absent", partner_key=pk, default={"d": 2})
    assert exp_miss == {"d": 2}
    assert steps_miss[-1].outcome == "miss"
    assert steps_miss[-1].inputs == {"value_source": "default"}
    json.dumps(trace_steps_to_jsonable(steps_miss), ensure_ascii=False)
