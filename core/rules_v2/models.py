from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any


@dataclass(slots=True)
class MetaInfo:
    ruleset_version: str
    updated_at: datetime
    updated_by: str
    comment: str | None = None
    is_active: bool = True


@dataclass(slots=True)
class JobDef:
    job_key: str
    display_name: str
    enabled: bool = True
    description: str | None = None


@dataclass(slots=True)
class PartnerDef:
    partner_key: str
    partner_code: str | None
    source_name: str | None
    display_name: str
    short_name: str | None = None
    partner_type: str | None = None
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class PartnerGroupDef:
    group_key: str
    display_name: str
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class PartnerGroupMember:
    group_key: str
    partner_key: str
    job_key: str
    default_method_key: str | None = None
    enabled: bool = True
    is_primary: bool = False
    group_priority: int | None = None


@dataclass(slots=True)
class MethodDef:
    method_key: str
    display_name: str
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class RoleDef:
    role_key: str
    role_level: int
    display_name: str
    enabled: bool = True


@dataclass(slots=True)
class CommandDef:
    command_key: str
    command_text: str
    job_key: str | None
    display_name: str
    enabled: bool = True


@dataclass(slots=True)
class TelegramRoute:
    route_key: str
    chat_id: str
    enabled: bool
    description: str


@dataclass(slots=True)
class AccessRule:
    chat_id: str
    user_id: str
    role_key: str
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class CommandPolicy:
    command_key: str
    min_role_key: str
    allow_private: bool
    allow_groups: bool
    enabled: bool = True


@dataclass(slots=True)
class ScheduleRule:
    schedule_key: str
    job_key: str
    schedule_type: str
    every_seconds: int | None = None
    cron_expr: str | None = None
    timezone: str = "Europe/Moscow"
    coalesce: bool = True
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class JobParam:
    job_key: str
    scope_type: str
    scope_key: str
    param_key: str
    value_type: str
    value: Any
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class LimitRule:
    rule_key: str
    job_key: str
    scope_type: str
    scope_key: str
    metric_key: str
    method_key: str | None
    limit_type: str
    limit_value: float
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class ThresholdRule:
    rule_key: str
    job_key: str
    scope_type: str
    scope_key: str
    metric_key: str
    threshold_min: float | None = None
    threshold_max: float | None = None
    min_events: int | None = None
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class ExclusionRule:
    exclusion_key: str
    job_key: str
    scope_type: str
    scope_key: str
    start_dt: datetime
    end_dt: datetime
    reason: str
    enabled: bool = True


@dataclass(slots=True)
class ReportDef:
    report_key: str
    job_key: str
    display_name: str
    enabled: bool = True


@dataclass(slots=True)
class ReportSection:
    section_key: str
    report_key: str
    parent_section_key: str | None
    display_name: str
    section_type: str
    sort_order: int
    style_key: str | None = None
    enabled: bool = True


@dataclass(slots=True)
class ReportItem:
    item_key: str
    report_key: str
    section_key: str
    item_type: str
    source_key: str
    method_key: str | None
    display_name: str
    sort_order: int
    enabled: bool = True
    comment: str | None = None


@dataclass(slots=True)
class ReportItemMember:
    item_key: str
    member_type: str
    member_key: str
    sort_order: int
    enabled: bool = True


@dataclass(slots=True)
class RulesSnapshotV2:
    meta: MetaInfo
    jobs: dict[str, JobDef] = field(default_factory=dict)
    partners: dict[str, PartnerDef] = field(default_factory=dict)
    partner_groups: dict[str, PartnerGroupDef] = field(default_factory=dict)
    partner_group_members: list[PartnerGroupMember] = field(default_factory=list)
    methods: dict[str, MethodDef] = field(default_factory=dict)
    roles: dict[str, RoleDef] = field(default_factory=dict)
    commands: dict[str, CommandDef] = field(default_factory=dict)
    access_rules: list[AccessRule] = field(default_factory=list)
    command_policies: dict[str, CommandPolicy] = field(default_factory=dict)
    schedule_rules: list[ScheduleRule] = field(default_factory=list)
    job_params: list[JobParam] = field(default_factory=list)
    limit_rules: list[LimitRule] = field(default_factory=list)
    threshold_rules: list[ThresholdRule] = field(default_factory=list)
    exclusion_rules: list[ExclusionRule] = field(default_factory=list)
    reports: dict[str, ReportDef] = field(default_factory=dict)
    report_sections: list[ReportSection] = field(default_factory=list)
    report_items: list[ReportItem] = field(default_factory=list)
    report_item_members: list[ReportItemMember] = field(default_factory=list)
    telegram_routes: dict[str, TelegramRoute] = field(default_factory=dict)