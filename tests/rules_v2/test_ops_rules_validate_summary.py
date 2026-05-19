"""C6 — /rules_validate diagnostics (read-only, no provider mutation)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import json
import pytest

from core.rules_provider import get_snapshot_v2, invalidate_rules_v2_cache
from core.rules_provider import get_rules_snapshot
from core.rules_v2.contract_errors import RULE_DUPLICATE_LIMIT, RULE_IMMUTABLE_ID_VIOLATION, make_issue
from core.rules_v2.contract_publish import ContractValidationMode, evaluate_snapshot_publish
from core.rules_v2.ops_rules_validate_summary import (
    assemble_rules_validate_payload,
    build_rules_validate_diagnostics,
    build_rules_validate_payload,
    build_rules_validate_telegram_chunks,
    chunk_telegram_text,
    format_rules_validate_telegram,
    rules_validate_payload_json_dumps,
    rules_validate_payload_to_jsonable,
)
from tests.rules_v2.test_contract_publish_identity import _drift_workbook_and_registry
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


def test_output_contains_version_fingerprint_mode(rules_xlsx_baseline: Path) -> None:
    d = build_rules_validate_diagnostics()
    text = format_rules_validate_telegram(d)
    assert d.meta_version
    assert d.snapshot_fingerprint
    assert "meta.version (ruleset):" in text
    assert d.meta_version in text
    assert d.snapshot_fingerprint in text
    assert "validation_mode: legacy" in text
    assert "stat_key (mtime,size):" in text


def test_warnings_counted_correctly(rules_xlsx_baseline: Path) -> None:
    with patch(
        "core.rules_v2.ops_rules_validate_summary.rules_validate_all",
        return_value=([], ["schedules: unknown schedule_type='weird' (id=1)", "other warn"]),
    ):
        d = build_rules_validate_diagnostics()
    assert d.legacy_warning_count == 2
    assert d.total_issue_count == d.contract_issue_total + 2


def test_blocking_errors_and_publish_allowed_strict(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_CONTRACT_STRICT", "1")

    def _with_dup(snap, *, strict: bool = False):
        out = list(real_validate_snapshot(snap, strict=strict))
        out.append(
            make_issue(
                RULE_DUPLICATE_LIMIT,
                "injected duplicate for C6 telegram summary",
                sheet="wallet_limits",
                severity=ValidationSeverity.ERROR,
            )
        )
        return out

    with patch("core.rules_v2.contract_publish.validate_snapshot", side_effect=_with_dup):
        d = build_rules_validate_diagnostics()
    assert d.validation_mode == "strict"
    assert d.publish_allowed is False
    assert d.has_blocking_contract is True
    assert RULE_DUPLICATE_LIMIT in d.blocking_issue_codes
    assert any("injected duplicate" in rec.message for rec in d.contract_top_blocking)
    text = format_rules_validate_telegram(d)
    assert "publish_allowed: no" in text
    assert "• injected duplicate" in text


def test_publish_allowed_yes_legacy(rules_xlsx_baseline: Path) -> None:
    d = build_rules_validate_diagnostics()
    assert d.publish_allowed is True
    assert "publish_allowed: yes" in format_rules_validate_telegram(d)


def test_command_does_not_mutate_runtime_snapshot(rules_xlsx_baseline: Path) -> None:
    get_snapshot_v2(force_sync=True)
    before = get_snapshot_v2(force_sync=False)
    build_rules_validate_diagnostics()
    after = get_snapshot_v2(force_sync=False)
    assert before is after


def test_works_with_contract_warnings_only(rules_xlsx_baseline: Path) -> None:
    """Legacy policy may downgrade duplicates to warns — diagnostics still build."""

    def _only_warns(snap, *, strict: bool = False):
        return [
            make_issue(
                RULE_DUPLICATE_LIMIT,
                "duplicate but warn-level for test",
                sheet="wallet_limits",
                severity=ValidationSeverity.WARN,
            )
        ]

    with patch("core.rules_v2.contract_publish.validate_snapshot", side_effect=_only_warns):
        d = build_rules_validate_diagnostics()
    chunks = build_rules_validate_telegram_chunks()
    assert chunks
    assert d.contract_warning_count >= 1
    combined = "\n".join(chunks)
    assert "warnings:" in combined


def test_strict_blocking_diagnostics_structurally_reports_publish_rejected(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict + blocking: diagnostics completes; payload encodes publish_rejected (no repeated get_snapshot assert)."""

    monkeypatch.setenv("RULES_CONTRACT_STRICT", "1")

    def _with_dup(snap, *, strict: bool = False):
        return list(real_validate_snapshot(snap, strict=strict)) + [
            make_issue(
                RULE_DUPLICATE_LIMIT,
                "blocking for C6",
                sheet="wallet_limits",
                severity=ValidationSeverity.ERROR,
            )
        ]

    with patch("core.rules_v2.contract_publish.validate_snapshot", side_effect=_with_dup):
        d = build_rules_validate_diagnostics()

    assert d.active_runtime_readable is False
    assert d.active_failure_kind == "publish_rejected"
    assert d.publish_allowed is False
    assert d.has_blocking_contract is True
    assert RULE_DUPLICATE_LIMIT in d.blocking_issue_codes
    assert any("blocking for C6" in rec.message for rec in d.contract_top_blocking)

    exported = rules_validate_payload_to_jsonable(d)
    assert exported["active_failure_kind"] == "publish_rejected"
    assert exported["publish_allowed"] is False
    assert exported["has_blocking_contract"] is True
    assert RULE_DUPLICATE_LIMIT in exported["blocking_issue_codes"]


def test_payload_jsonable_roundtrip(rules_xlsx_baseline: Path) -> None:
    p = build_rules_validate_payload()
    dct = rules_validate_payload_to_jsonable(p)
    assert dct["schema_version"] == "rules_validate_payload.v1"
    assert isinstance(dct["stat_key"], list) and len(dct["stat_key"]) == 2
    assert dct["stat_key"] == [p.stat_key_mtime, p.stat_key_size]
    s = rules_validate_payload_json_dumps(p)
    assert "meta_version" in s
    roundtrip = json.loads(s)
    assert roundtrip["publish_allowed"] == p.publish_allowed


def test_formatter_matches_json_snapshot_fields(rules_xlsx_baseline: Path) -> None:
    """Telegram layout is derived from the same frozen payload as ``rules_validate_payload_to_jsonable``."""

    p = build_rules_validate_payload()
    text = format_rules_validate_telegram(p)
    j = rules_validate_payload_to_jsonable(p)
    assert j["meta_version"] in text
    fp = j.get("snapshot_fingerprint")
    if fp:
        assert fp in text
    assert j["validation_mode"] in text


def test_chunk_split_makes_multiple_messages() -> None:
    body = "\n".join([f"line-{i}-{'x' * 40}" for i in range(400)])
    chunks = chunk_telegram_text(body, limit=120)
    assert len(chunks) >= 2


def _shadow_drift_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _drift_workbook_and_registry(tmp_path, monkeypatch)
    monkeypatch.setenv("RULES_XLSX_PATH", str(path))
    monkeypatch.setenv("RULES_IDENTITY_COMPARE", "shadow")
    wb = get_rules_snapshot(force_sync=False)
    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)
    assert decision.identity_shadow_issues
    published = decision.snapshot if decision.publish_allowed else None
    active_mode = "published" if published is not None else "rejected_audit"
    payload = assemble_rules_validate_payload(
        wb,
        decision,
        [],
        [],
        active_mode=active_mode,
        published_snapshot=published,
    )
    return path, decision, payload


def test_shadow_findings_in_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _path, decision, p = _shadow_drift_payload(tmp_path, monkeypatch)
    assert p.identity_shadow_issue_total >= 1
    assert p.identity_shadow_issues
    assert RULE_IMMUTABLE_ID_VIOLATION in {r.code for r in p.identity_shadow_issues}
    exported = rules_validate_payload_to_jsonable(p)
    assert exported["identity_shadow_issue_total"] >= 1
    assert exported["identity_shadow_issues"]
    assert RULE_IMMUTABLE_ID_VIOLATION not in {i.code for i in decision.contract_issues}


def test_shadow_findings_do_not_affect_blocking_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _path, decision, p = _shadow_drift_payload(tmp_path, monkeypatch)
    assert RULE_IMMUTABLE_ID_VIOLATION not in p.blocking_issue_codes
    assert p.has_blocking_contract == decision.has_blocking_contract
    assert p.contract_error_count == decision.error_count
    assert p.contract_warning_count == decision.warning_count
    assert p.contract_info_count == decision.info_count
    assert p.publish_allowed == decision.publish_allowed


def test_shadow_telegram_section_separate_from_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _path, _decision, p = _shadow_drift_payload(tmp_path, monkeypatch)
    text = format_rules_validate_telegram(p)
    assert "— Identity shadow findings —" in text
    assert p.identity_shadow_issues[0].message in text
    contract_section = text.split("— Identity shadow findings —")[0]
    assert RULE_IMMUTABLE_ID_VIOLATION not in contract_section or "identity_shadow" in contract_section


def test_enforce_mode_payload_merges_identity_into_contract_not_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _drift_workbook_and_registry(tmp_path, monkeypatch)
    monkeypatch.setenv("RULES_XLSX_PATH", str(path))
    monkeypatch.setenv("RULES_IDENTITY_COMPARE", "enforce")
    wb = get_rules_snapshot(force_sync=False)
    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)
    p = assemble_rules_validate_payload(
        wb,
        decision,
        [],
        [],
        active_mode="published" if decision.snapshot else "rejected_audit",
        published_snapshot=decision.snapshot,
    )
    assert RULE_IMMUTABLE_ID_VIOLATION in {r.code for r in p.contract_top_blocking + p.contract_top_warnings}
    assert p.identity_shadow_issue_total == 0
    assert p.identity_shadow_issues == ()
