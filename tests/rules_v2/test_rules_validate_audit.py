"""C7b rules validation audit trail (JSONL append-only)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.config_manager import rules_validate_all
from core.rules_provider import (
    ContractPublishRejected,
    get_rules_snapshot,
    get_snapshot_v2,
    invalidate_rules_v2_cache,
)
from core.rules_v2.contract_errors import RULE_DUPLICATE_LIMIT, make_issue
from core.rules_v2.contract_publish import ContractValidationMode, evaluate_snapshot_publish
from core.rules_v2.ops_rules_validate_summary import build_rules_validate_payload_for_publish_audit
from core.rules_v2.rules_validate_audit import (
    append_rules_validate_audit_record,
    rules_validate_audit_envelope,
    try_append_publish_audit_trail,
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


def test_audit_artifact_created_and_payload_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rules_xlsx_baseline: Path,
) -> None:
    logf = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RULES_VALIDATE_AUDIT_JSONL", str(logf))

    wb = get_rules_snapshot(force_sync=False)
    decision = evaluate_snapshot_publish(C5_BASELINE, policy_mode=ContractValidationMode.LEGACY)
    leg_e, leg_w = rules_validate_all(force_sync=False)
    payload = build_rules_validate_payload_for_publish_audit(wb, decision, list(leg_e), list(leg_w))
    append_rules_validate_audit_record(rules_validate_audit_envelope("publish_allowed", payload))

    lines = logf.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["schema_version"] == "rules_validate_audit.v1"
    assert rec["event_type"] == "publish_allowed"
    assert rec["event_timestamp_utc"].endswith("Z")
    assert "runtime_instance_id" in rec and rec["runtime_instance_id"]
    assert isinstance(rec["process_pid"], int)
    assert rec["payload"]["schema_version"] == "rules_validate_payload.v1"
    assert "stat_key" in rec["payload"]


def test_append_only_two_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rules_xlsx_baseline: Path) -> None:
    logf = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RULES_VALIDATE_AUDIT_JSONL", str(logf))
    wb = get_rules_snapshot(force_sync=False)
    decision = evaluate_snapshot_publish(C5_BASELINE, policy_mode=ContractValidationMode.LEGACY)
    leg_e, leg_w = rules_validate_all(force_sync=False)
    p = build_rules_validate_payload_for_publish_audit(wb, decision, list(leg_e), list(leg_w))
    append_rules_validate_audit_record(rules_validate_audit_envelope("manual_validate", p))
    append_rules_validate_audit_record(rules_validate_audit_envelope("manual_validate", p))
    assert len(logf.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_rejected_publish_audit_via_provider_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rules_xlsx_baseline: Path,
) -> None:
    logf = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RULES_VALIDATE_AUDIT_JSONL", str(logf))
    monkeypatch.setenv("RULES_CONTRACT_STRICT", "1")

    def _with_dup(snap, *, strict: bool = False):
        return list(real_validate_snapshot(snap, strict=strict)) + [
            make_issue(
                RULE_DUPLICATE_LIMIT,
                "blocking audit",
                sheet="wallet_limits",
                severity=ValidationSeverity.ERROR,
            )
        ]

    with patch("core.rules_v2.contract_publish.validate_snapshot", side_effect=_with_dup):
        with pytest.raises(ContractPublishRejected):
            get_snapshot_v2(force_sync=True)

    lines = logf.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event_type"] == "publish_rejected"
    assert rec["payload"]["publish_allowed"] is False


def test_audit_failure_does_not_break_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rules_xlsx_baseline: Path,
) -> None:
    logf = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RULES_VALIDATE_AUDIT_JSONL", str(logf))

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("simulated audit fs failure")

    with patch("core.rules_v2.rules_validate_audit.append_rules_validate_audit_record", side_effect=_boom):
        snap = get_snapshot_v2(force_sync=True)
    assert snap.meta.ruleset_version


def test_try_append_publish_audit_trail_swallows_errors(
    rules_xlsx_baseline: Path,
) -> None:
    wb = get_rules_snapshot(force_sync=False)
    decision = evaluate_snapshot_publish(C5_BASELINE, policy_mode=ContractValidationMode.LEGACY)

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("boom")

    with patch(
        "core.rules_v2.rules_validate_audit.build_rules_validate_payload_for_publish_audit",
        side_effect=_boom,
    ):
        try_append_publish_audit_trail(
            wb=wb,
            decision=decision,
            legacy_errors=[],
            legacy_warnings=[],
        )


def test_json_payload_stable_keys(rules_xlsx_baseline: Path) -> None:
    wb = get_rules_snapshot(force_sync=False)
    decision = evaluate_snapshot_publish(C5_BASELINE, policy_mode=ContractValidationMode.LEGACY)
    leg_e, leg_w = rules_validate_all(force_sync=False)
    p = build_rules_validate_payload_for_publish_audit(wb, decision, list(leg_e), list(leg_w))
    env = rules_validate_audit_envelope("publish_allowed", p)
    assert list(env.keys()) == [
        "schema_version",
        "event_type",
        "event_timestamp_utc",
        "runtime_instance_id",
        "process_pid",
        "payload",
    ]
