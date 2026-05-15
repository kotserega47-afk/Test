"""Unit tests for ``validation_snapshot`` (CONTRACT_V2 §7 / §9 / §13 / §18).

Each detector is exercised with a minimal hand-built ``RulesSnapshotV2``
so the test never goes through the legacy bridge — keeping the test
suite independent of any future bridge changes.

The bridge is exercised end-to-end in the C2 contract tests; this module
isolates the C3 validators.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.rules_v2.contract_errors import (
    RULE_DUPLICATE_ACCESS,
    RULE_DUPLICATE_COMMAND,
    RULE_DUPLICATE_JOB_PARAM,
    RULE_DUPLICATE_LIMIT,
    RULE_DUPLICATE_SCHEDULE_ID,
    RULE_DUPLICATE_THRESHOLD,
    RULE_EMPTY_JOBS,
    RULE_ORPHAN_JOB,
    RULE_UNSUPPORTED_JOB_PARAM,
    RULE_INVALID_THRESHOLD,
    RULE_INVALID_SCHEDULE,
    RULE_INVALID_SCHEDULE_TYPE,
    RULE_INVALID_SCOPE,
    RULE_NON_DETERMINISTIC_ORDER,
    RULE_ORPHAN_GROUP,
    RULE_ORPHAN_PARTNER,
    RULE_OVERLAPPING_EXCLUSION,
)
from core.rules_v2.contract_publish import (
    ContractValidationMode,
    evaluate_snapshot_publish,
)
from core.rules_v2.models import (
    AccessRule,
    CommandDef,
    ExclusionRule,
    JobDef,
    JobParam,
    LimitRule,
    MetaInfo,
    PartnerDef,
    PartnerGroupDef,
    PartnerGroupMember,
    ReportItem,
    ReportItemMember,
    RulesSnapshotV2,
    ScheduleRule,
    ThresholdRule,
)
from core.rules_v2.validation_issues import (
    ValidationIssue,
    ValidationSeverity,
    has_blocking_errors,
)
from core.rules_v2.validation_snapshot import validate_snapshot


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _meta() -> MetaInfo:
    return MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        updated_by="unit-test",
    )


def _job(key: str = "wallet") -> JobDef:
    return JobDef(job_key=key, display_name=key, enabled=True)


def _member(
    *,
    group_key: str = "group_a",
    partner_key: str = "p1",
    job_key: str = "wallet",
    enabled: bool = True,
) -> PartnerGroupMember:
    return PartnerGroupMember(
        group_key=group_key,
        partner_key=partner_key,
        job_key=job_key,
        enabled=enabled,
    )


def _empty_snapshot() -> RulesSnapshotV2:
    """Minimal snapshot with one job (avoids ``RULE_EMPTY_JOBS`` in unrelated tests)."""

    return RulesSnapshotV2(meta=_meta(), jobs={"wallet": _job("wallet")})


def _snapshot_without_jobs() -> RulesSnapshotV2:
    return RulesSnapshotV2(meta=_meta(), jobs={})


def _partner(key: str, code: str | None = None) -> PartnerDef:
    return PartnerDef(
        partner_key=key,
        partner_code=code,
        source_name=key,
        display_name=key,
    )


def _group(key: str) -> PartnerGroupDef:
    return PartnerGroupDef(group_key=key, display_name=key)


def _limit(
    *,
    rule_key: str,
    job_key: str = "wallet",
    scope_type: str = "partner",
    scope_key: str = "p1",
    metric_key: str = "daily_max_amount",
    method_key: str | None = None,
    enabled: bool = True,
) -> LimitRule:
    return LimitRule(
        rule_key=rule_key,
        job_key=job_key,
        scope_type=scope_type,
        scope_key=scope_key,
        metric_key=metric_key,
        method_key=method_key,
        limit_type="max",
        limit_value=1000.0,
        enabled=enabled,
    )


def _threshold(
    *,
    rule_key: str,
    job_key: str = "wallet",
    scope_type: str = "partner",
    scope_key: str = "p1",
    metric_key: str = "conversion_rate",
    threshold_min: float | None = 0.5,
    threshold_max: float | None = None,
    enabled: bool = True,
) -> ThresholdRule:
    return ThresholdRule(
        rule_key=rule_key,
        job_key=job_key,
        scope_type=scope_type,
        scope_key=scope_key,
        metric_key=metric_key,
        threshold_min=threshold_min,
        threshold_max=threshold_max,
        enabled=enabled,
    )


def _job_param(
    *,
    job_key: str = "wallet",
    scope_type: str = "global",
    scope_key: str = "*",
    param_key: str = "window_minutes",
    enabled: bool = True,
) -> JobParam:
    return JobParam(
        job_key=job_key,
        scope_type=scope_type,
        scope_key=scope_key,
        param_key=param_key,
        value_type="int",
        value=60,
        enabled=enabled,
    )


def _exclusion(
    *,
    key: str,
    job_key: str = "wallet",
    scope_key: str = "p1",
    start_dt: datetime,
    end_dt: datetime,
    enabled: bool = True,
) -> ExclusionRule:
    return ExclusionRule(
        exclusion_key=key,
        job_key=job_key,
        scope_type="partner",
        scope_key=scope_key,
        start_dt=start_dt,
        end_dt=end_dt,
        reason="test",
        enabled=enabled,
    )


def _command(*, key: str, text: str, enabled: bool = True) -> CommandDef:
    return CommandDef(
        command_key=key,
        command_text=text,
        job_key=None,
        display_name=text,
        enabled=enabled,
    )


def _schedule(
    *,
    schedule_key: str = "SCHED-1",
    job_key: str = "wallet",
    schedule_type: str = "interval",
    every_seconds: int | None = 60,
    cron_expr: str | None = None,
    enabled: bool = True,
) -> ScheduleRule:
    return ScheduleRule(
        schedule_key=schedule_key,
        job_key=job_key,
        schedule_type=schedule_type,
        every_seconds=every_seconds,
        cron_expr=cron_expr,
        enabled=enabled,
    )


def _codes(issues: list[ValidationIssue]) -> list[str]:
    return [i.code for i in issues]


def _by_code(issues: list[ValidationIssue], code: str) -> list[ValidationIssue]:
    return [i for i in issues if i.code == code]


# ---------------------------------------------------------------------------
# Empty / clean snapshot
# ---------------------------------------------------------------------------


def test_empty_jobs_emits_rule_empty_jobs_legacy_warn():
    snapshot = _snapshot_without_jobs()

    issues = validate_snapshot(snapshot, strict=False)

    empty = _by_code(issues, RULE_EMPTY_JOBS)
    assert len(empty) == 1
    assert empty[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(issues)


def test_empty_jobs_emits_rule_empty_jobs_strict_error():
    snapshot = _snapshot_without_jobs()

    issues = validate_snapshot(snapshot, strict=True)

    empty = _by_code(issues, RULE_EMPTY_JOBS)
    assert len(empty) == 1
    assert empty[0].severity == ValidationSeverity.ERROR
    assert has_blocking_errors(issues)


def test_snapshot_with_jobs_has_no_empty_jobs_issue():
    snapshot = _empty_snapshot()

    assert _by_code(validate_snapshot(snapshot), RULE_EMPTY_JOBS) == []
    assert _by_code(validate_snapshot(snapshot, strict=True), RULE_EMPTY_JOBS) == []


def test_empty_jobs_issue_is_deterministic():
    snapshot = _snapshot_without_jobs()

    a = validate_snapshot(snapshot, strict=True)
    b = validate_snapshot(snapshot, strict=True)

    assert a == b
    assert len(_by_code(a, RULE_EMPTY_JOBS)) == 1


# ---------------------------------------------------------------------------
# Orphan job_key (partner_group_members)
# ---------------------------------------------------------------------------


def test_orphan_job_on_enabled_membership_legacy_warn():
    snapshot = _empty_snapshot()
    snapshot.partner_groups = {"group_a": _group("group_a")}
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.partner_group_members = [
        _member(job_key="hourly"),
    ]

    issues = validate_snapshot(snapshot, strict=False)

    orphans = _by_code(issues, RULE_ORPHAN_JOB)
    assert len(orphans) == 1
    assert orphans[0].severity == ValidationSeverity.WARN
    assert orphans[0].sheet == "partner_groups"
    assert orphans[0].field == "job_key"
    assert orphans[0].details["job_key"] == "hourly"
    assert not has_blocking_errors(issues)


def test_orphan_job_on_enabled_membership_strict_error():
    snapshot = _empty_snapshot()
    snapshot.partner_groups = {"group_a": _group("group_a")}
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.partner_group_members = [
        _member(job_key="hourly"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    orphans = _by_code(issues, RULE_ORPHAN_JOB)
    assert len(orphans) == 1
    assert orphans[0].severity == ValidationSeverity.ERROR
    assert has_blocking_errors(issues)


def test_orphan_job_ignores_disabled_membership():
    snapshot = _empty_snapshot()
    snapshot.partner_groups = {"group_a": _group("group_a")}
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.partner_group_members = [
        _member(job_key="hourly", enabled=False),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_ORPHAN_JOB) == []


def test_valid_job_key_on_membership_no_orphan_issue():
    snapshot = _empty_snapshot()
    snapshot.jobs = {"wallet": _job("wallet"), "hourly": _job("hourly")}
    snapshot.partner_groups = {"group_a": _group("group_a")}
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.partner_group_members = [
        _member(job_key="wallet"),
        _member(job_key="hourly", group_key="group_b"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_ORPHAN_JOB) == []


def test_unsupported_job_param_legacy_warn():
    snapshot = _empty_snapshot()
    snapshot.job_params = [
        _job_param(job_key="hourly", param_key="not_in_catalog"),
    ]

    issues = validate_snapshot(snapshot, strict=False)

    bad = _by_code(issues, RULE_UNSUPPORTED_JOB_PARAM)
    assert len(bad) == 1
    assert bad[0].severity == ValidationSeverity.WARN
    assert bad[0].sheet == "job_params"
    assert bad[0].field == "key"
    assert bad[0].details["param_key"] == "not_in_catalog"
    assert "hide_inactive_rows" in bad[0].details["allowed_param_keys"]
    assert not has_blocking_errors(issues)


def test_unsupported_job_param_strict_error():
    snapshot = _empty_snapshot()
    snapshot.job_params = [
        _job_param(job_key="wallet", param_key="bogus_param"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    bad = _by_code(issues, RULE_UNSUPPORTED_JOB_PARAM)
    assert len(bad) == 1
    assert bad[0].severity == ValidationSeverity.ERROR
    assert has_blocking_errors(issues)


def test_unsupported_job_param_ignores_disabled():
    snapshot = _empty_snapshot()
    snapshot.job_params = [
        _job_param(job_key="hourly", param_key="bogus_param", enabled=False),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_UNSUPPORTED_JOB_PARAM) == []


def test_supported_job_param_no_issue():
    snapshot = _empty_snapshot()
    snapshot.job_params = [
        _job_param(job_key="wallet", param_key="window_minutes"),
        _job_param(job_key="hourly", param_key="hide_inactive_rows"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_UNSUPPORTED_JOB_PARAM) == []


def test_unknown_job_key_skips_unsupported_param_check():
    """Jobs without ``ALLOWED_JOB_PARAMS`` entry emit nothing here."""

    snapshot = _empty_snapshot()
    snapshot.jobs = {"custom": _job("custom")}
    snapshot.job_params = [
        _job_param(job_key="custom", param_key="anything"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_UNSUPPORTED_JOB_PARAM) == []


def test_unsupported_job_param_issue_is_deterministic():
    snapshot = _empty_snapshot()
    snapshot.job_params = [
        _job_param(job_key="hourly", param_key="z_bad"),
        _job_param(job_key="wallet", param_key="a_bad"),
    ]

    a = validate_snapshot(snapshot, strict=True)
    b = validate_snapshot(snapshot, strict=True)

    assert a == b
    assert len(_by_code(a, RULE_UNSUPPORTED_JOB_PARAM)) == 2


def test_orphan_job_issue_is_deterministic():
    snapshot = _empty_snapshot()
    snapshot.partner_groups = {
        "group_b": _group("group_b"),
        "group_a": _group("group_a"),
    }
    snapshot.partners = {"p1": _partner("p1"), "p2": _partner("p2")}
    snapshot.partner_group_members = [
        _member(group_key="group_b", partner_key="p2", job_key="hourly"),
        _member(group_key="group_a", partner_key="p1", job_key="hourly"),
    ]

    a = validate_snapshot(snapshot, strict=True)
    b = validate_snapshot(snapshot, strict=True)

    assert a == b
    assert len(_by_code(a, RULE_ORPHAN_JOB)) == 2


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_duplicate_limits_detected_legacy_is_warn():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.limit_rules = [
        _limit(rule_key="LIM-1"),
        _limit(rule_key="LIM-2"),  # same indexing key
    ]

    issues = validate_snapshot(snapshot)

    dups = _by_code(issues, RULE_DUPLICATE_LIMIT)
    assert len(dups) == 1
    assert dups[0].severity == ValidationSeverity.WARN  # legacy default
    assert not has_blocking_errors(issues)
    assert "LIM-1" in dups[0].details["rule_ids"]
    assert "LIM-2" in dups[0].details["rule_ids"]


def test_duplicate_limits_strict_is_error():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.limit_rules = [
        _limit(rule_key="LIM-1"),
        _limit(rule_key="LIM-2"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    dups = _by_code(issues, RULE_DUPLICATE_LIMIT)
    assert len(dups) == 1
    assert dups[0].severity == ValidationSeverity.ERROR
    assert has_blocking_errors(issues)


def test_duplicate_limits_ignores_disabled():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.limit_rules = [
        _limit(rule_key="LIM-1"),
        _limit(rule_key="LIM-2", enabled=False),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_DUPLICATE_LIMIT) == []


def test_duplicate_limits_method_specific_vs_generic_are_not_dupes():
    """method=None vs method='sbp' must NOT collide (different index keys)."""

    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.limit_rules = [
        _limit(rule_key="LIM-1", method_key=None),
        _limit(rule_key="LIM-2", method_key="sbp"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_DUPLICATE_LIMIT) == []


def test_invalid_threshold_both_bounds_missing_legacy_warn():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [
        _threshold(rule_key="THR-1", threshold_min=None, threshold_max=None),
    ]

    issues = validate_snapshot(snapshot, strict=False)

    bad = _by_code(issues, RULE_INVALID_THRESHOLD)
    assert len(bad) == 1
    assert bad[0].severity == ValidationSeverity.WARN
    assert bad[0].sheet == "thresholds_partner"
    assert bad[0].rule_id == "THR-1"
    assert not has_blocking_errors(issues)


def test_invalid_threshold_both_bounds_missing_strict_error():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [
        _threshold(rule_key="THR-1", threshold_min=None, threshold_max=None),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    bad = _by_code(issues, RULE_INVALID_THRESHOLD)
    assert len(bad) == 1
    assert bad[0].severity == ValidationSeverity.ERROR
    assert has_blocking_errors(issues)


def test_invalid_threshold_ignores_disabled():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [
        _threshold(
            rule_key="THR-1",
            threshold_min=None,
            threshold_max=None,
            enabled=False,
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_INVALID_THRESHOLD) == []


def test_threshold_min_only_no_issue():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [
        _threshold(rule_key="THR-1", threshold_min=0.5, threshold_max=None),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_INVALID_THRESHOLD) == []


def test_threshold_max_only_no_issue():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [
        _threshold(rule_key="THR-1", threshold_min=None, threshold_max=35.0),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_INVALID_THRESHOLD) == []


def test_threshold_both_bounds_present_no_issue():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [
        _threshold(rule_key="THR-1", threshold_min=0.5, threshold_max=35.0),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_INVALID_THRESHOLD) == []


def test_invalid_threshold_issue_is_deterministic():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [
        _threshold(rule_key="THR-B", threshold_min=None, threshold_max=None),
        _threshold(rule_key="THR-A", threshold_min=None, threshold_max=None),
    ]

    a = validate_snapshot(snapshot, strict=True)
    b = validate_snapshot(snapshot, strict=True)

    assert a == b
    assert len(_by_code(a, RULE_INVALID_THRESHOLD)) == 2


def test_duplicate_thresholds_detected():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.threshold_rules = [
        _threshold(rule_key="THR-1"),
        _threshold(rule_key="THR-2"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    dups = _by_code(issues, RULE_DUPLICATE_THRESHOLD)
    assert len(dups) == 1
    assert dups[0].severity == ValidationSeverity.ERROR


def test_duplicate_job_params_detected():
    snapshot = _empty_snapshot()
    snapshot.job_params = [
        _job_param(),
        _job_param(),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    dups = _by_code(issues, RULE_DUPLICATE_JOB_PARAM)
    assert len(dups) == 1
    assert dups[0].sheet == "job_params"


def test_duplicate_access_detected():
    snapshot = _empty_snapshot()
    snapshot.access_rules = [
        AccessRule(chat_id="-100200300", user_id="42", role_key="level_2"),
        AccessRule(chat_id="-100200300", user_id="42", role_key="level_3"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    dups = _by_code(issues, RULE_DUPLICATE_ACCESS)
    assert len(dups) == 1
    assert dups[0].severity == ValidationSeverity.ERROR


def test_duplicate_access_normalizes_chat_case_and_whitespace():
    snapshot = _empty_snapshot()
    snapshot.access_rules = [
        AccessRule(chat_id="PRIVATE", user_id="42", role_key="level_2"),
        AccessRule(chat_id=" private ", user_id="42", role_key="level_3"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert len(_by_code(issues, RULE_DUPLICATE_ACCESS)) == 1


def test_duplicate_commands_detected_by_indexed_text():
    """Two CommandDef rows that produce the same '/cmd' index text collide."""

    snapshot = _empty_snapshot()
    snapshot.commands = {
        "ping": _command(key="ping", text="ping"),
        # Different command_key, but same indexed text '/ping' (after
        # bridge-style normalization).
        "ping_alias": _command(key="ping_alias", text="/PING"),
    }

    issues = validate_snapshot(snapshot, strict=True)

    dups = _by_code(issues, RULE_DUPLICATE_COMMAND)
    assert len(dups) == 1
    assert dups[0].severity == ValidationSeverity.ERROR


def test_duplicate_schedules_by_schedule_key():
    snapshot = _empty_snapshot()
    snapshot.schedule_rules = [
        ScheduleRule(
            schedule_key="SCHED-1",
            job_key="wallet",
            schedule_type="interval",
            every_seconds=60,
        ),
        ScheduleRule(
            schedule_key="SCHED-1",
            job_key="hourly",
            schedule_type="interval",
            every_seconds=120,
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    dups = _by_code(issues, RULE_DUPLICATE_SCHEDULE_ID)
    assert len(dups) == 1


# ---------------------------------------------------------------------------
# Schedule validity (CONTRACT_V2 §3.7 / §18)
# ---------------------------------------------------------------------------


def test_invalid_schedule_type_on_enabled_row():
    snapshot = _empty_snapshot()
    snapshot.schedule_rules = [
        _schedule(schedule_type="every_seconds", every_seconds=60),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    bad = _by_code(issues, RULE_INVALID_SCHEDULE_TYPE)
    assert len(bad) == 1
    assert bad[0].sheet == "schedules"
    assert bad[0].rule_id == "SCHED-1"
    assert bad[0].field == "schedule_type"
    assert bad[0].severity == ValidationSeverity.ERROR
    assert _by_code(issues, RULE_INVALID_SCHEDULE) == []


def test_invalid_interval_every_seconds_zero():
    snapshot = _empty_snapshot()
    snapshot.schedule_rules = [_schedule(every_seconds=0)]

    issues = validate_snapshot(snapshot, strict=True)

    bad = _by_code(issues, RULE_INVALID_SCHEDULE)
    assert len(bad) == 1
    assert bad[0].field == "every_seconds"
    assert bad[0].severity == ValidationSeverity.ERROR


def test_invalid_interval_every_seconds_none():
    snapshot = _empty_snapshot()
    snapshot.schedule_rules = [_schedule(every_seconds=None)]

    issues = validate_snapshot(snapshot, strict=True)

    bad = _by_code(issues, RULE_INVALID_SCHEDULE)
    assert len(bad) == 1
    assert bad[0].details["every_seconds"] is None


def test_invalid_cron_empty_expression():
    snapshot = _empty_snapshot()
    snapshot.schedule_rules = [
        _schedule(schedule_type="cron", every_seconds=None, cron_expr="  "),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    bad = _by_code(issues, RULE_INVALID_SCHEDULE)
    assert len(bad) == 1
    assert bad[0].field == "cron"
    assert bad[0].severity == ValidationSeverity.ERROR


def test_disabled_invalid_schedule_is_ignored():
    snapshot = _empty_snapshot()
    snapshot.schedule_rules = [
        _schedule(schedule_type="bogus", enabled=False),
        _schedule(every_seconds=0, enabled=False),
        _schedule(schedule_type="cron", cron_expr="", enabled=False),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_INVALID_SCHEDULE_TYPE) == []
    assert _by_code(issues, RULE_INVALID_SCHEDULE) == []


def test_schedule_validity_strict_vs_legacy_downgrade():
    snapshot = _empty_snapshot()
    snapshot.schedule_rules = [_schedule(schedule_type="every_seconds", every_seconds=60)]

    legacy = validate_snapshot(snapshot, strict=False)
    strict = validate_snapshot(snapshot, strict=True)

    assert {(i.code, i.rule_id) for i in legacy} == {(i.code, i.rule_id) for i in strict}
    assert all(i.severity == ValidationSeverity.WARN for i in legacy)
    assert all(i.severity == ValidationSeverity.ERROR for i in strict)
    assert has_blocking_errors(strict)
    assert not has_blocking_errors(legacy)


def test_strict_publish_blocked_on_invalid_schedule_type(tmp_path: Path) -> None:
    """End-to-end: C4 publish gate blocks strict when C3 emits schedule errors."""

    import pandas as pd

    from core.rules_v2.contract_schema import SHEET_SCHEMAS

    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 2},
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
                "id": "SCHED-BAD",
                "enabled": 1,
                "job_type": "wallet",
                "schedule_type": "every_seconds",
                "every_seconds": 60,
                "cron": "",
                "jitter_sec": 0,
                "max_runtime_sec": 0,
                "coalesce": 1,
            }
        ]
    )

    path = tmp_path / "sched_invalid.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.publish_allowed is False
    assert RULE_INVALID_SCHEDULE_TYPE in decision.blocking_issue_codes
    legacy = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)
    assert legacy.publish_allowed is True
    assert not has_blocking_errors(legacy.contract_issues)


# ---------------------------------------------------------------------------
# Scope type validity (CONTRACT_V2 §3.4 / §5.6 / §18)
# ---------------------------------------------------------------------------


def test_invalid_scope_on_enabled_limit():
    snapshot = _empty_snapshot()
    snapshot.limit_rules = [_limit(rule_key="LIM-1", scope_type="bogus")]

    issues = validate_snapshot(snapshot, strict=True)

    bad = _by_code(issues, RULE_INVALID_SCOPE)
    assert len(bad) == 1
    assert bad[0].sheet == "wallet_limits"
    assert bad[0].rule_id == "LIM-1"
    assert bad[0].field == "scope_type"
    assert bad[0].severity == ValidationSeverity.ERROR
    assert _by_code(issues, RULE_ORPHAN_PARTNER) == []


def test_invalid_scope_on_threshold_job_param_exclusion():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [_threshold(rule_key="THR-1", scope_type="invalid")]
    snapshot.job_params = [_job_param(scope_type="nope")]
    snapshot.exclusion_rules = [
        ExclusionRule(
            exclusion_key="EXC-1",
            job_key="wallet",
            scope_type="bad_scope",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 2, tzinfo=timezone.utc),
            reason="test",
            enabled=True,
        )
    ]

    issues = validate_snapshot(snapshot, strict=True)

    bad = _by_code(issues, RULE_INVALID_SCOPE)
    assert len(bad) == 3
    assert {i.sheet for i in bad} == {
        "thresholds_partner",
        "job_params",
        "exclude_time",
    }


def test_disabled_invalid_scope_is_ignored():
    snapshot = _empty_snapshot()
    snapshot.limit_rules = [_limit(rule_key="LIM-1", scope_type="bogus", enabled=False)]
    snapshot.job_params = [_job_param(scope_type="nope", enabled=False)]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_INVALID_SCOPE) == []


def test_valid_scope_types_emit_nothing():
    snapshot = _empty_snapshot()
    snapshot.limit_rules = [
        _limit(rule_key="LIM-P", scope_type="partner"),
        _limit(rule_key="LIM-G", scope_type="group", scope_key="g1"),
    ]
    snapshot.job_params = [_job_param(scope_type="global", scope_key="*")]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_INVALID_SCOPE) == []


def test_strict_publish_blocked_on_invalid_scope(tmp_path: Path) -> None:
    import pandas as pd

    from core.rules_v2.contract_schema import SHEET_SCHEMAS

    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 2},
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
    frames["wallet_limits"] = pd.DataFrame(
        [
            {
                "id": "LIM-BAD",
                "enabled": 1,
                "analyzers": "wallet",
                "scope": "not_a_valid_scope",
                "scope_value": "P1",
                "limit_type": "daily_max_amount",
                "limit_value": 1000,
                "reason": "",
                "method": "",
                "comment": "",
            }
        ]
    )

    path = tmp_path / "scope_invalid.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.publish_allowed is False
    assert RULE_INVALID_SCOPE in decision.blocking_issue_codes


# ---------------------------------------------------------------------------
# Orphans
# ---------------------------------------------------------------------------


def test_orphan_partner_in_limit_rule():
    snapshot = _empty_snapshot()
    # No partners declared -> any partner scope_key is an orphan.
    snapshot.limit_rules = [_limit(rule_key="LIM-1", scope_key="ghost_partner")]

    issues = validate_snapshot(snapshot, strict=True)

    orphans = _by_code(issues, RULE_ORPHAN_PARTNER)
    assert len(orphans) == 1
    issue = orphans[0]
    assert issue.sheet == "wallet_limits"
    assert issue.rule_id == "LIM-1"
    assert issue.field == "scope_key"
    assert issue.details["scope_key"] == "ghost_partner"
    assert issue.severity == ValidationSeverity.ERROR


def test_orphan_partner_in_threshold_rule():
    snapshot = _empty_snapshot()
    snapshot.threshold_rules = [
        _threshold(rule_key="THR-1", scope_key="ghost_partner"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    orphans = _by_code(issues, RULE_ORPHAN_PARTNER)
    assert len(orphans) == 1
    assert orphans[0].sheet == "thresholds_partner"


def test_orphan_partner_in_exclusion_rule():
    snapshot = _empty_snapshot()
    snapshot.exclusion_rules = [
        _exclusion(
            key="EXC-1",
            scope_key="ghost",
            start_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert len(_by_code(issues, RULE_ORPHAN_PARTNER)) == 1


def test_orphan_group_in_job_param():
    snapshot = _empty_snapshot()
    snapshot.job_params = [
        _job_param(scope_type="group", scope_key="ghost_group"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    orphans = _by_code(issues, RULE_ORPHAN_GROUP)
    assert len(orphans) == 1
    assert orphans[0].sheet == "job_params"
    assert orphans[0].details["job_key"] == "wallet"
    assert orphans[0].details["param_key"] == "window_minutes"


def test_global_scope_is_never_orphan():
    snapshot = _empty_snapshot()
    snapshot.job_params = [
        _job_param(scope_type="global", scope_key="*"),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_ORPHAN_PARTNER) == []
    assert _by_code(issues, RULE_ORPHAN_GROUP) == []


def test_known_partner_is_not_orphan():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.limit_rules = [_limit(rule_key="LIM-1", scope_key="p1")]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_ORPHAN_PARTNER) == []


def test_disabled_rules_do_not_emit_orphan():
    snapshot = _empty_snapshot()
    snapshot.limit_rules = [
        _limit(rule_key="LIM-1", scope_key="ghost", enabled=False),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_ORPHAN_PARTNER) == []


def test_orphan_legacy_is_warn():
    snapshot = _empty_snapshot()
    snapshot.limit_rules = [_limit(rule_key="LIM-1", scope_key="ghost")]

    issues = validate_snapshot(snapshot)

    orphans = _by_code(issues, RULE_ORPHAN_PARTNER)
    assert len(orphans) == 1
    assert orphans[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(issues)


# ---------------------------------------------------------------------------
# Overlap detection (exclusions)
# ---------------------------------------------------------------------------


def test_exclusion_overlap_basic_pair():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.exclusion_rules = [
        _exclusion(
            key="EXC-A",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc),
        ),
        _exclusion(
            key="EXC-B",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc),
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    overlaps = _by_code(issues, RULE_OVERLAPPING_EXCLUSION)
    assert len(overlaps) == 1
    assert overlaps[0].severity == ValidationSeverity.ERROR


def test_exclusion_overlap_disjoint_pair_is_not_flagged():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.exclusion_rules = [
        _exclusion(
            key="EXC-A",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        ),
        _exclusion(
            key="EXC-B",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),  # touches, doesn't overlap (half-open)
            end_dt=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc),
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_OVERLAPPING_EXCLUSION) == []


def test_exclusion_overlap_three_way():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.exclusion_rules = [
        _exclusion(
            key="EXC-A",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc),
        ),
        _exclusion(
            key="EXC-B",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc),
        ),
        _exclusion(
            key="EXC-C",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc),
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    overlaps = _by_code(issues, RULE_OVERLAPPING_EXCLUSION)
    # Three pairs all overlap: A-B, A-C, B-C
    assert len(overlaps) == 3


def test_exclusion_overlap_different_partners_no_collision():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1"), "p2": _partner("p2")}
    snapshot.exclusion_rules = [
        _exclusion(
            key="EXC-A",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc),
        ),
        _exclusion(
            key="EXC-B",
            scope_key="p2",
            start_dt=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc),
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_OVERLAPPING_EXCLUSION) == []


def test_exclusion_overlap_legacy_is_warn():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.exclusion_rules = [
        _exclusion(
            key="EXC-A",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc),
        ),
        _exclusion(
            key="EXC-B",
            scope_key="p1",
            start_dt=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            end_dt=datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc),
        ),
    ]

    issues = validate_snapshot(snapshot)

    overlaps = _by_code(issues, RULE_OVERLAPPING_EXCLUSION)
    assert len(overlaps) == 1
    assert overlaps[0].severity == ValidationSeverity.WARN


# ---------------------------------------------------------------------------
# Non-deterministic order (CONTRACT_V2 §13)
# ---------------------------------------------------------------------------


def test_partner_code_collision_emits_warn():
    snapshot = _empty_snapshot()
    snapshot.partners = {
        "alpha_123": _partner("alpha_123", code="123"),
        "beta_123": _partner("beta_123", code="123"),
    }

    issues = validate_snapshot(snapshot, strict=True)

    nd = _by_code(issues, RULE_NON_DETERMINISTIC_ORDER)
    assert len(nd) == 1
    assert nd[0].severity == ValidationSeverity.WARN  # warn even in strict
    assert nd[0].details["partner_code"] == "123"
    assert sorted(nd[0].details["partner_keys"]) == ["alpha_123", "beta_123"]


def test_partner_code_unique_no_collision():
    snapshot = _empty_snapshot()
    snapshot.partners = {
        "alpha_123": _partner("alpha_123", code="123"),
        "beta_456": _partner("beta_456", code="456"),
    }

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_NON_DETERMINISTIC_ORDER) == []


def test_primary_group_ambiguity_emits_warn():
    snapshot = _empty_snapshot()
    snapshot.partner_groups = {
        "group_a": _group("group_a"),
        "group_b": _group("group_b"),
    }
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.partner_group_members = [
        PartnerGroupMember(
            group_key="group_a", partner_key="p1", job_key="wallet"
        ),
        PartnerGroupMember(
            group_key="group_b", partner_key="p1", job_key="wallet"
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    nd = _by_code(issues, RULE_NON_DETERMINISTIC_ORDER)
    assert len(nd) == 1
    assert nd[0].severity == ValidationSeverity.WARN
    assert nd[0].details["partner_key"] == "p1"
    assert nd[0].details["job_key"] == "wallet"
    assert sorted(nd[0].details["group_keys"]) == ["group_a", "group_b"]
    assert nd[0].details.get("disambiguation") == "no_explicit_metadata"


def test_single_membership_is_deterministic():
    snapshot = _empty_snapshot()
    snapshot.partner_groups = {"group_a": _group("group_a")}
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.partner_group_members = [
        PartnerGroupMember(
            group_key="group_a", partner_key="p1", job_key="wallet"
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_NON_DETERMINISTIC_ORDER) == []


# ---------------------------------------------------------------------------
# Orphan report item members
# ---------------------------------------------------------------------------


def test_orphan_parent_group_item_member_emits_warn():
    snapshot = _empty_snapshot()
    snapshot.report_items = [
        ReportItem(
            item_key="hourly.payout_method.gA.uni.1",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="gA",
            method_key="uni",
            display_name="UNI",
            sort_order=1,
        ),
    ]
    snapshot.report_item_members = [
        ReportItemMember(
            item_key="hourly.payout_method.gA.uni.1",
            member_type="parent_group_item",
            member_key="hourly.payout_group.gNON_EXISTENT",
            sort_order=1,
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    nd = _by_code(issues, RULE_NON_DETERMINISTIC_ORDER)
    assert len(nd) == 1
    assert nd[0].field == "member_key"
    assert "gNON_EXISTENT" in nd[0].details["member_key"]


def test_known_parent_group_item_member_is_silent():
    snapshot = _empty_snapshot()
    snapshot.report_items = [
        ReportItem(
            item_key="hourly.payout_group.gA",
            report_key="hourly",
            section_key="hourly.config_payouts",
            item_type="payout_group",
            source_key="gA",
            method_key=None,
            display_name="gA",
            sort_order=1,
        ),
        ReportItem(
            item_key="hourly.payout_method.gA.uni.1",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="gA",
            method_key="uni",
            display_name="UNI",
            sort_order=1,
        ),
    ]
    snapshot.report_item_members = [
        ReportItemMember(
            item_key="hourly.payout_method.gA.uni.1",
            member_type="parent_group_item",
            member_key="hourly.payout_group.gA",
            sort_order=1,
        ),
    ]

    issues = validate_snapshot(snapshot, strict=True)

    assert _by_code(issues, RULE_NON_DETERMINISTIC_ORDER) == []


# ---------------------------------------------------------------------------
# Determinism / immutability
# ---------------------------------------------------------------------------


def test_validator_does_not_mutate_snapshot():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.limit_rules = [_limit(rule_key="LIM-1"), _limit(rule_key="LIM-2")]

    # Capture an identity baseline by tracking object ids and key counts.
    partners_before = dict(snapshot.partners)
    limits_before = list(snapshot.limit_rules)

    validate_snapshot(snapshot, strict=True)

    assert snapshot.partners == partners_before
    assert snapshot.limit_rules == limits_before


def test_two_runs_return_equal_issue_sequences():
    snapshot = _empty_snapshot()
    snapshot.partners = {"p1": _partner("p1")}
    snapshot.limit_rules = [
        _limit(rule_key="LIM-A"),
        _limit(rule_key="LIM-B"),
        _limit(rule_key="LIM-C", scope_key="ghost"),
    ]

    a = validate_snapshot(snapshot, strict=True)
    b = validate_snapshot(snapshot, strict=True)

    assert [
        (i.code, i.sheet, i.rule_id, i.field) for i in a
    ] == [(i.code, i.sheet, i.rule_id, i.field) for i in b]


# ---------------------------------------------------------------------------
# Strict toggle: only severity differs; structure stays the same.
# ---------------------------------------------------------------------------


def test_strict_vs_legacy_same_codes_different_severity():
    snapshot = _empty_snapshot()
    snapshot.limit_rules = [
        _limit(rule_key="LIM-A", scope_key="ghost"),
        _limit(rule_key="LIM-B", scope_key="ghost"),
    ]

    legacy = validate_snapshot(snapshot, strict=False)
    strict = validate_snapshot(snapshot, strict=True)

    assert {(i.code, i.sheet, i.rule_id) for i in legacy} == {
        (i.code, i.sheet, i.rule_id) for i in strict
    }
    assert all(i.severity == ValidationSeverity.WARN for i in legacy)
    assert any(i.severity == ValidationSeverity.ERROR for i in strict)
    assert has_blocking_errors(strict)
    assert not has_blocking_errors(legacy)


# ---------------------------------------------------------------------------
# RULE_NON_DETERMINISTIC_ORDER severity is warn in both modes.
# ---------------------------------------------------------------------------


def test_non_deterministic_order_severity_does_not_escalate_in_strict():
    snapshot = _empty_snapshot()
    snapshot.partners = {
        "alpha_999": _partner("alpha_999", code="999"),
        "beta_999": _partner("beta_999", code="999"),
    }

    issues_legacy = validate_snapshot(snapshot, strict=False)
    issues_strict = validate_snapshot(snapshot, strict=True)

    nd_legacy = _by_code(issues_legacy, RULE_NON_DETERMINISTIC_ORDER)
    nd_strict = _by_code(issues_strict, RULE_NON_DETERMINISTIC_ORDER)
    assert len(nd_legacy) == 1
    assert len(nd_strict) == 1
    assert nd_legacy[0].severity == ValidationSeverity.WARN
    assert nd_strict[0].severity == ValidationSeverity.WARN
