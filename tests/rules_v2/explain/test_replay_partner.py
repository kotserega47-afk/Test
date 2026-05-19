"""Parity tests: ``explain_resolve_partner`` vs ``BaseRulesAccessor.resolve_partner``."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.rules_v2.accessors import BaseRulesAccessor
from core.rules_v2.explain.replay import (
    explain_resolve_partner,
    partner_def_from_explain_result,
)
from core.rules_v2.explain.types import PHASE_RESULT, trace_steps_to_jsonable
from core.rules_v2.indexes import build_indexes
from core.rules_v2.models import MetaInfo, PartnerDef, RulesSnapshotV2
from core.rules_v2.normalizers import normalize_key


def _snap_with_partners(partners: dict[str, PartnerDef]) -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="explain_test",
        updated_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        updated_by="test",
    )
    return RulesSnapshotV2(meta=meta, partners=partners)


def _assert_parity(
    snap: RulesSnapshotV2,
    raw: str | None,
    *,
    expect_raw: bool = False,
) -> None:
    idx = build_indexes(snap)
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    steps = explain_resolve_partner(snap, idx, raw, expect_raw_excel_partner_label=expect_raw)
    assert steps[-1].phase == PHASE_RESULT
    json.dumps(trace_steps_to_jsonable(steps), ensure_ascii=False)
    exp = acc.resolve_partner(raw, expect_raw_excel_partner_label=expect_raw)
    got = partner_def_from_explain_result(snap, steps)
    assert (exp.partner_key if exp else None) == (got.partner_key if got else None)


def test_parity_hit_by_partner_code() -> None:
    partners = {
        "pk108": PartnerDef(
            partner_key="pk108",
            partner_code="108",
            source_name=None,
            display_name="P108",
            enabled=True,
        ),
    }
    _assert_parity(_snap_with_partners(partners), "Label (108)")


def test_parity_hit_by_normalize_key_fallback() -> None:
    raw = "only_normalize_me"
    nk = normalize_key(raw)
    partners = {
        nk: PartnerDef(
            partner_key=nk,
            partner_code=None,
            source_name=None,
            display_name="Norm",
            enabled=True,
        ),
    }
    _assert_parity(_snap_with_partners(partners), raw)


def test_parity_miss_no_match() -> None:
    partners = {
        "pk108": PartnerDef(
            partner_key="pk108",
            partner_code="108",
            source_name=None,
            display_name="P108",
            enabled=True,
        ),
    }
    _assert_parity(_snap_with_partners(partners), "no such partner string")


def test_parity_code_disabled_then_normalize_hit() -> None:
    """Accessor falls through when code-mapped partner is disabled."""

    raw = "X (108) extra"
    nk = normalize_key(raw)
    partners = {
        "pk108": PartnerDef(
            partner_key="pk108",
            partner_code="108",
            source_name=None,
            display_name="Off",
            enabled=False,
        ),
        nk: PartnerDef(
            partner_key=nk,
            partner_code=None,
            source_name=None,
            display_name="ViaNorm",
            enabled=True,
        ),
    }
    _assert_parity(_snap_with_partners(partners), raw)


def test_replay_import_boundary_subprocess() -> None:
    repo = Path(__file__).resolve().parents[3]
    # ``replay`` imports ``normalizers`` (partner resolution), which loads ``pandas``
    # via ``datetime_utils``; forbid provider / Excel / audit side only.
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "import core.rules_v2.explain.replay as r\n"
        "for m in ('openpyxl', 'core.rules_provider', "
        "'core.rules_v2.ops_rules_validate_summary', 'core.rules_v2.rules_validate_audit', "
        "'core.rules_v2.identity_drift', 'core.rules_v2.identity_registry_io'):\n"
        "    assert m not in sys.modules, m\n"
        "print('ok')\n"
    )
    subprocess.check_call([sys.executable, "-c", code], cwd=str(repo))
