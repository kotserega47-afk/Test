from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    AccessRule,
    CommandDef,
    CommandPolicy,
    JobParam,
    LimitRule,
    RulesSnapshotV2,
    ScheduleRule,
    ThresholdRule,
)


@dataclass(slots=True)
class RulesIndexes:
    partners_by_key: dict[str, str] = field(default_factory=dict)
    partners_by_code: dict[str, str] = field(default_factory=dict)

    group_members_by_group: dict[str, list[str]] = field(default_factory=dict)
    group_members_by_job: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    group_members_by_job_and_partner: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    job_params_index: dict[tuple[str, str, str, str], JobParam] = field(default_factory=dict)
    limit_rules_index: dict[tuple[str, str, str, str, str | None], LimitRule] = field(default_factory=dict)
    threshold_rules_index: dict[tuple[str, str, str, str], ThresholdRule] = field(default_factory=dict)

    access_by_chat_user: dict[tuple[str, int], AccessRule] = field(default_factory=dict)
    commands_by_text: dict[str, CommandDef] = field(default_factory=dict)
    command_policy_by_command_key: dict[str, CommandPolicy] = field(default_factory=dict)
    schedule_rules_by_job: dict[str, list[ScheduleRule]] = field(default_factory=dict)


def build_indexes(snapshot: RulesSnapshotV2) -> RulesIndexes:
    idx = RulesIndexes()

    # ------------------------------------------------------------------
    # partners
    # ------------------------------------------------------------------
    for partner_key, partner in snapshot.partners.items():
        idx.partners_by_key[partner_key] = partner_key

        if partner.partner_code:
            idx.partners_by_code[str(partner.partner_code)] = partner_key

    # ------------------------------------------------------------------
    # group members
    # ------------------------------------------------------------------
    for member in snapshot.partner_group_members:
        if not member.enabled:
            continue

        idx.group_members_by_group.setdefault(member.group_key, []).append(member.partner_key)
        idx.group_members_by_job.setdefault((member.job_key, member.group_key), []).append(member.partner_key)
        idx.group_members_by_job_and_partner.setdefault((member.job_key, member.partner_key), []).append(
            member.group_key
        )

    # ------------------------------------------------------------------
    # job params
    # ------------------------------------------------------------------
    for param in snapshot.job_params:
        if not param.enabled:
            continue

        idx.job_params_index[(param.job_key, param.scope_type, param.scope_key, param.param_key)] = param

    # ------------------------------------------------------------------
    # limits
    # ------------------------------------------------------------------
    for rule in snapshot.limit_rules:
        if not rule.enabled:
            continue

        key = (rule.job_key, rule.scope_type, rule.scope_key, rule.metric_key, rule.method_key)
        idx.limit_rules_index[key] = rule

    # ------------------------------------------------------------------
    # thresholds
    # ------------------------------------------------------------------
    for rule in snapshot.threshold_rules:
        if not rule.enabled:
            continue

        key = (rule.job_key, rule.scope_type, rule.scope_key, rule.metric_key)
        idx.threshold_rules_index[key] = rule

    # ------------------------------------------------------------------
    # access
    # ------------------------------------------------------------------
    for rule in snapshot.access_rules:
        if not rule.enabled:
            continue

        chat_key = str(rule.chat_id).strip().lower()
        user_id = int(rule.user_id)
        idx.access_by_chat_user[(chat_key, user_id)] = rule

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    for command in snapshot.commands.values():
        if not command.enabled:
            continue

        cmd = str(command.command_text or "").strip()
        if not cmd:
            continue

        if not cmd.startswith("/"):
            cmd = f"/{cmd}"

        idx.commands_by_text[cmd] = command

    # ------------------------------------------------------------------
    # command policies
    # ------------------------------------------------------------------
    for policy in snapshot.command_policies:
        if not policy.enabled:
            continue

        idx.command_policy_by_command_key[policy.command_key] = policy

    # ------------------------------------------------------------------
    # schedules
    # ------------------------------------------------------------------
    for rule in snapshot.schedule_rules:
        if not rule.enabled:
            continue

        idx.schedule_rules_by_job.setdefault(rule.job_key, []).append(rule)

    return idx