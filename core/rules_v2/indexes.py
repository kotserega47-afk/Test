from __future__ import annotations

from dataclasses import dataclass, field

from .models import JobParam, LimitRule, RulesSnapshotV2, ThresholdRule


@dataclass(slots=True)
class RulesIndexes:
    group_members_by_group: dict[str, list[str]] = field(default_factory=dict)
    group_members_by_job: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    job_params_index: dict[tuple[str, str, str, str], JobParam] = field(default_factory=dict)
    limit_rules_index: dict[tuple[str, str, str, str, str | None], LimitRule] = field(default_factory=dict)
    threshold_rules_index: dict[tuple[str, str, str, str], ThresholdRule] = field(default_factory=dict)


def build_indexes(snapshot: RulesSnapshotV2) -> RulesIndexes:
    idx = RulesIndexes()

    for member in snapshot.partner_group_members:
        if not member.enabled:
            continue
        idx.group_members_by_group.setdefault(member.group_key, []).append(member.partner_key)
        idx.group_members_by_job.setdefault((member.job_key, member.group_key), []).append(member.partner_key)

    for param in snapshot.job_params:
        if not param.enabled:
            continue
        idx.job_params_index[(param.job_key, param.scope_type, param.scope_key, param.param_key)] = param

    for rule in snapshot.limit_rules:
        if not rule.enabled:
            continue
        key = (rule.job_key, rule.scope_type, rule.scope_key, rule.metric_key, rule.method_key)
        idx.limit_rules_index[key] = rule

    for rule in snapshot.threshold_rules:
        if not rule.enabled:
            continue
        key = (rule.job_key, rule.scope_type, rule.scope_key, rule.metric_key)
        idx.threshold_rules_index[key] = rule

    return idx