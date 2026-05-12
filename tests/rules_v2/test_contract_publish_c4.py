"""C4 contract publish policy (strict / legacy / shadow)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import job_runner
from core.rules_provider import (
    ContractPublishRejected,
    get_indexes_v2,
    get_rules_snapshot,
    get_snapshot_v2,
    invalidate_rules_v2_cache,
)
from core.rules_v2.contract_errors import RULE_DUPLICATE_LIMIT, make_issue
from core.rules_v2.contract_publish import (
    ContractValidationMode,
    evaluate_snapshot_publish,
    resolve_contract_validation_mode,
)
from core.rules_v2.validation_issues import ValidationSeverity
from core.rules_v2.validation_snapshot import validate_snapshot as real_validate_snapshot

C5_BASELINE = Path(__file__).resolve().parent / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"


@pytest.fixture(autouse=True)
def _reset_caches():
    invalidate_rules_v2_cache()
    yield
    invalidate_rules_v2_cache()


@pytest.fixture
def rules_xlsx_baseline(monkeypatch: pytest.MonkeyPatch) -> Path:
    assert C5_BASELINE.is_file()
    monkeypatch.setenv("RULES_XLSX_PATH", str(C5_BASELINE))
    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    monkeypatch.delenv("RULES_CONTRACT_SHADOW", raising=False)
    return C5_BASELINE


def test_resolve_precedence_strict_over_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_CONTRACT_STRICT", "1")
    monkeypatch.setenv("RULES_CONTRACT_SHADOW", "1")
    assert resolve_contract_validation_mode() == ContractValidationMode.STRICT


def test_resolve_shadow_when_strict_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    monkeypatch.setenv("RULES_CONTRACT_SHADOW", "1")
    assert resolve_contract_validation_mode() == ContractValidationMode.SHADOW


def test_legacy_get_snapshot_v2_succeeds_with_contract_warns(rules_xlsx_baseline: Path) -> None:
    snap = get_snapshot_v2(force_sync=True)
    assert snap.meta.ruleset_version


def test_get_rules_snapshot_exposes_rules_version_for_job_runner(rules_xlsx_baseline: Path) -> None:
    rs = get_rules_snapshot(force_sync=True)
    assert isinstance(rs.rules_version, str)
    assert rs.rules_version == rs.rules_version.strip()
    assert rs.source


def test_get_rules_snapshot_rules_version_matches_snapshot_meta(rules_xlsx_baseline: Path) -> None:
    snap = get_snapshot_v2(force_sync=True)
    rs = get_rules_snapshot(force_sync=False)
    assert rs.rules_version == snap.meta.ruleset_version


def test_request_job_does_not_raise_on_rules_snapshot_contract(rules_xlsx_baseline: Path) -> None:
    job_id = job_runner.request_job(
        "__nonexistent_c4_rules_contract_job__",
        job_runner.Actor("cli"),
        force_rules_sync=True,
    )
    assert len(job_id) == 12


def test_strict_blocks_on_extra_blocking_issue(rules_xlsx_baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_CONTRACT_STRICT", "1")

    def _with_dup(snap, *, strict: bool = False):
        out = list(real_validate_snapshot(snap, strict=strict))
        out.append(
            make_issue(
                RULE_DUPLICATE_LIMIT,
                "injected duplicate for C4 test",
                sheet="wallet_limits",
                severity=ValidationSeverity.ERROR,
            )
        )
        return out

    with patch("core.rules_v2.contract_publish.validate_snapshot", side_effect=_with_dup):
        with pytest.raises(ContractPublishRejected) as ei:
            get_snapshot_v2(force_sync=True)
    d = ei.value.decision
    assert d.policy_mode == "strict"
    assert d.publish_allowed is False
    assert d.has_blocking_contract is True
    assert RULE_DUPLICATE_LIMIT in d.blocking_issue_codes
    assert d.snapshot_fingerprint


def test_shadow_publishes_with_extra_blocking_issue(rules_xlsx_baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_CONTRACT_SHADOW", "1")

    def _with_dup(snap, *, strict: bool = False):
        return list(real_validate_snapshot(snap, strict=strict)) + [
            make_issue(
                RULE_DUPLICATE_LIMIT,
                "injected duplicate for C4 test",
                sheet="wallet_limits",
                severity=ValidationSeverity.ERROR,
            )
        ]

    with patch("core.rules_v2.contract_publish.validate_snapshot", side_effect=_with_dup):
        snap = get_snapshot_v2(force_sync=True)
    assert snap.meta.ruleset_version


def test_evaluate_decision_deterministic_fingerprint(rules_xlsx_baseline: Path) -> None:
    a = evaluate_snapshot_publish(rules_xlsx_baseline, policy_mode=ContractValidationMode.LEGACY)
    b = evaluate_snapshot_publish(rules_xlsx_baseline, policy_mode=ContractValidationMode.LEGACY)
    assert a.snapshot_fingerprint == b.snapshot_fingerprint
    assert a.publish_allowed and b.publish_allowed


def test_get_indexes_v2_repeated_returns_same_instance(rules_xlsx_baseline: Path) -> None:
    sentinel = object()
    spy = MagicMock(return_value=sentinel)
    with patch("core.rules_provider.build_indexes", spy):
        a = get_indexes_v2()
        b = get_indexes_v2()
    assert a is b is sentinel
    assert spy.call_count == 1


def test_get_indexes_v2_force_sync_rebuilds_indexes(rules_xlsx_baseline: Path) -> None:
    spy = MagicMock(side_effect=lambda _snap: object())
    with patch("core.rules_provider.build_indexes", spy):
        get_indexes_v2()
        get_indexes_v2(force_sync=True)
    assert spy.call_count == 2


def test_invalidate_rules_v2_cache_clears_indexes_cache(rules_xlsx_baseline: Path) -> None:
    first = object()
    second = object()
    spy = MagicMock(side_effect=[first, second])
    with patch("core.rules_provider.build_indexes", spy):
        a = get_indexes_v2()
        invalidate_rules_v2_cache()
        b = get_indexes_v2()
    assert a is first
    assert b is second
    assert a is not b
    assert spy.call_count == 2
