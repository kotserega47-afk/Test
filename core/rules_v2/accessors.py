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
    job_key: str = "hourly"

    def get_comment_params(
        self,
        *,
        partner_key: str | None = None,
        group_key: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}

        for param_key in (
            "comment",
            "label",
            "display_name",
            "sort_order",
        ):
            value = self.get_job_param(
                self.job_key,
                param_key,
                partner_key=partner_key,
                group_key=group_key,
                default=None,
            )
            if value is not None:
                result[param_key] = value

        return result