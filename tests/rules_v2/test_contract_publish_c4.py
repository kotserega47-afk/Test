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
from core.rules_v2.contract_errors import (
    RULE_DUPLICATE_LIMIT,
    RULE_EMPTY_JOBS,
    RULE_ORPHAN_JOB,
    RULE_UNSUPPORTED_JOB_PARAM,
    RULE_INVALID_THRESHOLD,
    make_issue,
)
from core.rules_v2.contract_publish import (
    ContractValidationMode,
    SnapshotPublishDecision,
    evaluate_snapshot_publish,
    resolve_contract_validation_mode,
)
from core.rules_v2.models import MetaInfo, RulesSnapshotV2
from datetime import datetime
from core.rules_v2.contract_schema import SHEET_SCHEMAS
from core.rules_v2.validation_issues import ValidationSeverity, has_blocking_errors
from core.rules_v2.validation_snapshot import validate_snapshot as real_validate_snapshot

C5_BASELINE = Path(__file__).resolve().parent / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"


def _workbook_empty_jobs(tmp_path: Path) -> Path:
    """Workbook that builds a snapshot with ``jobs={}`` (no job_key sources)."""

    import pandas as pd

    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 3},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
            {"key": "updated_by", "value": "test"},
        ]
    )
    frames["access"] = pd.DataFrame(
        [{"chat_id": "1", "user_id": "1", "level": 1, "enabled": 1, "note": ""}]
    )
    frames["commands"] = pd.DataFrame(
        [
            {
                "command": "/start",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
                "note": "",
            }
        ]
    )
    path = tmp_path / "empty_jobs.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
    return path


def _workbook_orphan_job_membership(tmp_path: Path) -> Path:
    """``jobs`` has ``wallet`` only; membership references ``hourly`` via analyzers."""

    import pandas as pd

    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 3},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
            {"key": "updated_by", "value": "test"},
        ]
    )
    frames["access"] = pd.DataFrame(
        [{"chat_id": "1", "user_id": "1", "level": 1, "enabled": 1, "note": ""}]
    )
    frames["commands"] = pd.DataFrame(
        [
            {
                "command": "/start",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
                "note": "",
            }
        ]
    )
    frames["schedules"] = pd.DataFrame(
        [
            {
                "id": "SCHED-1",
                "enabled": 1,
                "job_type": "wallet",
                "schedule_type": "interval",
                "every_seconds": 60,
                "cron": "",
                "jitter_sec": 0,
                "max_runtime_sec": 0,
                "coalesce": 1,
            }
        ]
    )
    frames["partner_groups"] = pd.DataFrame(
        [
            {
                "id": "PGM-1",
                "enabled": 1,
                "analyzers": "hourly",
                "group_name": "Group A",
                "partner": "Acme Corp",
            }
        ]
    )
    path = tmp_path / "orphan_job_membership.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
    return path


def _workbook_unsupported_job_param(tmp_path: Path) -> Path:
    """``hourly`` job with a ``param_key`` outside ``ALLOWED_JOB_PARAMS``."""

    import pandas as pd

    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 3},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
            {"key": "updated_by", "value": "test"},
        ]
    )
    frames["access"] = pd.DataFrame(
        [{"chat_id": "1", "user_id": "1", "level": 1, "enabled": 1, "note": ""}]
    )
    frames["commands"] = pd.DataFrame(
        [
            {
                "command": "/start",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
                "note": "",
            }
        ]
    )
    frames["job_params"] = pd.DataFrame(
        [
            {
                "id": "JP-1",
                "enabled": 1,
                "job": "hourly",
                "scope": "global",
                "scope_value": "*",
                "key": "not_in_catalog",
                "value_type": "int",
                "value": 1,
                "comment": "",
            }
        ]
    )
    path = tmp_path / "unsupported_job_param.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
    return path


def _workbook_invalid_threshold_bounds(tmp_path: Path) -> Path:
    """Enabled ``thresholds_partner`` row with both bounds empty after bridge."""

    import pandas as pd

    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 3},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
            {"key": "updated_by", "value": "test"},
        ]
    )
    frames["access"] = pd.DataFrame(
        [{"chat_id": "1", "user_id": "1", "level": 1, "enabled": 1, "note": ""}]
    )
    frames["commands"] = pd.DataFrame(
        [
            {
                "command": "/start",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
                "note": "",
            }
        ]
    )
    frames["thresholds_partner"] = pd.DataFrame(
        [
            {
                "id": "THR-1",
                "enabled": 1,
                "analyzer": "wallet",
                "partner": "Acme Corp",
                "metric": "conversion_rate",
                "threshold_min": None,
                "threshold_max": None,
                "reason": "test",
            }
        ]
    )
    path = tmp_path / "invalid_threshold.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
    return path


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


def test_strict_publish_blocked_on_empty_jobs(tmp_path: Path) -> None:
    path = _workbook_empty_jobs(tmp_path)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.snapshot is not None
    assert decision.snapshot.jobs == {}
    assert decision.publish_allowed is False
    assert RULE_EMPTY_JOBS in decision.blocking_issue_codes


def test_strict_publish_blocked_on_orphan_job_membership(tmp_path: Path) -> None:
    path = _workbook_orphan_job_membership(tmp_path)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.snapshot is not None
    assert list(decision.snapshot.jobs) == ["wallet"]
    assert decision.publish_allowed is False
    assert RULE_ORPHAN_JOB in decision.blocking_issue_codes


def test_strict_publish_blocked_on_unsupported_job_param(tmp_path: Path) -> None:
    path = _workbook_unsupported_job_param(tmp_path)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.snapshot is not None
    assert decision.publish_allowed is False
    assert RULE_UNSUPPORTED_JOB_PARAM in decision.blocking_issue_codes


def test_strict_publish_blocked_on_invalid_threshold_bounds(tmp_path: Path) -> None:
    path = _workbook_invalid_threshold_bounds(tmp_path)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.snapshot is not None
    assert decision.publish_allowed is False
    assert RULE_INVALID_THRESHOLD in decision.blocking_issue_codes


def test_legacy_publish_allowed_on_invalid_threshold_bounds_warn(tmp_path: Path) -> None:
    path = _workbook_invalid_threshold_bounds(tmp_path)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)

    assert decision.publish_allowed is True
    bad = [i for i in decision.contract_issues if i.code == RULE_INVALID_THRESHOLD]
    assert len(bad) == 1
    assert bad[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(decision.contract_issues)


def test_legacy_publish_allowed_on_unsupported_job_param_warn(tmp_path: Path) -> None:
    path = _workbook_unsupported_job_param(tmp_path)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)

    assert decision.publish_allowed is True
    bad = [i for i in decision.contract_issues if i.code == RULE_UNSUPPORTED_JOB_PARAM]
    assert len(bad) == 1
    assert bad[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(decision.contract_issues)


def test_legacy_publish_allowed_on_orphan_job_membership_warn(tmp_path: Path) -> None:
    path = _workbook_orphan_job_membership(tmp_path)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)

    assert decision.publish_allowed is True
    orphans = [i for i in decision.contract_issues if i.code == RULE_ORPHAN_JOB]
    assert len(orphans) == 1
    assert orphans[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(decision.contract_issues)


def test_legacy_publish_allowed_on_empty_jobs_warn(tmp_path: Path) -> None:
    path = _workbook_empty_jobs(tmp_path)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)

    assert decision.publish_allowed is True
    empty = [i for i in decision.contract_issues if i.code == RULE_EMPTY_JOBS]
    assert len(empty) == 1
    assert empty[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(decision.contract_issues)


def test_baseline_empty_jobs_legacy_warn_publish_allowed(rules_xlsx_baseline: Path) -> None:
    """C5 baseline builds ``jobs={}``; legacy still publishes with WARN."""

    decision = evaluate_snapshot_publish(
        rules_xlsx_baseline,
        policy_mode=ContractValidationMode.LEGACY,
    )

    assert decision.snapshot is not None
    assert decision.snapshot.jobs == {}
    assert decision.publish_allowed is True
    empty = [i for i in decision.contract_issues if i.code == RULE_EMPTY_JOBS]
    assert len(empty) == 1
    assert empty[0].severity == ValidationSeverity.WARN


def test_workbook_with_schedule_job_has_no_empty_jobs_issue(tmp_path: Path) -> None:
    """Snapshot derived from ``schedules.job_type`` is not flagged."""

    import pandas as pd

    path = _workbook_empty_jobs(tmp_path)
    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 3},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
            {"key": "updated_by", "value": "test"},
        ]
    )
    frames["access"] = pd.DataFrame(
        [{"chat_id": "1", "user_id": "1", "level": 1, "enabled": 1, "note": ""}]
    )
    frames["commands"] = pd.DataFrame(
        [
            {
                "command": "/start",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
                "note": "",
            }
        ]
    )
    frames["schedules"] = pd.DataFrame(
        [
            {
                "id": "SCHED-1",
                "enabled": 1,
                "job_type": "wallet",
                "schedule_type": "interval",
                "every_seconds": 60,
                "cron": "",
                "jitter_sec": 0,
                "max_runtime_sec": 0,
                "coalesce": 1,
            }
        ]
    )
    path = tmp_path / "with_job.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.snapshot is not None
    assert "wallet" in decision.snapshot.jobs
    assert RULE_EMPTY_JOBS not in {i.code for i in decision.contract_issues}


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


def _legacy_non_blocking_decision(
    *,
    publish_allowed: bool,
    snapshot: RulesSnapshotV2 | None,
    has_blocking: bool = False,
    build_error: str | None = None,
) -> SnapshotPublishDecision:
    return SnapshotPublishDecision(
        workbook_path="/tmp/rules.xlsx",
        policy_mode="legacy",
        validators_strict=False,
        contract_issues=(),
        has_blocking_contract=has_blocking,
        blocking_issue_codes=(),
        warning_count=0,
        info_count=0,
        error_count=0,
        snapshot_fingerprint="fp-test",
        snapshot=snapshot,
        publish_allowed=publish_allowed,
        build_error=build_error,
    )


def _minimal_snapshot() -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=MetaInfo(
            ruleset_version="test",
            updated_at=datetime(2026, 6, 3, 12, 0, 0),
            updated_by="test",
        ),
    )


def test_legacy_get_snapshot_v2_non_blocking_publish_allowed_false_with_snapshot(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy: contract non-blocking findings must not block when snapshot is available."""

    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    monkeypatch.delenv("RULES_CONTRACT_SHADOW", raising=False)
    invalidate_rules_v2_cache()

    snap = _minimal_snapshot()
    decision = _legacy_non_blocking_decision(
        publish_allowed=False,
        snapshot=snap,
        has_blocking=False,
    )

    with patch(
        "core.rules_provider.evaluate_snapshot_publish",
        return_value=decision,
    ):
        out = get_snapshot_v2(force_sync=True)

    assert out is snap


def test_legacy_get_snapshot_v2_blocking_still_rejects(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    invalidate_rules_v2_cache()

    snap = _minimal_snapshot()
    decision = SnapshotPublishDecision(
        workbook_path="/tmp/rules.xlsx",
        policy_mode="legacy",
        validators_strict=False,
        contract_issues=(),
        has_blocking_contract=True,
        blocking_issue_codes=(RULE_EMPTY_JOBS,),
        warning_count=0,
        info_count=0,
        error_count=0,
        snapshot_fingerprint="fp-test",
        snapshot=snap,
        publish_allowed=False,
    )

    with patch(
        "core.rules_provider.evaluate_snapshot_publish",
        return_value=decision,
    ):
        with pytest.raises(ContractPublishRejected) as ei:
            get_snapshot_v2(force_sync=True)

    assert ei.value.decision.has_blocking_contract is True
    assert RULE_EMPTY_JOBS in ei.value.decision.blocking_issue_codes


def test_strict_publish_allowed_false_still_rejects_without_blocking_codes(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict/infra gating unchanged: publish_allowed=False still blocks."""

    monkeypatch.setenv("RULES_CONTRACT_STRICT", "1")
    invalidate_rules_v2_cache()

    decision = SnapshotPublishDecision(
        workbook_path="/tmp/rules.xlsx",
        policy_mode="strict",
        validators_strict=True,
        contract_issues=(),
        has_blocking_contract=False,
        blocking_issue_codes=(),
        warning_count=0,
        info_count=0,
        error_count=0,
        snapshot_fingerprint=None,
        snapshot=None,
        publish_allowed=False,
        build_error="ValueError: injected build failure",
    )

    with patch(
        "core.rules_provider.evaluate_snapshot_publish",
        return_value=decision,
    ):
        with pytest.raises(ContractPublishRejected) as ei:
            get_snapshot_v2(force_sync=True)

    assert ei.value.decision.policy_mode == "strict"
    assert ei.value.decision.has_blocking_contract is False


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
