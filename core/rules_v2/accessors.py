from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.rules_v2.indexes import RulesIndexes
from core.rules_v2.models import (
    ExclusionRule,
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

    # -------------------------------------------------------------------------
    # helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def _norm_str(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _norm_scope_key(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _norm_method_key(value: Any) -> str | None:
        raw = str(value).strip().lower() if value is not None else ""
        return raw or None

    @staticmethod
    def _build_scope_candidates(
        *,
        partner_key: str | None,
        group_key: str | None,
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []

        if partner_key:
            candidates.append(("partner", str(partner_key)))

        if group_key:
            candidates.append(("group", str(group_key)))

        candidates.append(("global", "*"))
        return candidates

    # -------------------------------------------------------------------------
    # partner / group
    # -------------------------------------------------------------------------
    def resolve_partner(self, raw_partner: str | None) -> PartnerDef | None:
        if not raw_partner:
            return None

        raw = str(raw_partner).strip()
        if not raw:
            return None

        partner_code = extract_partner_code(raw)
        if partner_code and partner_code in self.indexes.partners_by_code:
            partner_key = self.indexes.partners_by_code[partner_code]
            partner = self.snapshot.partners.get(partner_key)
            if partner and partner.enabled:
                return partner

        partner_key = normalize_key(raw)
        if partner_key in self.indexes.partners_by_key:
            partner = self.snapshot.partners.get(partner_key)
            if partner and partner.enabled:
                return partner

        return None

    def get_partner(self, partner_key: str) -> PartnerDef | None:
        partner = self.snapshot.partners.get(str(partner_key))
        if not partner or not partner.enabled:
            return None
        return partner

    def get_group(self, group_key: str):
        group = self.snapshot.partner_groups.get(str(group_key))
        if not group or not group.enabled:
            return None
        return group

    def get_group_members(self, job_key: str, group_key: str) -> list[PartnerGroupMember]:
        job_key = self._norm_str(job_key)
        group_key = self._norm_str(group_key)

        partner_keys = self.indexes.group_members_by_job.get((job_key, group_key), [])
        if not partner_keys:
            return []

        partner_keys_set = set(partner_keys)
        result: list[PartnerGroupMember] = []

        for member in self.snapshot.partner_group_members:
            if not member.enabled:
                continue
            if member.job_key != job_key:
                continue
            if member.group_key != group_key:
                continue
            if member.partner_key not in partner_keys_set:
                continue
            result.append(member)

        return result

    def get_group_memberships(self, job_key: str, partner_key: str) -> list[PartnerGroupMember]:
        job_key = self._norm_str(job_key)
        partner_key = self._norm_str(partner_key)

        group_keys = self.indexes.group_members_by_job_and_partner.get((job_key, partner_key), [])
        if not group_keys:
            return []

        group_keys_set = set(group_keys)
        result: list[PartnerGroupMember] = []

        for member in self.snapshot.partner_group_members:
            if not member.enabled:
                continue
            if member.job_key != job_key:
                continue
            if member.partner_key != partner_key:
                continue
            if member.group_key not in group_keys_set:
                continue
            result.append(member)

        return result

    def get_primary_group(self, job_key: str, partner_key: str) -> PartnerGroupMember | None:
        members = self.get_group_memberships(job_key, partner_key)
        return members[0] if members else None

    def get_group_display_name(self, group_key: str) -> str | None:
        group = self.get_group(group_key)
        if not group:
            return None
        return str(group.display_name or "").strip() or None

    def get_partner_display_name(self, partner_key: str) -> str | None:
        partner = self.get_partner(partner_key)
        if not partner:
            return None
        return str(partner.display_name or "").strip() or None

    def get_default_method_key(self, job_key: str, partner_key: str) -> str | None:
        member = self.get_primary_group(job_key, partner_key)
        if not member:
            return None
        return self._norm_method_key(member.default_method_key)

    # -------------------------------------------------------------------------
    # params / rules
    # -------------------------------------------------------------------------
    def get_job_param(
        self,
        job_key: str,
        param_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
        default: Any = None,
    ) -> Any:
        job_key = self._norm_str(job_key)
        param_key = self._norm_str(param_key)

        for scope_type, scope_key in self._build_scope_candidates(
            partner_key=partner_key,
            group_key=group_key,
        ):
            param = self.indexes.job_params_index.get((job_key, scope_type, scope_key, param_key))
            if param and param.enabled:
                return param.value

        return default

    def resolve_limit_rule(
        self,
        job_key: str,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
        method_key: str | None = None,
    ) -> LimitRule | None:
        job_key = self._norm_str(job_key)
        metric_key = self._norm_str(metric_key)
        method_key = self._norm_method_key(method_key)

        seen: set[tuple[str, str, str, str, str | None]] = set()

        for scope_type, scope_key in self._build_scope_candidates(
            partner_key=partner_key,
            group_key=group_key,
        ):
            for candidate_method in (method_key, None):
                key = (job_key, scope_type, scope_key, metric_key, candidate_method)
                if key in seen:
                    continue
                seen.add(key)

                rule = self.indexes.limit_rules_index.get(key)
                if rule and rule.enabled:
                    return rule

        return None

    def resolve_threshold_rule(
        self,
        job_key: str,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> ThresholdRule | None:
        job_key = self._norm_str(job_key)
        metric_key = self._norm_str(metric_key)

        for scope_type, scope_key in self._build_scope_candidates(
            partner_key=partner_key,
            group_key=group_key,
        ):
            key = (job_key, scope_type, scope_key, metric_key)
            rule = self.indexes.threshold_rules_index.get(key)
            if rule and rule.enabled:
                return rule

        return None

    # -------------------------------------------------------------------------
    # exclusions
    # -------------------------------------------------------------------------
    def get_exclusions(
        self,
        job_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> list[ExclusionRule]:
        job_key = self._norm_str(job_key)
        partner_key = self._norm_scope_key(partner_key) if partner_key else None
        group_key = self._norm_scope_key(group_key) if group_key else None

        result: list[ExclusionRule] = []

        for rule in self.snapshot.exclusion_rules:
            if not rule.enabled:
                continue
            if rule.job_key != job_key:
                continue

            if rule.scope_type == "partner":
                if partner_key and rule.scope_key == partner_key:
                    result.append(rule)
                continue

            if rule.scope_type == "group":
                if group_key and rule.scope_key == group_key:
                    result.append(rule)
                continue

            if rule.scope_type == "global" and rule.scope_key == "*":
                result.append(rule)

        return result

    def get_exclusion(
        self,
        job_key: str,
        at_dt: datetime,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> ExclusionRule | None:
        for rule in self.get_exclusions(
            job_key,
            partner_key=partner_key,
            group_key=group_key,
        ):
            if rule.start_dt <= at_dt <= rule.end_dt:
                return rule
        return None

    def is_excluded(
        self,
        job_key: str,
        at_dt: datetime,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> bool:
        return (
            self.get_exclusion(
                job_key,
                at_dt,
                partner_key=partner_key,
                group_key=group_key,
            )
            is not None
        )


@dataclass(slots=True)
class WalletRulesAccessor(BaseRulesAccessor):
    job_key: str = "wallet"

    def get_default_method_key(self, partner_key: str) -> str | None:
        return BaseRulesAccessor.get_default_method_key(self, self.job_key, partner_key)

    def resolve_limit_rule(
        self,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
        method_key: str | None = None,
    ) -> LimitRule | None:
        return BaseRulesAccessor.resolve_limit_rule(
            self,
            self.job_key,
            metric_key,
            partner_key=partner_key,
            group_key=group_key,
            method_key=method_key,
        )

    def resolve_threshold_rule(
        self,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> ThresholdRule | None:
        return BaseRulesAccessor.resolve_threshold_rule(
            self,
            self.job_key,
            metric_key,
            partner_key=partner_key,
            group_key=group_key,
        )

    def get_exclusion(
        self,
        at_dt: datetime,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> ExclusionRule | None:
        return BaseRulesAccessor.get_exclusion(
            self,
            self.job_key,
            at_dt,
            partner_key=partner_key,
            group_key=group_key,
        )

    def is_excluded(
        self,
        at_dt: datetime,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> bool:
        return BaseRulesAccessor.is_excluded(
            self,
            self.job_key,
            at_dt,
            partner_key=partner_key,
            group_key=group_key,
        )


@dataclass(slots=True)
class HourlyRulesAccessor(BaseRulesAccessor):
    job_key: str = "hourly"

    def get_default_method_key(self, partner_key: str) -> str | None:
        return BaseRulesAccessor.get_default_method_key(self, self.job_key, partner_key)

    def resolve_limit_rule(
        self,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
        method_key: str | None = None,
    ) -> LimitRule | None:
        return BaseRulesAccessor.resolve_limit_rule(
            self,
            self.job_key,
            metric_key,
            partner_key=partner_key,
            group_key=group_key,
            method_key=method_key,
        )

    def resolve_threshold_rule(
        self,
        metric_key: str,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> ThresholdRule | None:
        return BaseRulesAccessor.resolve_threshold_rule(
            self,
            self.job_key,
            metric_key,
            partner_key=partner_key,
            group_key=group_key,
        )

    def get_exclusion(
        self,
        at_dt: datetime,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> ExclusionRule | None:
        return BaseRulesAccessor.get_exclusion(
            self,
            self.job_key,
            at_dt,
            partner_key=partner_key,
            group_key=group_key,
        )

    def is_excluded(
        self,
        at_dt: datetime,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> bool:
        return BaseRulesAccessor.is_excluded(
            self,
            self.job_key,
            at_dt,
            partner_key=partner_key,
            group_key=group_key,
        )


@dataclass(slots=True)
class AccessRulesAccessor(BaseRulesAccessor):
    @staticmethod
    def _normalize_chat_key(chat_id: int | str) -> str:
        raw_chat = str(chat_id).strip().lower()
        if raw_chat == "private":
            return "private"
        return str(int(chat_id))

    def resolve_role_level(
        self,
        *,
        chat_id: int | str,
        user_id: int,
    ) -> int | None:
        chat_key = self._normalize_chat_key(chat_id)

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
            return False, "no_access_rule", {
                **details,
                "level": 0,
                "required": rule["required_level"],
            }

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


# -----------------------------------------------------------------------------
# legacy compatibility accessor
# -----------------------------------------------------------------------------
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