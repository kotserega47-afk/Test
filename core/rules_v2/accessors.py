from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.rules_v2.indexes import RulesIndexes
from core.rules_v2.models import (
    ExclusionRule,
    JobParam,
    LimitRule,
    PartnerDef,
    PartnerGroupMember,
    RulesSnapshotV2,
    ThresholdRule,
)
from core.rules_v2.normalizers import extract_partner_code, normalize_key


@dataclass(slots=True)
class BaseRulesAccessor:
    snapshot: RulesSnapshotV2
    indexes: RulesIndexes

    def resolve_partner(self, raw_partner: str | None) -> PartnerDef | None:
        if not raw_partner:
            return None

        raw = str(raw_partner).strip()
        if not raw:
            return None

        partner_code = extract_partner_code(raw)
        if partner_code and partner_code in self.indexes.partners_by_code:
            partner_key = self.indexes.partners_by_code[partner_code]
            return self.snapshot.partners.get(partner_key)

        partner_key = normalize_key(raw)
        if partner_key in self.indexes.partners_by_key:
            return self.snapshot.partners.get(partner_key)

        return None

    def get_group_memberships(self, job_key: str, partner_key: str) -> list[PartnerGroupMember]:
        result: list[PartnerGroupMember] = []

        group_keys = self.indexes.group_members_by_job_and_partner.get((job_key, partner_key), [])
        if not group_keys:
            return result

        for member in self.snapshot.partner_group_members:
            if not member.enabled:
                continue
            if member.job_key != job_key:
                continue
            if member.partner_key != partner_key:
                continue
            if member.group_key in group_keys:
                result.append(member)

        return result

    def get_primary_group(self, job_key: str, partner_key: str) -> PartnerGroupMember | None:
        members = self.get_group_memberships(job_key, partner_key)
        return members[0] if members else None

    def get_job_param(
        self,
        job_key: str,
        param_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
        default: Any = None,
    ) -> Any:
        candidates = []

        if partner_key:
            candidates.append((job_key, "partner", partner_key, param_key))

        if group_key:
            candidates.append((job_key, "group", group_key, param_key))

        candidates.append((job_key, "global", "*", param_key))

        for key in candidates:
            param = self.indexes.job_params_index.get(key)
            if param and param.enabled:
                return param.value

        return default

    def get_exclusions(
        self,
        job_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> list[ExclusionRule]:
        result: list[ExclusionRule] = []

        for rule in self.snapshot.exclusion_rules:
            if not rule.enabled:
                continue
            if rule.job_key != job_key:
                continue

            if rule.scope_type == "global":
                result.append(rule)
            elif rule.scope_type == "partner" and partner_key and rule.scope_key == partner_key:
                result.append(rule)
            elif rule.scope_type == "group" and group_key and rule.scope_key == group_key:
                result.append(rule)

        return result

    def is_excluded(
        self,
        job_key: str,
        at_dt: datetime,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> bool:
        rules = self.get_exclusions(job_key, partner_key=partner_key, group_key=group_key)
        for rule in rules:
            if rule.start_dt <= at_dt <= rule.end_dt:
                return True
        return False


@dataclass(slots=True)
class WalletRulesAccessor(BaseRulesAccessor):
    job_key: str = "wallet"

    def get_default_method_key(self, partner_key: str) -> str | None:
        member = self.get_primary_group(self.job_key, partner_key)
        if member:
            return member.default_method_key
        return None

    def resolve_limit_rule(
        self,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
        method_key: str | None = None,
    ) -> LimitRule | None:
        candidates: list[tuple[str, str, str, str, str | None]] = []

        if partner_key:
            candidates.append((self.job_key, "partner", partner_key, metric_key, method_key))
            candidates.append((self.job_key, "partner", partner_key, metric_key, None))

        if group_key:
            candidates.append((self.job_key, "group", group_key, metric_key, method_key))
            candidates.append((self.job_key, "group", group_key, metric_key, None))

        candidates.append((self.job_key, "global", "*", metric_key, method_key))
        candidates.append((self.job_key, "global", "*", metric_key, None))

        for key in candidates:
            rule = self.indexes.limit_rules_index.get(key)
            if rule and rule.enabled:
                return rule

        return None

    def resolve_threshold_rule(
        self,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> ThresholdRule | None:
        candidates: list[tuple[str, str, str, str]] = []

        if partner_key:
            candidates.append((self.job_key, "partner", partner_key, metric_key))

        if group_key:
            candidates.append((self.job_key, "group", group_key, metric_key))

        candidates.append((self.job_key, "global", "*", metric_key))

        for key in candidates:
            rule = self.indexes.threshold_rules_index.get(key)
            if rule and rule.enabled:
                return rule

        return None


@dataclass(slots=True)
class HourlyRulesAccessor(BaseRulesAccessor):
    def get_group_members(self, job_key: str, group_key: str) -> list:
        partner_keys = self.indexes.group_members_by_job.get((str(job_key), str(group_key)), [])
        result = []

        for member in self.snapshot.partner_group_members:
            if not member.enabled:
                continue
            if str(member.job_key) != str(job_key):
                continue
            if str(member.group_key) != str(group_key):
                continue
            if str(member.partner_key) in partner_keys:
                result.append(member)

        return result

    def get_group_memberships(self, job_key: str, partner_key: str) -> list:
        result = []

        for member in self.snapshot.partner_group_members:
            if not member.enabled:
                continue
            if str(member.job_key) != str(job_key):
                continue
            if str(member.partner_key) != str(partner_key):
                continue
            result.append(member)

        return result

    def get_job_param(
        self,
        job_key: str,
        param_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ):
        candidates = []

        if partner_key:
            candidates.append((str(job_key), "partner", str(partner_key), str(param_key)))

        if group_key:
            candidates.append((str(job_key), "group", str(group_key), str(param_key)))

        candidates.append((str(job_key), "global", "*", str(param_key)))

        for key in candidates:
            rule = self.indexes.job_params_index.get(key)
            if rule and rule.enabled:
                return rule

        return None

    def resolve_limit_rule(
        self,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
        method_key: str | None = None,
    ):
        job_key = "hourly"
        metric_key = str(metric_key).strip()
        method_key_norm = str(method_key).strip().lower() if method_key else None

        candidates = []

        if partner_key:
            candidates.append((job_key, "partner", str(partner_key), metric_key, method_key_norm))
            candidates.append((job_key, "partner", str(partner_key), metric_key, None))

        if group_key:
            candidates.append((job_key, "group", str(group_key), metric_key, method_key_norm))
            candidates.append((job_key, "group", str(group_key), metric_key, None))

        candidates.append((job_key, "global", "*", metric_key, method_key_norm))
        candidates.append((job_key, "global", "*", metric_key, None))

        seen = set()
        for key in candidates:
            if key in seen:
                continue
            seen.add(key)

            rule = self.indexes.limit_rules_index.get(key)
            if rule and rule.enabled:
                return rule

        return None

    def get_exclusion(
        self,
        at_dt,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ):
        job_key = "hourly"

        for rule in self.snapshot.exclusion_rules:
            if not rule.enabled:
                continue
            if str(rule.job_key) != job_key:
                continue

            scope_type = str(rule.scope_type)
            scope_key = str(rule.scope_key)

            if scope_type == "partner" and partner_key and scope_key != str(partner_key):
                continue
            if scope_type == "group" and group_key and scope_key != str(group_key):
                continue
            if scope_type == "global" and scope_key != "*":
                continue

            if rule.start_dt <= at_dt <= rule.end_dt:
                return rule

        return None

@dataclass(slots=True)
class AccessRulesAccessor(BaseRulesAccessor):
    def resolve_role_level(
        self,
        *,
        chat_id: int | str,
        user_id: int,
    ) -> int | None:
        raw_chat = str(chat_id).strip().lower()
        chat_key = "private" if raw_chat == "private" else str(int(chat_id))

        rule = self.indexes.access_by_chat_user.get((chat_key.lower(), int(user_id)))
        if not rule or not rule.enabled:
            return None

        role = self.snapshot.roles.get(rule.role_key)
        if not role or not role.enabled:
            return None

        return int(role.role_level)

    def get_command_rule(self, command_text: str):
        cmd = str(command_text or "").strip()
        if not cmd:
            return None

        if not cmd.startswith("/"):
            cmd = f"/{cmd}"

        command = self.indexes.commands_by_text.get(cmd)
        if not command or not command.enabled:
            return None

        policy = self.indexes.command_policy_by_command_key.get(command.command_key)
        if not policy or not policy.enabled:
            return None

        role = self.snapshot.roles.get(policy.min_role_key)
        if not role or not role.enabled:
            return None

        return {
            "required_level": int(role.role_level),
            "allow_private": bool(policy.allow_private),
            "allow_groups": bool(policy.allow_groups),
            "enabled": True,
            "command_key": command.command_key,
        }


    def get_command_rule(self, command_text: str):
        cmd = str(command_text or "").strip()
        if not cmd:
            return None

        if not cmd.startswith("/"):
            cmd = f"/{cmd}"

        command = self.indexes.commands_by_text.get(cmd)
        if not command or not command.enabled:
            return None

        policy = self.indexes.command_policy_by_command_key.get(command.command_key)
        if not policy or not policy.enabled:
            return None

        role = self.snapshot.roles.get(policy.min_role_key)
        if not role or not role.enabled:
            return None

        return {
            "required_level": int(role.role_level),
            "allow_private": bool(policy.allow_private),
            "allow_groups": bool(policy.allow_groups),
            "enabled": True,
            "command_key": command.command_key,
        }

    def is_allowed(
        self,
        *,
        command_text: str,
        chat_id: int | str,
        user_id: int,
        is_private: bool,
    ) -> tuple[bool, str, dict]:
        rule = self.get_command_rule(command_text)
        cmd = str(command_text or "").strip().lstrip("/").lower()

        details = {
            "command": cmd,
            "chat_id": chat_id,
            "user_id": int(user_id),
        }

        if rule is None:
            return False, "unknown_command", details

        if is_private and not rule["allow_private"]:
            return False, "command_not_allowed_here", {**details, "where": "private"}

        if (not is_private) and not rule["allow_groups"]:
            return False, "command_not_allowed_here", {**details, "where": "group"}

        level = self.resolve_role_level(chat_id=chat_id, user_id=int(user_id))
        if level is None:
            return False, "no_access_rule", {**details, "level": 0, "required": rule["required_level"]}

        if int(level) < int(rule["required_level"]):
            return False, "insufficient_level", {
                **details,
                "level": int(level),
                "required": int(rule["required_level"]),
            }

        return True, "ok", {
            **details,
            "level": int(level),
            "required": int(rule["required_level"]),
        }


@dataclass(slots=True)
class ScheduleRulesAccessor(BaseRulesAccessor):
    def get_enabled_schedules(self) -> list:
        result = []
        for rule in self.snapshot.schedule_rules:
            if not rule.enabled:
                continue
            result.append(rule)
        return result

    def get_schedules_for_job(self, job_key: str) -> list:
        return list(self.indexes.schedule_rules_by_job.get(str(job_key), []))
class RulesAccessor:

    def __init__(self, snapshot, indexes):
        self.snapshot = snapshot
        self.indexes = indexes

    def get_partner(self, partner_key):
        return self.snapshot.partners.get(partner_key)

    def get_partner_by_code(self, code):
        partner_key = self.indexes.partners_by_code.get(str(code))
        if not partner_key:
            return None
        return self.snapshot.partners.get(partner_key)

    def get_group(self, group_key):
        return self.snapshot.partner_groups.get(group_key)

    def get_group_partners(self, job_key, group_key):
        partners = []
        for m in self.snapshot.partner_group_members:
            if not m.enabled:
                continue
            if m.job_key != job_key:
                continue
            if m.group_key != group_key:
                continue
            partners.append(m.partner_key)
        return partners