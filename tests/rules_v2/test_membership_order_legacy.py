"""Primary group ordering + validation (CONTRACT_V2 §8.3 Stage 1).

* Legacy: no ``is_primary`` / ``group_priority`` → snapshot insertion order.
* Explicit ``is_primary`` / ``group_priority`` → deterministic first element
  for ``get_group_memberships`` / ``get_primary_group`` / wallet ``[0]``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.rules_v2.accessors import BaseRulesAccessor
from core.rules_v2.contract_errors import RULE_NON_DETERMINISTIC_ORDER
from core.rules_v2.indexes import build_indexes
from core.rules_v2.models import (
    MetaInfo,
    PartnerDef,
    PartnerGroupDef,
    PartnerGroupMember,
    RulesSnapshotV2,
)
from core.rules_v2.validation_snapshot import validate_snapshot


def _meta() -> MetaInfo:
    return MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        updated_by="unit-test",
    )


def _partner(key: str) -> PartnerDef:
    return PartnerDef(
        partner_key=key,
        partner_code=None,
        source_name=key,
        display_name=key,
    )


def _group(key: str) -> PartnerGroupDef:
    return PartnerGroupDef(group_key=key, display_name=key)


def _snapshot_memberships(group_order: list[str]) -> RulesSnapshotV2:
    """``group_order`` defines ``partner_group_members`` list order (simulates Excel rows)."""
    snap = RulesSnapshotV2(meta=_meta())
    snap.partner_groups = {
        "group_a": _group("group_a"),
        "group_b": _group("group_b"),
    }
    snap.partners = {"p1": _partner("p1")}
    snap.partner_group_members = [
        PartnerGroupMember(
            group_key=gk,
            partner_key="p1",
            job_key="wallet",
        )
        for gk in group_order
    ]
    return snap


def _snap_rows(
    rows: list[tuple[str, bool, int | None]],
    *,
    job_key: str = "wallet",
    partner_key: str = "p1",
) -> RulesSnapshotV2:
    """``(group_key, is_primary, group_priority)`` in snapshot insertion order."""

    snap = RulesSnapshotV2(meta=_meta())
    gkeys = {gk for gk, _, _ in rows}
    snap.partner_groups = {gk: _group(gk) for gk in gkeys}
    snap.partners = {partner_key: _partner(partner_key)}
    snap.partner_group_members = [
        PartnerGroupMember(
            group_key=gk,
            partner_key=partner_key,
            job_key=job_key,
            is_primary=isp,
            group_priority=gp,
        )
        for gk, isp, gp in rows
    ]
    return snap


def _nd_primary_ambiguity(issues: list) -> list:
    return [
        i
        for i in issues
        if i.code == RULE_NON_DETERMINISTIC_ORDER
        and i.field == "group_name"
        and (i.details or {}).get("job_key") == "wallet"
        and (i.details or {}).get("partner_key") == "p1"
    ]


def _accessor(snap: RulesSnapshotV2) -> BaseRulesAccessor:
    return BaseRulesAccessor(snapshot=snap, indexes=build_indexes(snap))


def test_legacy_membership_order_ab_matches_snapshot_insertion_order():
    snap = _snapshot_memberships(["group_a", "group_b"])
    acc = _accessor(snap)
    ms = acc.get_group_memberships("wallet", "p1")
    assert [m.group_key for m in ms] == ["group_a", "group_b"]
    primary = acc.get_primary_group("wallet", "p1")
    assert primary is not None and primary.group_key == "group_a"
    assert ms[0] is primary


def test_legacy_membership_order_ba_matches_snapshot_insertion_order():
    snap = _snapshot_memberships(["group_b", "group_a"])
    acc = _accessor(snap)
    ms = acc.get_group_memberships("wallet", "p1")
    assert [m.group_key for m in ms] == ["group_b", "group_a"]
    primary = acc.get_primary_group("wallet", "p1")
    assert primary is not None and primary.group_key == "group_b"
    assert ms[0] is primary


def test_multi_membership_emits_non_deterministic_warn_both_orders():
    for order in (["group_a", "group_b"], ["group_b", "group_a"]):
        snap = _snapshot_memberships(order)
        issues = validate_snapshot(snap, strict=True)
        nd = _nd_primary_ambiguity(issues)
        assert len(nd) == 1, f"expected single primary ambiguity for order {order}, got {issues}"
        assert sorted((nd[0].details or {}).get("group_keys", [])) == ["group_a", "group_b"]
        assert (nd[0].details or {}).get("disambiguation") == "no_explicit_metadata"


def test_single_is_primary_reorders_first_no_validation_warn():
    snap = _snap_rows(
        [
            ("group_a", False, None),
            ("group_b", True, None),
        ]
    )
    issues = validate_snapshot(snap, strict=True)
    assert _nd_primary_ambiguity(issues) == []

    acc = _accessor(snap)
    ms = acc.get_group_memberships("wallet", "p1")
    assert [m.group_key for m in ms] == ["group_b", "group_a"]
    prim = acc.get_primary_group("wallet", "p1")
    assert prim is not None and prim.group_key == "group_b"
    assert ms[0] is prim


def test_two_is_primary_keeps_snapshot_order_and_warns():
    snap = _snap_rows(
        [
            ("group_a", True, None),
            ("group_b", True, None),
        ]
    )
    issues = validate_snapshot(snap, strict=True)
    nd = _nd_primary_ambiguity(issues)
    assert len(nd) == 1
    assert (nd[0].details or {}).get("disambiguation") == "multiple_is_primary"

    acc = _accessor(snap)
    ms = acc.get_group_memberships("wallet", "p1")
    assert [m.group_key for m in ms] == ["group_a", "group_b"]
    assert ms[0] is acc.get_primary_group("wallet", "p1")


def test_unique_min_group_priority_reorders_first_no_warn():
    snap = _snap_rows(
        [
            ("group_a", False, 2),
            ("group_b", False, 1),
        ]
    )
    issues = validate_snapshot(snap, strict=True)
    assert _nd_primary_ambiguity(issues) == []

    acc = _accessor(snap)
    ms = acc.get_group_memberships("wallet", "p1")
    assert [m.group_key for m in ms] == ["group_b", "group_a"]
    assert ms[0] is acc.get_primary_group("wallet", "p1")


def test_tied_min_group_priority_snapshot_order_and_warns():
    snap = _snap_rows(
        [
            ("group_a", False, 1),
            ("group_b", False, 1),
        ]
    )
    issues = validate_snapshot(snap, strict=True)
    nd = _nd_primary_ambiguity(issues)
    assert len(nd) == 1
    assert (nd[0].details or {}).get("disambiguation") == "tied_group_priority"

    acc = _accessor(snap)
    ms = acc.get_group_memberships("wallet", "p1")
    assert [m.group_key for m in ms] == ["group_a", "group_b"]
    assert ms[0] is acc.get_primary_group("wallet", "p1")


def test_mixed_is_primary_true_wins_despite_group_priority_on_other_row():
    snap = _snap_rows(
        [
            ("group_a", False, None),
            ("group_b", True, 99),
        ]
    )
    issues = validate_snapshot(snap, strict=True)
    assert _nd_primary_ambiguity(issues) == []

    acc = _accessor(snap)
    ms = acc.get_group_memberships("wallet", "p1")
    assert [m.group_key for m in ms] == ["group_b", "group_a"]
    assert ms[0] is acc.get_primary_group("wallet", "p1")


def test_unique_priority_one_row_without_priority_still_resolves():
    """Single numeric priority among rows with ``None`` → unique minimum."""

    snap = _snap_rows(
        [
            ("group_a", False, None),
            ("group_b", False, 5),
        ]
    )
    assert _nd_primary_ambiguity(validate_snapshot(snap, strict=True)) == []

    acc = _accessor(snap)
    ms = acc.get_group_memberships("wallet", "p1")
    assert [m.group_key for m in ms] == ["group_b", "group_a"]
    assert ms[0].group_key == "group_b"