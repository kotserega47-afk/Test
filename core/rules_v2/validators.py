from __future__ import annotations

from dataclasses import dataclass, field

from .constants import (
    ALLOWED_JOB_PARAMS,
    ALLOWED_TELEGRAM_ROUTE_KEYS,
    ITEM_TYPES,
    LIMIT_TYPES,
    MEMBER_TYPES,
    REQUIRED_SHEETS_V2,
    SCHEDULE_TYPES,
    SCOPE_TYPES,
)
from .models import RulesSnapshotV2, TelegramRoute


@dataclass(slots=True)
class ValidationIssue:
    level: str
    message: str
    sheet: str | None = None
    key: str | None = None


@dataclass(slots=True)
class ValidationResult:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_snapshot(snapshot: RulesSnapshotV2) -> ValidationResult:
    result = ValidationResult()

    _validate_jobs(snapshot, result)
    _validate_groups(snapshot, result)
    _validate_access(snapshot, result)
    _validate_job_params(snapshot, result)
    _validate_limits(snapshot, result)
    _validate_thresholds(snapshot, result)
    _validate_reports(snapshot, result)
    _validate_telegram_routes(snapshot, result)

    return result


def is_valid_telegram_chat_id(chat_id: str) -> bool:
    """Int-like Telegram chat id string (negative group ids allowed)."""

    s = str(chat_id or "").strip()
    if not s:
        return False
    if s.startswith("-"):
        body = s[1:]
        return body.isdigit() and len(body) > 0
    return s.isdigit()


def validate_telegram_routes_dict(routes: dict[str, TelegramRoute]) -> list[str]:
    """Row-level validation for ``telegram_routes`` when the sheet is present."""

    errors: list[str] = []
    seen: set[str] = set()

    for route_key in sorted(routes.keys()):
        route = routes[route_key]
        if route.route_key != route_key:
            errors.append(f"route_key mismatch: index={route_key!r} row={route.route_key!r}")
        if not route.route_key:
            errors.append("empty route_key")
            continue
        if route.route_key in seen:
            errors.append(f"duplicate route_key: {route.route_key!r}")
        seen.add(route.route_key)
        if route.route_key not in ALLOWED_TELEGRAM_ROUTE_KEYS:
            errors.append(f"unknown route_key: {route.route_key!r}")
        if not is_valid_telegram_chat_id(route.chat_id):
            errors.append(f"invalid chat_id for route {route.route_key!r}")
        if not str(route.description or "").strip():
            errors.append(f"empty description for route {route.route_key!r}")

    return errors


def _validate_telegram_routes(snapshot: RulesSnapshotV2, result: ValidationResult) -> None:
    if not snapshot.telegram_routes:
        return
    for msg in validate_telegram_routes_dict(snapshot.telegram_routes):
        result.errors.append(ValidationIssue("error", msg, "telegram_routes"))


def _validate_jobs(snapshot: RulesSnapshotV2, result: ValidationResult) -> None:
    if not snapshot.jobs:
        result.errors.append(ValidationIssue("error", "No jobs loaded"))


def _validate_groups(snapshot: RulesSnapshotV2, result: ValidationResult) -> None:
    for member in snapshot.partner_group_members:
        if member.group_key not in snapshot.partner_groups:
            result.errors.append(
                ValidationIssue("error", f"Unknown group_key: {member.group_key}", "partner_group_members")
            )
        if member.partner_key not in snapshot.partners:
            result.errors.append(
                ValidationIssue("error", f"Unknown partner_key: {member.partner_key}", "partner_group_members")
            )
        if member.job_key not in snapshot.jobs:
            result.errors.append(
                ValidationIssue("error", f"Unknown job_key: {member.job_key}", "partner_group_members")
            )


def _validate_access(snapshot: RulesSnapshotV2, result: ValidationResult) -> None:
    for rule in snapshot.access_rules:
        if rule.role_key not in snapshot.roles:
            result.errors.append(
                ValidationIssue("error", f"Unknown role_key: {rule.role_key}", "access_rules")
            )


def _validate_job_params(snapshot: RulesSnapshotV2, result: ValidationResult) -> None:
    for param in snapshot.job_params:
        allowed = ALLOWED_JOB_PARAMS.get(param.job_key)
        if not allowed:
            result.warnings.append(
                ValidationIssue("warning", f"No param registry for job {param.job_key}", "job_params")
            )
            continue

        if param.param_key not in allowed:
            result.errors.append(
                ValidationIssue(
                    "error",
                    f"Unsupported param_key {param.param_key} for job {param.job_key}",
                    "job_params",
                )
            )

        if param.scope_type not in SCOPE_TYPES:
            result.errors.append(
                ValidationIssue("error", f"Invalid scope_type {param.scope_type}", "job_params")
            )

        if param.scope_type == "global" and param.scope_key != "*":
            result.errors.append(
                ValidationIssue("error", "Global scope must use scope_key='*'", "job_params")
            )

        if param.scope_type == "group" and param.scope_key not in snapshot.partner_groups:
            result.errors.append(
                ValidationIssue("error", f"Unknown group scope_key {param.scope_key}", "job_params")
            )

        if param.scope_type == "partner" and param.scope_key not in snapshot.partners:
            result.errors.append(
                ValidationIssue("error", f"Unknown partner scope_key {param.scope_key}", "job_params")
            )


def _validate_limits(snapshot: RulesSnapshotV2, result: ValidationResult) -> None:
    for rule in snapshot.limit_rules:
        if rule.limit_type not in LIMIT_TYPES:
            result.errors.append(
                ValidationIssue("error", f"Invalid limit_type {rule.limit_type}", "limit_rules", rule.rule_key)
            )


def _validate_thresholds(snapshot: RulesSnapshotV2, result: ValidationResult) -> None:
    for rule in snapshot.threshold_rules:
        if rule.threshold_min is None and rule.threshold_max is None:
            result.errors.append(
                ValidationIssue(
                    "error",
                    "Threshold rule must have threshold_min or threshold_max",
                    "threshold_rules",
                    rule.rule_key,
                )
            )


def _validate_reports(snapshot: RulesSnapshotV2, result: ValidationResult) -> None:
    section_keys = {s.section_key for s in snapshot.report_sections}

    for item in snapshot.report_items:
        if item.report_key not in snapshot.reports:
            result.errors.append(
                ValidationIssue("error", f"Unknown report_key {item.report_key}", "report_items", item.item_key)
            )
        if item.section_key not in section_keys:
            result.errors.append(
                ValidationIssue("error", f"Unknown section_key {item.section_key}", "report_items", item.item_key)
            )
        if item.item_type not in ITEM_TYPES:
            result.errors.append(
                ValidationIssue("error", f"Invalid item_type {item.item_type}", "report_items", item.item_key)
            )

    for member in snapshot.report_item_members:
        if member.member_type not in MEMBER_TYPES:
            result.errors.append(
                ValidationIssue(
                    "error", f"Invalid member_type {member.member_type}", "report_item_members", member.item_key
                )
            )