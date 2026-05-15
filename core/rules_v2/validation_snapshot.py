"""Snapshot-level diagnostic validation (CONTRACT_V2 §7 / §9 / §13 / §18).

Stage 1 / C3 scope:

* Pure / read-only analysis of an already-built ``RulesSnapshotV2``.
* Emits ``ValidationIssue`` records ONLY.
* Builds **ephemeral validation indexes** (local dicts inside each check)
  to detect duplicates, orphans, collisions, and overlaps. These indexes
  are NEVER published, NEVER replace ``RulesIndexes``, and do not change
  runtime resolution.
* Does NOT mutate the snapshot, does not call into bridge / accessors /
  analyzers / reporters, does not "fix" or "deduplicate" anything.

What it detects (see CONTRACT_V2 §18 for codes):

1. Duplicate index keys for enabled rules:
   * ``RULE_DUPLICATE_LIMIT``
   * ``RULE_DUPLICATE_THRESHOLD``
   * ``RULE_DUPLICATE_JOB_PARAM``
   * ``RULE_DUPLICATE_ACCESS``
   * ``RULE_DUPLICATE_COMMAND``
   * ``RULE_DUPLICATE_SCHEDULE_ID``
2. Enabled schedule row validity (CONTRACT_V2 §3.7):
   * ``RULE_INVALID_SCHEDULE_TYPE`` — ``schedule_type`` ∉ {``interval``, ``cron``}
   * ``RULE_INVALID_SCHEDULE`` — interval without ``every_seconds`` > 0;
     cron without non-empty ``cron_expr``
3. Invalid ``scope_type`` on enabled scoped rules (CONTRACT_V2 §3.4 / §5.6):
   * ``RULE_INVALID_SCOPE`` — ``scope_type`` ∉ {``global``, ``group``, ``partner``}
     on ``wallet_limits``, ``thresholds_partner``, ``job_params``, ``exclude_time``
4. Orphan scope references in rules:
   * ``RULE_ORPHAN_PARTNER`` (limit / threshold / job_param / exclusion
     with ``scope_type=partner`` and ``scope_key`` not in
     ``snapshot.partners``)
   * ``RULE_ORPHAN_GROUP`` (same for ``scope_type=group``)
5. Orphan ``job_key`` on enabled memberships (CONTRACT_V2 §7.1 / §18):
   * ``RULE_ORPHAN_JOB`` — ``partner_group_members.job_key`` not in
     ``snapshot.jobs``
6. Empty job catalog after build (CONTRACT_V2 §7.1 / §18):
   * ``RULE_EMPTY_JOBS`` — ``snapshot.jobs`` is empty
7. Unsupported ``job_params`` keys (CONTRACT_V2 §3.8 / §18):
   * ``RULE_UNSUPPORTED_JOB_PARAM`` — enabled ``param_key`` not in
     ``ALLOWED_JOB_PARAMS`` for ``job_key`` (same registry as legacy validators)
8. Invalid threshold bounds (CONTRACT_V2 §3.3 / §18):
   * ``RULE_INVALID_THRESHOLD`` — enabled row with both ``threshold_min`` and
     ``threshold_max`` missing after bridge
9. Overlap of exclusion intervals (CONTRACT_V2 §8.4):
   * ``RULE_OVERLAPPING_EXCLUSION``
10. Non-deterministic collisions (CONTRACT_V2 §13, §8.3):
   * ``RULE_NON_DETERMINISTIC_ORDER`` — multiple memberships per
     (job, partner) without resolvable ``is_primary`` / ``group_priority``;
     conflicting ``is_primary``; tied minimum ``group_priority``;
     partner_code mapped to more than one partner_key; orphan
     ``parent_group_item`` refs from payout_method members pointing to
     non-existent items, etc.

Severity policy: by default, codes are emitted at the catalog default
(``error`` for duplicates / orphans / overlaps; ``warn`` for
non-determinism). In legacy mode (``strict=False`` — current default),
duplicates / orphans / overlaps are downgraded to ``warn`` to match
CONTRACT_V2 §18 "Legacy behavior" column. Strict mode (``strict=True``)
uses the catalog defaults.

Out of C3 scope (do NOT add here):

* No row-level workbook checks (those live in `validation_workbook` /
  C2 or row validators / C5+).
* No precedence changes (CONTRACT_V2 §16.3).
* No silent dedup / runtime index rewrites.
* No bridge / accessor / scheduler modification.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable

from .contract_errors import (
    RULE_DUPLICATE_ACCESS,
    RULE_DUPLICATE_COMMAND,
    RULE_DUPLICATE_JOB_PARAM,
    RULE_DUPLICATE_LIMIT,
    RULE_DUPLICATE_SCHEDULE_ID,
    RULE_DUPLICATE_THRESHOLD,
    RULE_EMPTY_JOBS,
    RULE_INVALID_SCHEDULE,
    RULE_INVALID_SCHEDULE_TYPE,
    RULE_INVALID_THRESHOLD,
    RULE_INVALID_SCOPE,
    RULE_NON_DETERMINISTIC_ORDER,
    RULE_ORPHAN_GROUP,
    RULE_ORPHAN_JOB,
    RULE_ORPHAN_PARTNER,
    RULE_OVERLAPPING_EXCLUSION,
    RULE_UNSUPPORTED_JOB_PARAM,
    make_issue,
)
from .constants import ALLOWED_JOB_PARAMS
from .models import (
    AccessRule,
    CommandDef,
    ExclusionRule,
    JobParam,
    LimitRule,
    PartnerGroupMember,
    RulesSnapshotV2,
    ScheduleRule,
    ThresholdRule,
)
from .validation_issues import ValidationIssue, ValidationSeverity


# ---------------------------------------------------------------------------
# Severity policy (legacy vs strict)
# ---------------------------------------------------------------------------

# Codes that CONTRACT_V2 §18 downgrades from ``error`` to ``warn`` in legacy
# mode. Strict mode keeps catalog defaults.
_LEGACY_DOWNGRADE_TO_WARN: frozenset[str] = frozenset(
    {
        RULE_DUPLICATE_LIMIT,
        RULE_DUPLICATE_THRESHOLD,
        RULE_DUPLICATE_JOB_PARAM,
        RULE_DUPLICATE_ACCESS,
        RULE_DUPLICATE_COMMAND,
        RULE_DUPLICATE_SCHEDULE_ID,
        RULE_INVALID_SCHEDULE,
        RULE_INVALID_SCHEDULE_TYPE,
        RULE_ORPHAN_PARTNER,
        RULE_ORPHAN_GROUP,
        RULE_ORPHAN_JOB,
        RULE_EMPTY_JOBS,
        RULE_UNSUPPORTED_JOB_PARAM,
        RULE_INVALID_THRESHOLD,
        RULE_OVERLAPPING_EXCLUSION,
    }
)

# CONTRACT_V2 §3.7 — allowed ``schedule_type`` values on enabled rows.
_VALID_SCHEDULE_TYPES: frozenset[str] = frozenset({"interval", "cron"})

# CONTRACT_V2 §3.4 / §5.6 — allowed ``scope_type`` values on scoped snapshot rows.
_VALID_SCOPE_TYPES: frozenset[str] = frozenset({"global", "group", "partner"})


def _severity_for(code: str, *, strict: bool) -> ValidationSeverity | None:
    """Return explicit severity override for ``code`` under current mode.

    Returns ``None`` to indicate "use the catalog default" (which
    ``make_issue`` will resolve via ``default_severity``).
    """

    if strict:
        return None
    if code in _LEGACY_DOWNGRADE_TO_WARN:
        return ValidationSeverity.WARN
    return None


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def validate_snapshot(
    snapshot: RulesSnapshotV2,
    *,
    strict: bool = False,
) -> list[ValidationIssue]:
    """Run all C3 snapshot-level checks against ``snapshot``.

    Parameters
    ----------
    snapshot:
        An already-built ``RulesSnapshotV2`` (typically from
        ``bridge_legacy.build_snapshot_v2_from_legacy``). Must not be
        mutated by callers during validation.
    strict:
        Severity policy. ``False`` (default, legacy): duplicates /
        orphans / overlaps are emitted at ``warn`` per CONTRACT_V2 §18
        "Legacy behavior". ``True``: catalog defaults (``error``).

    Returns
    -------
    list[ValidationIssue]
        Deterministic list of structured findings. Empty when the
        snapshot is free of duplicates, orphans, collisions, and
        overlaps within the C3 scope.
    """

    issues: list[ValidationIssue] = []

    issues.extend(_check_empty_jobs(snapshot, strict=strict))
    issues.extend(_check_orphan_job_refs(snapshot, strict=strict))
    issues.extend(_check_duplicate_limits(snapshot, strict=strict))
    issues.extend(_check_duplicate_thresholds(snapshot, strict=strict))
    issues.extend(_check_invalid_threshold_bounds(snapshot, strict=strict))
    issues.extend(_check_duplicate_job_params(snapshot, strict=strict))
    issues.extend(_check_unsupported_job_params(snapshot, strict=strict))
    issues.extend(_check_duplicate_access(snapshot, strict=strict))
    issues.extend(_check_duplicate_commands(snapshot, strict=strict))
    issues.extend(_check_duplicate_schedules(snapshot, strict=strict))
    issues.extend(_check_schedule_validity(snapshot, strict=strict))
    issues.extend(_check_invalid_scope_types(snapshot, strict=strict))

    issues.extend(_check_orphan_scope_refs_in_limits(snapshot, strict=strict))
    issues.extend(_check_orphan_scope_refs_in_thresholds(snapshot, strict=strict))
    issues.extend(_check_orphan_scope_refs_in_job_params(snapshot, strict=strict))
    issues.extend(_check_orphan_scope_refs_in_exclusions(snapshot, strict=strict))

    issues.extend(_check_exclusion_overlaps(snapshot, strict=strict))

    issues.extend(_check_partner_code_collisions(snapshot))
    issues.extend(_check_primary_group_ambiguity(snapshot))
    issues.extend(_check_orphan_report_item_members(snapshot))

    return issues


# ---------------------------------------------------------------------------
# Job catalog (CONTRACT_V2 §7.1 / §18)
# ---------------------------------------------------------------------------


def _check_empty_jobs(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Emit ``RULE_EMPTY_JOBS`` when ``snapshot.jobs`` has no entries."""

    if snapshot.jobs:
        return []
    return [
        make_issue(
            RULE_EMPTY_JOBS,
            "No jobs loaded in snapshot after build",
            severity=_severity_for(RULE_EMPTY_JOBS, strict=strict),
        )
    ]


def _check_orphan_job_refs(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Emit ``RULE_ORPHAN_JOB`` for enabled memberships with unknown ``job_key``."""

    issues: list[ValidationIssue] = []
    for member in sorted(
        snapshot.partner_group_members,
        key=lambda m: (m.job_key, m.partner_key, m.group_key),
    ):
        if not member.enabled:
            continue
        if member.job_key in snapshot.jobs:
            continue
        issues.append(
            make_issue(
                RULE_ORPHAN_JOB,
                (
                    f"Unknown job '{member.job_key}' referenced from "
                    f"partner_group_members"
                ),
                severity=_severity_for(RULE_ORPHAN_JOB, strict=strict),
                sheet="partner_groups",
                field="job_key",
                details={
                    "job_key": member.job_key,
                    "group_key": member.group_key,
                    "partner_key": member.partner_key,
                },
            )
        )
    return issues


def _check_unsupported_job_params(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Emit ``RULE_UNSUPPORTED_JOB_PARAM`` for unknown enabled ``param_key`` values.

    Uses ``ALLOWED_JOB_PARAMS`` from ``constants`` — the same registry as
    ``core.rules_v2.validators``. Jobs without a catalog entry are skipped
    (no issue from this check; legacy ``No param registry`` warn is out of
    scope for this code).
    """

    issues: list[ValidationIssue] = []
    for param in sorted(
        snapshot.job_params,
        key=lambda p: (p.job_key, p.scope_type, p.scope_key, p.param_key),
    ):
        if not param.enabled:
            continue
        allowed = ALLOWED_JOB_PARAMS.get(param.job_key)
        if allowed is None:
            continue
        if param.param_key in allowed:
            continue
        issues.append(
            make_issue(
                RULE_UNSUPPORTED_JOB_PARAM,
                (
                    f"Unsupported param_key '{param.param_key}' "
                    f"for job '{param.job_key}'"
                ),
                severity=_severity_for(RULE_UNSUPPORTED_JOB_PARAM, strict=strict),
                sheet="job_params",
                field="key",
                details={
                    "job_key": param.job_key,
                    "param_key": param.param_key,
                    "allowed_param_keys": sorted(allowed.keys()),
                },
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Duplicate detection (CONTRACT_V2 §9 / §18)
# ---------------------------------------------------------------------------


def _check_duplicate_limits(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Limits: key = (job_key, scope_type, scope_key, metric_key, method_key)."""

    groups: dict[
        tuple[str, str, str, str, str | None], list[LimitRule]
    ] = defaultdict(list)
    for rule in snapshot.limit_rules:
        if not rule.enabled:
            continue
        key = (
            rule.job_key,
            rule.scope_type,
            rule.scope_key,
            rule.metric_key,
            rule.method_key,
        )
        groups[key].append(rule)

    issues: list[ValidationIssue] = []
    for key, rules in _sorted_dup_groups(groups):
        issues.append(
            _make_duplicate_issue(
                RULE_DUPLICATE_LIMIT,
                "wallet_limits",
                key,
                [r.rule_key for r in rules],
                strict=strict,
            )
        )
    return issues


def _check_duplicate_thresholds(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Thresholds: key = (job_key, scope_type, scope_key, metric_key)."""

    groups: dict[tuple[str, str, str, str], list[ThresholdRule]] = defaultdict(list)
    for rule in snapshot.threshold_rules:
        if not rule.enabled:
            continue
        key = (rule.job_key, rule.scope_type, rule.scope_key, rule.metric_key)
        groups[key].append(rule)

    issues: list[ValidationIssue] = []
    for key, rules in _sorted_dup_groups(groups):
        issues.append(
            _make_duplicate_issue(
                RULE_DUPLICATE_THRESHOLD,
                "thresholds_partner",
                key,
                [r.rule_key for r in rules],
                strict=strict,
            )
        )
    return issues


def _check_invalid_threshold_bounds(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Emit ``RULE_INVALID_THRESHOLD`` when both bounds are missing on enabled rows."""

    issues: list[ValidationIssue] = []
    for rule in sorted(snapshot.threshold_rules, key=lambda r: r.rule_key):
        if not rule.enabled:
            continue
        if rule.threshold_min is not None or rule.threshold_max is not None:
            continue
        issues.append(
            make_issue(
                RULE_INVALID_THRESHOLD,
                (
                    "Threshold rule must have threshold_min or threshold_max"
                ),
                severity=_severity_for(RULE_INVALID_THRESHOLD, strict=strict),
                sheet="thresholds_partner",
                rule_id=rule.rule_key,
                field="threshold_min",
                details={
                    "threshold_min": rule.threshold_min,
                    "threshold_max": rule.threshold_max,
                },
            )
        )
    return issues


def _check_duplicate_job_params(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Job params: key = (job_key, scope_type, scope_key, param_key)."""

    groups: dict[tuple[str, str, str, str], list[JobParam]] = defaultdict(list)
    for param in snapshot.job_params:
        if not param.enabled:
            continue
        key = (param.job_key, param.scope_type, param.scope_key, param.param_key)
        groups[key].append(param)

    issues: list[ValidationIssue] = []
    for key, params in _sorted_dup_groups(groups):
        # job_params has no per-row rule_key in the model; surface scope
        # tuple in details for ops triage.
        issues.append(
            _make_duplicate_issue(
                RULE_DUPLICATE_JOB_PARAM,
                "job_params",
                key,
                rule_ids=None,
                strict=strict,
            )
        )
    return issues


def _check_duplicate_access(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Access: key = (chat_id_norm, user_id_norm).

    Normalization matches the index builder behavior (see
    ``indexes.build_indexes``): chat trimmed-and-lowered; user_id
    coerced to int when parseable, otherwise kept as the stripped
    string for comparison so unparseable rows are still de-duplicated
    against themselves.
    """

    groups: dict[tuple[str, object], list[AccessRule]] = defaultdict(list)
    for rule in snapshot.access_rules:
        if not rule.enabled:
            continue
        chat_norm = str(rule.chat_id).strip().lower()
        user_raw = str(rule.user_id).strip()
        try:
            user_norm: object = int(user_raw)
        except (TypeError, ValueError):
            user_norm = user_raw
        groups[(chat_norm, user_norm)].append(rule)

    issues: list[ValidationIssue] = []
    for key, rules in _sorted_dup_groups(groups):
        issues.append(
            _make_duplicate_issue(
                RULE_DUPLICATE_ACCESS,
                "access",
                key,
                rule_ids=None,
                strict=strict,
            )
        )
    return issues


def _check_duplicate_commands(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Commands: key = normalized ``/cmd`` text used by the runtime index.

    ``snapshot.commands`` is already a dict keyed by ``command_key``,
    so a "same command_key" collision is collapsed before we see it.
    What we *can* detect here is two distinct ``CommandDef`` entries
    that produce the same indexed ``commands_by_text`` key — i.e. the
    same ``command_text`` after the bridge-style ``/`` prefix + lower.
    """

    groups: dict[str, list[CommandDef]] = defaultdict(list)
    for command in snapshot.commands.values():
        if not command.enabled:
            continue
        text = str(command.command_text or "").strip()
        if not text:
            continue
        if not text.startswith("/"):
            text = "/" + text
        groups[text.lower()].append(command)

    issues: list[ValidationIssue] = []
    for key, defs in _sorted_dup_groups(groups):
        issues.append(
            _make_duplicate_issue(
                RULE_DUPLICATE_COMMAND,
                "commands",
                key,
                [d.command_key for d in defs],
                strict=strict,
            )
        )
    return issues


def _check_duplicate_schedules(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Schedules: duplicate ``schedule_key`` among enabled rules."""

    groups: dict[str, list[ScheduleRule]] = defaultdict(list)
    for rule in snapshot.schedule_rules:
        if not rule.enabled:
            continue
        groups[rule.schedule_key].append(rule)

    issues: list[ValidationIssue] = []
    for key, rules in _sorted_dup_groups(groups):
        issues.append(
            _make_duplicate_issue(
                RULE_DUPLICATE_SCHEDULE_ID,
                "schedules",
                key,
                [r.schedule_key for r in rules],
                strict=strict,
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Schedule validity (CONTRACT_V2 §3.7 / §18)
# ---------------------------------------------------------------------------


def _check_schedule_validity(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Validate enabled schedule rows against §3.7 (read-only diagnostics)."""

    issues: list[ValidationIssue] = []
    for rule in sorted(snapshot.schedule_rules, key=lambda r: r.schedule_key):
        if not rule.enabled:
            continue

        schedule_type = str(rule.schedule_type or "").strip().lower()
        if schedule_type not in _VALID_SCHEDULE_TYPES:
            issues.append(
                make_issue(
                    RULE_INVALID_SCHEDULE_TYPE,
                    (
                        f"Invalid schedule_type '{rule.schedule_type}' "
                        f"for schedule '{rule.schedule_key}' "
                        f"(allowed: interval, cron)"
                    ),
                    severity=_severity_for(RULE_INVALID_SCHEDULE_TYPE, strict=strict),
                    sheet="schedules",
                    rule_id=rule.schedule_key,
                    field="schedule_type",
                    details={"schedule_type": rule.schedule_type},
                )
            )
            continue

        if schedule_type == "interval":
            every = rule.every_seconds
            if every is None or every <= 0:
                issues.append(
                    make_issue(
                        RULE_INVALID_SCHEDULE,
                        (
                            f"Interval schedule '{rule.schedule_key}' requires "
                            f"every_seconds > 0 (got {every!r})"
                        ),
                        severity=_severity_for(RULE_INVALID_SCHEDULE, strict=strict),
                        sheet="schedules",
                        rule_id=rule.schedule_key,
                        field="every_seconds",
                        details={"every_seconds": every},
                    )
                )
            continue

        # schedule_type == "cron"
        cron = (rule.cron_expr or "").strip()
        if not cron:
            issues.append(
                make_issue(
                    RULE_INVALID_SCHEDULE,
                    f"Cron schedule '{rule.schedule_key}' requires non-empty cron",
                    severity=_severity_for(RULE_INVALID_SCHEDULE, strict=strict),
                    sheet="schedules",
                    rule_id=rule.schedule_key,
                    field="cron",
                    details={"cron_expr": rule.cron_expr},
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Scope type validity (CONTRACT_V2 §3.4 / §5.6 / §18)
# ---------------------------------------------------------------------------


def _normalized_scope_type(scope_type: str) -> str:
    """Lowercase trim for comparison only — does not mutate snapshot rows."""

    return str(scope_type or "").strip().lower()


def _is_valid_scope_type(scope_type: str) -> bool:
    return _normalized_scope_type(scope_type) in _VALID_SCOPE_TYPES


def _check_invalid_scope_types(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Emit ``RULE_INVALID_SCOPE`` for enabled rows with unknown ``scope_type``."""

    issues: list[ValidationIssue] = []

    for rule in sorted(snapshot.limit_rules, key=lambda r: r.rule_key):
        if not rule.enabled:
            continue
        if _is_valid_scope_type(rule.scope_type):
            continue
        issues.append(
            _make_invalid_scope_issue(
                rule.scope_type,
                sheet="wallet_limits",
                rule_id=rule.rule_key,
                strict=strict,
            )
        )

    for rule in sorted(snapshot.threshold_rules, key=lambda r: r.rule_key):
        if not rule.enabled:
            continue
        if _is_valid_scope_type(rule.scope_type):
            continue
        issues.append(
            _make_invalid_scope_issue(
                rule.scope_type,
                sheet="thresholds_partner",
                rule_id=rule.rule_key,
                strict=strict,
            )
        )

    for param in sorted(
        snapshot.job_params,
        key=lambda p: (p.job_key, p.scope_type, p.scope_key, p.param_key),
    ):
        if not param.enabled:
            continue
        if _is_valid_scope_type(param.scope_type):
            continue
        issues.append(
            _make_invalid_scope_issue(
                param.scope_type,
                sheet="job_params",
                rule_id=None,
                strict=strict,
                extra_details={
                    "job_key": param.job_key,
                    "param_key": param.param_key,
                },
            )
        )

    for rule in sorted(snapshot.exclusion_rules, key=lambda r: r.exclusion_key):
        if not rule.enabled:
            continue
        if _is_valid_scope_type(rule.scope_type):
            continue
        issues.append(
            _make_invalid_scope_issue(
                rule.scope_type,
                sheet="exclude_time",
                rule_id=rule.exclusion_key,
                strict=strict,
            )
        )

    return issues


def _make_invalid_scope_issue(
    scope_type: str,
    *,
    sheet: str,
    rule_id: str | None,
    strict: bool,
    extra_details: dict[str, object] | None = None,
) -> ValidationIssue:
    details: dict[str, object] = {"scope_type": scope_type}
    if extra_details:
        details.update(extra_details)
    return make_issue(
        RULE_INVALID_SCOPE,
        (
            f"Invalid scope_type '{scope_type}' on '{sheet}' "
            f"(allowed: global, group, partner)"
        ),
        severity=_severity_for(RULE_INVALID_SCOPE, strict=strict),
        sheet=sheet,
        rule_id=rule_id,
        field="scope_type",
        details=details,
    )


# ---------------------------------------------------------------------------
# Orphan reference detection
# ---------------------------------------------------------------------------


def _check_orphan_scope_refs_in_limits(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule in snapshot.limit_rules:
        if not rule.enabled:
            continue
        issues.extend(
            _orphan_scope_ref(
                snapshot,
                sheet="wallet_limits",
                rule_id=rule.rule_key,
                scope_type=rule.scope_type,
                scope_key=rule.scope_key,
                strict=strict,
            )
        )
    return issues


def _check_orphan_scope_refs_in_thresholds(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule in snapshot.threshold_rules:
        if not rule.enabled:
            continue
        issues.extend(
            _orphan_scope_ref(
                snapshot,
                sheet="thresholds_partner",
                rule_id=rule.rule_key,
                scope_type=rule.scope_type,
                scope_key=rule.scope_key,
                strict=strict,
            )
        )
    return issues


def _check_orphan_scope_refs_in_job_params(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for param in snapshot.job_params:
        if not param.enabled:
            continue
        issues.extend(
            _orphan_scope_ref(
                snapshot,
                sheet="job_params",
                rule_id=None,
                scope_type=param.scope_type,
                scope_key=param.scope_key,
                strict=strict,
                extra_details={
                    "job_key": param.job_key,
                    "param_key": param.param_key,
                },
            )
        )
    return issues


def _check_orphan_scope_refs_in_exclusions(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule in snapshot.exclusion_rules:
        if not rule.enabled:
            continue
        issues.extend(
            _orphan_scope_ref(
                snapshot,
                sheet="exclude_time",
                rule_id=rule.exclusion_key,
                scope_type=rule.scope_type,
                scope_key=rule.scope_key,
                strict=strict,
            )
        )
    return issues


def _orphan_scope_ref(
    snapshot: RulesSnapshotV2,
    *,
    sheet: str,
    rule_id: str | None,
    scope_type: str,
    scope_key: str,
    strict: bool,
    extra_details: dict[str, object] | None = None,
) -> list[ValidationIssue]:
    """Return at most one orphan issue for a single rule's scope reference."""

    if scope_type == "partner":
        if scope_key in snapshot.partners:
            return []
        details: dict[str, object] = {"scope_key": scope_key}
        if extra_details:
            details.update(extra_details)
        return [
            make_issue(
                RULE_ORPHAN_PARTNER,
                f"Unknown partner '{scope_key}' referenced from {sheet}",
                severity=_severity_for(RULE_ORPHAN_PARTNER, strict=strict),
                sheet=sheet,
                rule_id=rule_id,
                field="scope_key",
                details=details,
            )
        ]
    if scope_type == "group":
        if scope_key in snapshot.partner_groups:
            return []
        details = {"scope_key": scope_key}
        if extra_details:
            details.update(extra_details)
        return [
            make_issue(
                RULE_ORPHAN_GROUP,
                f"Unknown group '{scope_key}' referenced from {sheet}",
                severity=_severity_for(RULE_ORPHAN_GROUP, strict=strict),
                sheet=sheet,
                rule_id=rule_id,
                field="scope_key",
                details=details,
            )
        ]
    # global scope or anything else — out of orphan-detection scope here.
    return []


# ---------------------------------------------------------------------------
# Exclusion overlaps (CONTRACT_V2 §8.4)
# ---------------------------------------------------------------------------


def _check_exclusion_overlaps(
    snapshot: RulesSnapshotV2, *, strict: bool
) -> list[ValidationIssue]:
    """Pairwise overlap on enabled exclusions sharing (job, scope_type, scope_key).

    One issue per overlapping pair. Iteration uses sorted ``(start_dt,
    end_dt, exclusion_key)`` order so output is deterministic; inner
    loop breaks once the next start is beyond the current end (since
    starts are non-decreasing).
    """

    groups: dict[tuple[str, str, str], list[ExclusionRule]] = defaultdict(list)
    for rule in snapshot.exclusion_rules:
        if not rule.enabled:
            continue
        groups[(rule.job_key, rule.scope_type, rule.scope_key)].append(rule)

    issues: list[ValidationIssue] = []
    for group_key in sorted(groups.keys()):
        rules = sorted(
            groups[group_key],
            key=lambda r: (r.start_dt, r.end_dt, r.exclusion_key),
        )
        n = len(rules)
        for i in range(n):
            a = rules[i]
            for j in range(i + 1, n):
                b = rules[j]
                if b.start_dt >= a.end_dt:
                    # Sorted-by-start invariant: nothing after b can
                    # start before a.end_dt either.
                    break
                # Half-open overlap test: a and b share at least one
                # instant.
                issues.append(
                    make_issue(
                        RULE_OVERLAPPING_EXCLUSION,
                        (
                            f"Overlapping exclusions "
                            f"'{a.exclusion_key}' and '{b.exclusion_key}' "
                            f"for {group_key[1]}={group_key[2]!r} "
                            f"in job '{group_key[0]}'"
                        ),
                        severity=_severity_for(
                            RULE_OVERLAPPING_EXCLUSION, strict=strict
                        ),
                        sheet="exclude_time",
                        rule_id=a.exclusion_key,
                        details={
                            "other_rule_id": b.exclusion_key,
                            "job_key": group_key[0],
                            "scope_type": group_key[1],
                            "scope_key": group_key[2],
                            "a_start": a.start_dt.isoformat(),
                            "a_end": a.end_dt.isoformat(),
                            "b_start": b.start_dt.isoformat(),
                            "b_end": b.end_dt.isoformat(),
                        },
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# Non-deterministic collisions (CONTRACT_V2 §13)
# ---------------------------------------------------------------------------


def _check_partner_code_collisions(
    snapshot: RulesSnapshotV2,
) -> list[ValidationIssue]:
    """One ``partner_code`` mapped to more than one ``partner_key``.

    This is what makes ``partners_by_code`` ambiguous and CONTRACT_V2
    §13 deprecates "row-order wins". WARN-only by design (§13).
    """

    groups: dict[str, list[str]] = defaultdict(list)
    for partner_key, partner in snapshot.partners.items():
        if not partner.enabled:
            continue
        code = partner.partner_code
        if code is None:
            continue
        groups[str(code)].append(partner_key)

    issues: list[ValidationIssue] = []
    for code in sorted(k for k, v in groups.items() if len(v) > 1):
        partner_keys = sorted(groups[code])
        issues.append(
            make_issue(
                RULE_NON_DETERMINISTIC_ORDER,
                (
                    f"partner_code '{code}' maps to multiple partner_keys: "
                    f"{partner_keys}"
                ),
                sheet="partners (derived)",
                field="partner_code",
                details={"partner_code": code, "partner_keys": partner_keys},
            )
        )
    return issues


def _primary_group_disambiguation_resolved(members: list[PartnerGroupMember]) -> bool:
    """Whether ``(job, partner)`` multi-membership is unambiguous per §8.3."""

    if len(members) <= 1:
        return True

    primaries = [m for m in members if m.is_primary]
    if len(primaries) == 1:
        return True
    if len(primaries) > 1:
        return False

    with_pri = [m for m in members if m.group_priority is not None]
    if not with_pri:
        return False

    min_p = min(m.group_priority for m in with_pri)
    winners = [m for m in with_pri if m.group_priority == min_p]
    return len(winners) == 1


def _check_primary_group_ambiguity(
    snapshot: RulesSnapshotV2,
) -> list[ValidationIssue]:
    """Multiple memberships per ``(job_key, partner_key)`` without resolution.

    Emits ``RULE_NON_DETERMINISTIC_ORDER`` (warn) when:

    * more than one enabled membership shares ``(job_key, partner_key)``
      and there is **no** disambiguation: no ``is_primary=True`` and all
      ``group_priority`` are ``None`` (legacy row-order dependence);
    * **two or more** ``is_primary=True`` on the same ``(job, partner)``;
    * **tie** on the minimum ``group_priority`` among non-``None`` values.

    Skips the issue when exactly one membership has ``is_primary=True``, or
    when a **unique** minimum ``group_priority`` picks a single winner among
    rows that set ``group_priority``. Runtime ordering for accessors matches
    these rules (see ``_order_partner_group_memberships``).
    """

    groups: dict[tuple[str, str], list[PartnerGroupMember]] = defaultdict(list)
    for member in snapshot.partner_group_members:
        if not member.enabled:
            continue
        groups[(member.job_key, member.partner_key)].append(member)

    issues: list[ValidationIssue] = []
    for (job_key, partner_key) in sorted(
        k for k, v in groups.items() if len(v) > 1
    ):
        members = groups[(job_key, partner_key)]
        if _primary_group_disambiguation_resolved(members):
            continue

        group_keys = sorted({m.group_key for m in members})
        primaries = [m for m in members if m.is_primary]
        with_pri = [m for m in members if m.group_priority is not None]

        if len(primaries) > 1:
            kind = "multiple_is_primary"
            msg = (
                f"Partner '{partner_key}' has multiple memberships with "
                f"is_primary=true for job '{job_key}': "
                f"{sorted({m.group_key for m in primaries})}"
            )
        elif with_pri:
            min_p = min(m.group_priority for m in with_pri)
            winners = [m for m in with_pri if m.group_priority == min_p]
            kind = "tied_group_priority"
            msg = (
                f"Partner '{partner_key}' has tied minimum group_priority={min_p} "
                f"for job '{job_key}': {sorted(m.group_key for m in winners)}"
            )
        else:
            kind = "no_explicit_metadata"
            msg = (
                f"Partner '{partner_key}' has multiple group memberships "
                f"for job '{job_key}' without explicit priority: {group_keys}"
            )

        issues.append(
            make_issue(
                RULE_NON_DETERMINISTIC_ORDER,
                msg,
                sheet="partner_groups",
                field="group_name",
                details={
                    "job_key": job_key,
                    "partner_key": partner_key,
                    "group_keys": group_keys,
                    "disambiguation": kind,
                },
            )
        )
    return issues


def _check_orphan_report_item_members(
    snapshot: RulesSnapshotV2,
) -> list[ValidationIssue]:
    """``parent_group_item`` references that point to non-existent items.

    The bridge (``_build_report_items``) emits a ``parent_group_item``
    member for each payout method, pointing at the payout_group's
    item_key. If the chain breaks (group missing or out of order), the
    member ends up dangling. Flag as warn (RULE_NON_DETERMINISTIC_ORDER
    — closest catalog code; treat as diagnostic of inconsistent layout).
    """

    valid_item_keys = {item.item_key for item in snapshot.report_items}

    issues: list[ValidationIssue] = []
    for member in snapshot.report_item_members:
        if not member.enabled:
            continue
        if member.member_type != "parent_group_item":
            continue
        if member.member_key in valid_item_keys:
            continue
        issues.append(
            make_issue(
                RULE_NON_DETERMINISTIC_ORDER,
                (
                    f"Report item member 'parent_group_item' refers to "
                    f"unknown item_key '{member.member_key}'"
                ),
                sheet="report_item_members (derived)",
                rule_id=member.item_key,
                field="member_key",
                details={
                    "owner_item_key": member.item_key,
                    "member_key": member.member_key,
                },
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sorted_dup_groups(
    groups: dict[Hashable, list],
) -> Iterable[tuple[Hashable, list]]:
    """Yield (key, rules) only where ``len(rules) > 1``, deterministic order."""

    return sorted(
        ((k, v) for k, v in groups.items() if len(v) > 1),
        key=lambda kv: repr(kv[0]),
    )


def _make_duplicate_issue(
    code: str,
    sheet: str,
    key: Hashable,
    rule_ids: list[str] | None,
    *,
    strict: bool,
) -> ValidationIssue:
    details: dict[str, object] = {"key": list(key) if isinstance(key, tuple) else key}
    if rule_ids is not None:
        details["rule_ids"] = list(rule_ids)
    details["count"] = (
        len(rule_ids)
        if rule_ids is not None
        else None
    )
    return make_issue(
        code,
        f"Duplicate active rules on '{sheet}' for key={key!r}",
        severity=_severity_for(code, strict=strict),
        sheet=sheet,
        rule_id=(rule_ids[0] if rule_ids else None),
        details={k: v for k, v in details.items() if v is not None},
    )


__all__ = ["validate_snapshot"]
