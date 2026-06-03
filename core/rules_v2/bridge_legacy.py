from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from core.rules_v2.normalizers import (
    normalize_key,
    build_partner_key,
    extract_partner_code,
)

import pandas as pd

from core.datetime_utils import now_msk, parse_msk_datetime, parse_time_value
from core.rules_v2.models import (
    AccessRule,
    CommandDef,
    CommandPolicy,
    ExclusionRule,
    JobDef,
    JobParam,
    LimitRule,
    MetaInfo,
    MethodDef,
    PartnerDef,
    PartnerGroupDef,
    PartnerGroupMember,
    ReportDef,
    ReportItem,
    ReportItemMember,
    ReportSection,
    RoleDef,
    RulesSnapshotV2,
    ScheduleRule,
    TelegramRoute,
    ThresholdRule,
)
from core.rules_v2.constants import TELEGRAM_ROUTES_REQUIRED_COLUMNS, TELEGRAM_ROUTES_SHEET
from core.rules_v2.validators import validate_telegram_routes_dict


DEFAULT_TIMEZONE = "Europe/Moscow"


def load_legacy_workbook(path: str | Path) -> dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(path)
    sheets: dict[str, pd.DataFrame] = {}

    for sheet_name in xls.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet_name)
        df = df.rename(columns=lambda c: str(c).strip())
        sheets[sheet_name] = df

    return sheets


def build_snapshot_v2_from_legacy(path: str | Path) -> RulesSnapshotV2:
    sheets = load_legacy_workbook(path)

    meta = _build_meta(sheets)
    jobs = _build_jobs(sheets)
    roles = _build_roles(sheets)
    commands, command_policies = _build_commands_and_policies(sheets)
    methods = _build_methods(sheets)
    partners = _build_partners(sheets)
    partner_groups = _build_partner_groups(sheets)
    partner_group_members = _build_partner_group_members(sheets, partners)
    access_rules = _build_access_rules(sheets, roles)
    schedule_rules = _build_schedule_rules(sheets)
    job_params = _build_job_params(sheets)
    limit_rules = _build_limit_rules(sheets)
    threshold_rules = _build_threshold_rules(sheets)
    exclusion_rules = _build_exclusion_rules(sheets, partners)

    reports = _build_reports(sheets)
    report_sections = _build_report_sections(sheets, reports)
    report_items, report_item_members = _build_report_items(sheets, reports)
    telegram_routes, telegram_routes_sheet_present = _build_telegram_routes(sheets)
    if telegram_routes_sheet_present:
        route_errors = validate_telegram_routes_dict(telegram_routes)
        if route_errors:
            raise ValueError(
                "telegram_routes validation failed: " + "; ".join(route_errors[:8])
                + (f" (+{len(route_errors) - 8} more)" if len(route_errors) > 8 else "")
            )

    return RulesSnapshotV2(
        meta=meta,
        jobs=jobs,
        partners=partners,
        partner_groups=partner_groups,
        partner_group_members=partner_group_members,
        methods=methods,
        roles=roles,
        commands=commands,
        access_rules=access_rules,
        command_policies=command_policies,
        schedule_rules=schedule_rules,
        job_params=job_params,
        limit_rules=limit_rules,
        threshold_rules=threshold_rules,
        exclusion_rules=exclusion_rules,
        reports=reports,
        report_sections=report_sections,
        report_items=report_items,
        report_item_members=report_item_members,
        telegram_routes=telegram_routes,
    )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _derive_layout_section(row: pd.Series) -> tuple[str, str]:
    raw_section = _as_str(row.get("section"))
    if raw_section:
        section_key = normalize_key(raw_section)
        if section_key:
            return section_key, raw_section

    raw_key = _as_str(row.get("key"))
    if raw_key and "." in raw_key:
        prefix = raw_key.split(".", 1)[0].strip()
        section_key = normalize_key(prefix)
        if section_key:
            return section_key, prefix

    return "", ""

def _is_enabled(value: Any) -> bool:
    if pd.isna(value):
        return False

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        try:
            return float(value) != 0.0
        except (TypeError, ValueError):
            return False

    s = str(value).strip().lower()
    if not s:
        return False

    try:
        return float(s) != 0.0
    except ValueError:
        pass

    return s in {"true", "yes", "y", "да", "on"}


def _as_str(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _as_optional_str(value: Any) -> str | None:
    s = _as_str(value)
    return s or None



def _safe_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    s = str(value).strip().replace(",", ".")
    if not s:
        return None
    return float(s)


def _safe_int(value: Any) -> int | None:
    if pd.isna(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    return int(float(s))


def _parse_bool_flag(value: Any) -> bool:
    """Parse optional Excel flags (e.g. ``is_primary``): empty/NaN → ``False``."""

    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if not s:
        return False
    if s in {"1", "true", "yes", "y", "да", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off", "нет"}:
        return False
    try:
        return float(s) != 0.0
    except ValueError:
        return False


def _parse_analyzers_cell(value: Any) -> list[str]:
    s = _as_str(value)
    if not s:
        return []
    return [normalize_key(x) for x in s.split(",") if _as_str(x)]


def _scope_key_from_legacy(scope: str, scope_value: str, partners: dict[str, PartnerDef]) -> str:
    scope = normalize_key(scope)
    scope_value = _as_str(scope_value)

    if scope == "global":
        return "*"
    if scope == "group":
        return normalize_key(scope_value)
    if scope == "partner":
        partner_key = build_partner_key(scope_value)
        if partner_key in partners:
            return partner_key
        return partner_key

    return normalize_key(scope_value)

def _safe_sort_order(value: Any, default: int = 1000) -> int:
    n = _safe_int(value)
    return int(n) if n is not None else default


def _csv_tokens(value: Any) -> list[str]:
    s = _as_str(value)
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x and str(x).strip()]


def _item_key(*parts: Any) -> str:
    tokens = []
    for p in parts:
        s = _as_str(p)
        if not s:
            continue
        tokens.append(normalize_key(s))
    return ".".join(tokens)


def _section_key(report_key: str, section_name: str) -> str:
    return f"{normalize_key(report_key)}.{normalize_key(section_name)}"

def _build_reports(sheets: dict[str, pd.DataFrame]) -> dict[str, ReportDef]:
    reports: dict[str, ReportDef] = {}

    ui_df = sheets.get("ui_layout")
    if ui_df is not None and not ui_df.empty:
        for view in ui_df.get("view", pd.Series(dtype=str)).dropna().tolist():
            view_key = normalize_key(view)
            if not view_key:
                continue

            reports[view_key] = ReportDef(
                report_key=view_key,
                job_key=view_key,
                display_name=str(view).strip(),
                enabled=True,
            )

    # transitional hourly fallback:
    # даже если ui_layout временно пустой/битый, hourly report должен существовать.
    if "hourly" not in reports:
        reports["hourly"] = ReportDef(
            report_key="hourly",
            job_key="hourly",
            display_name="hourly",
            enabled=True,
        )

    return reports


def _build_report_sections(
    sheets: dict[str, pd.DataFrame],
    reports: dict[str, ReportDef],
) -> list[ReportSection]:
    sections: list[ReportSection] = []
    seen: set[str] = set()

    ui_df = sheets.get("ui_layout")
    if ui_df is not None and not ui_df.empty:
        for _, row in ui_df.iterrows():
            if not _is_enabled_default_true(row.get("enabled")):
                continue

            report_key = normalize_key(row.get("view"))
            section_name, _section_display_name = _derive_layout_section(row)

            if not report_key or not section_name:
                continue
            if report_key not in reports:
                continue

            key = _section_key(report_key, section_name)
            if key in seen:
                continue
            seen.add(key)

            sections.append(
                ReportSection(
                    section_key=key,
                    report_key=report_key,
                    parent_section_key=None,
                    display_name=section_name,
                    section_type="layout",
                    sort_order=_safe_sort_order(row.get("order"), default=1000),
                    style_key=None,
                    enabled=True,
                )
            )

    # transitional config sections for hourly
    if "hourly" in reports:
        extra_sections = [
            ("config_payins", "config_payins", 100),
            ("config_payouts", "config_payouts", 110),
            ("config_payout_methods", "config_payout_methods", 120),
        ]
        for section_name, display_name, sort_order in extra_sections:
            key = _section_key("hourly", section_name)
            if key in seen:
                continue
            seen.add(key)

            sections.append(
                ReportSection(
                    section_key=key,
                    report_key="hourly",
                    parent_section_key=None,
                    display_name=display_name,
                    section_type="config",
                    sort_order=sort_order,
                    style_key=None,
                    enabled=True,
                )
            )

    sections.sort(key=lambda x: (x.report_key, x.sort_order, x.section_key))
    return sections

def _is_enabled_default_true(value: Any) -> bool:
    if pd.isna(value):
        return True
    return _is_enabled(value)

def _build_report_items(
    sheets: dict[str, pd.DataFrame],
    reports: dict[str, ReportDef],
) -> tuple[list[ReportItem], list[ReportItemMember]]:
    items: list[ReportItem] = []
    members: list[ReportItemMember] = []

    # ------------------------------------------------------------------
    # ui_layout -> layout items
    # ------------------------------------------------------------------
    ui_df = sheets.get("ui_layout")
    if ui_df is not None and not ui_df.empty:
        for idx, row in ui_df.iterrows():
            if not _is_enabled_default_true(row.get("enabled")):
                continue

            report_key = normalize_key(row.get("view"))
            section_name = normalize_key(row.get("section"))
            if not report_key or not section_name:
                continue
            if report_key not in reports:
                continue

            section_key = _section_key(report_key, section_name)
            line_key = _as_str(row.get("key"))
            title = _as_optional_str(row.get("title"))
            style = _as_optional_str(row.get("style"))

            item_key = _item_key(
                "layout",
                report_key,
                section_name,
                _safe_sort_order(row.get("order"), default=idx + 1),
                line_key or f"row_{idx+1}",
            )

            items.append(
                ReportItem(
                    item_key=item_key,
                    report_key=report_key,
                    section_key=section_key,
                    item_type="layout_line",
                    source_key=line_key,
                    method_key=None,
                    display_name=title,  # без fallback на line_key
                    sort_order=_safe_sort_order(row.get("order"), default=idx + 1),
                    enabled=True,
                    comment=style,
                )
            )

    # ------------------------------------------------------------------
    # hourly_payins -> config items
    # ------------------------------------------------------------------
    payins_df = sheets.get("hourly_payins")
    if payins_df is not None and not payins_df.empty and "hourly" in reports:
        section_key = _section_key("hourly", "config_payins")

        for idx, row in payins_df.iterrows():
            if not _is_enabled_default_true(row.get("enabled")):
                continue

            display_name = _as_str(row.get("display_name"))
            group_code = _as_str(row.get("group_code"))
            comment = _as_optional_str(row.get("comment"))

            # фильтр пустых строк
            if not display_name and not group_code and not comment:
                continue

            source_key = group_code or display_name
            if not source_key:
                continue

            item_key = _item_key("hourly", "payin", idx + 1, source_key)

            items.append(
                ReportItem(
                    item_key=item_key,
                    report_key="hourly",
                    section_key=section_key,
                    item_type="payin_row",
                    source_key=source_key,
                    method_key=None,
                    display_name=display_name or source_key,
                    sort_order=_safe_sort_order(row.get("sort_order"), default=idx + 1),
                    enabled=True,
                    comment=comment,
                )
            )

            source_partners = _csv_tokens(row.get("source_partners"))
            for member_order, partner_name in enumerate(source_partners, start=1):
                members.append(
                    ReportItemMember(
                        item_key=item_key,
                        member_type="source_partner",
                        member_key=normalize_key(partner_name),
                        sort_order=member_order,
                        enabled=True,
                    )
                )

            if _safe_int(row.get("group_break_after")) == 1:
                members.append(
                    ReportItemMember(
                        item_key=item_key,
                        member_type="group_break_after",
                        member_key="1",
                        sort_order=999,
                        enabled=True,
                    )
                )

    # ------------------------------------------------------------------
    # hourly_payouts -> payout group config
    # ------------------------------------------------------------------
    payout_group_item_keys: dict[str, str] = {}

    payouts_df = sheets.get("hourly_payouts")
    if payouts_df is not None and not payouts_df.empty and "hourly" in reports:
        section_key = _section_key("hourly", "config_payouts")

        for idx, row in payouts_df.iterrows():
            if not _is_enabled_default_true(row.get("enabled")):
                continue

            group_code = _as_str(row.get("group_code"))
            display_name = _as_str(row.get("display_name")) or group_code
            if not group_code:
                continue

            item_key = _item_key("hourly", "payout_group", group_code)
            payout_group_item_keys[group_code] = item_key

            items.append(
                ReportItem(
                    item_key=item_key,
                    report_key="hourly",
                    section_key=section_key,
                    item_type="payout_group",
                    source_key=group_code,
                    method_key=None,
                    display_name=display_name,
                    sort_order=_safe_sort_order(row.get("sort_order"), default=idx + 1),
                    enabled=True,
                    comment=None,
                )
            )

            if _safe_int(row.get("group_break_after")) == 1:
                members.append(
                    ReportItemMember(
                        item_key=item_key,
                        member_type="group_break_after",
                        member_key="1",
                        sort_order=999,
                        enabled=True,
                    )
                )

    # ------------------------------------------------------------------
    # hourly_payout_methods -> payout method config
    # ------------------------------------------------------------------
    methods_df = sheets.get("hourly_payout_methods")
    if methods_df is not None and not methods_df.empty and "hourly" in reports:
        section_key = _section_key("hourly", "config_payout_methods")

        for idx, row in methods_df.iterrows():
            if not _is_enabled_default_true(row.get("enabled")):
                continue

            group_code = _as_str(row.get("group_code"))
            method_key = normalize_key(_as_str(row.get("method_code")) or "UNI")
            method_name = _as_str(row.get("method_name")) or method_key.upper()
            comment = _as_optional_str(row.get("comment"))

            if not group_code:
                continue

            item_key = _item_key("hourly", "payout_method", group_code, method_key, idx + 1)

            items.append(
                ReportItem(
                    item_key=item_key,
                    report_key="hourly",
                    section_key=section_key,
                    item_type="payout_method",
                    source_key=group_code,
                    method_key=method_key,
                    display_name=method_name,
                    sort_order=_safe_sort_order(row.get("sort_order"), default=idx + 1),
                    enabled=True,
                    comment=comment,
                )
            )

            # link payout_method -> payout_group
            parent_group_item_key = payout_group_item_keys.get(group_code)
            if parent_group_item_key:
                members.append(
                    ReportItemMember(
                        item_key=item_key,
                        member_type="parent_group_item",
                        member_key=parent_group_item_key,
                        sort_order=1,
                        enabled=True,
                    )
                )

            # keep legacy source_partners semantics for transitional render-model
            source_partners = _csv_tokens(row.get("source_partners"))
            for member_order, partner_name in enumerate(source_partners, start=10):
                members.append(
                    ReportItemMember(
                        item_key=item_key,
                        member_type="source_partner",
                        member_key=normalize_key(partner_name),
                        sort_order=member_order,
                        enabled=True,
                    )
                )

    items.sort(key=lambda x: (x.report_key, x.section_key, x.sort_order, x.item_key))
    members.sort(key=lambda x: (x.item_key, x.sort_order, x.member_type, x.member_key))
    return items, members
# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------


def _build_meta(sheets: dict[str, pd.DataFrame]) -> MetaInfo:
    df = sheets.get("meta", pd.DataFrame(columns=["key", "value"]))

    values: dict[str, Any] = {}
    for _, row in df.iterrows():
        key = _as_str(row.get("key"))
        if key:
            values[key] = row.get("value")

    version = values.get("version", "legacy")
    updated_at_raw = values.get("updated_at")
    updated_by = _as_str(values.get("updated_by")) or "legacy_bridge"
    comment = _as_optional_str(values.get("comment")) or "Built from legacy workbook"
    is_active = True

    if isinstance(updated_at_raw, datetime):
        updated_at = updated_at_raw
    elif pd.notna(updated_at_raw):
        parsed = parse_msk_datetime(updated_at_raw)
        updated_at = parsed if parsed is not None else now_msk()
    else:
        updated_at = now_msk()

    return MetaInfo(
        ruleset_version=str(version),
        updated_at=updated_at,
        updated_by=updated_by,
        comment=comment,
        is_active=is_active,
    )


def _build_jobs(sheets: dict[str, pd.DataFrame]) -> dict[str, JobDef]:
    job_keys: set[str] = set()

    if "schedules" in sheets:
        df = sheets["schedules"]
        if "job_type" in df.columns:
            for v in df["job_type"].dropna().tolist():
                job_keys.add(normalize_key(v))

    if "job_params" in sheets:
        df = sheets["job_params"]
        if "job" in df.columns:
            for v in df["job"].dropna().tolist():
                job_keys.add(normalize_key(v))

    if "thresholds_partner" in sheets:
        df = sheets["thresholds_partner"]
        if "analyzer" in df.columns:
            for v in df["analyzer"].dropna().tolist():
                job_keys.add(normalize_key(v))

    if "wallet_limits" in sheets:
        df = sheets["wallet_limits"]
        if "analyzers" in df.columns:
            for v in df["analyzers"].dropna().tolist():
                for item in _parse_analyzers_cell(v):
                    job_keys.add(item)

    if "exclude_time" in sheets:
        df = sheets["exclude_time"]
        if "analyzers" in df.columns:
            for v in df["analyzers"].dropna().tolist():
                for item in _parse_analyzers_cell(v):
                    job_keys.add(item)

    return {
        job_key: JobDef(
            job_key=job_key,
            display_name=job_key,
            enabled=True,
            description="Derived from legacy rules",
        )
        for job_key in sorted(job_keys)
    }


def _build_roles(sheets: dict[str, pd.DataFrame]) -> dict[str, RoleDef]:
    """
    Role keys ``level_N`` must exist for every ``required_level`` referenced on ``commands``,
    not only for levels that appear on ``access``. Otherwise ``core.access_rules`` drops those
    commands from ``commands_map`` (no ``RoleDef`` for ``policy.min_role_key``).
    """
    levels: set[int] = set()

    df_access = sheets.get("access", pd.DataFrame(columns=["level"]))
    for v in df_access.get("level", pd.Series(dtype=object)).dropna().tolist():
        lv = _safe_int(v)
        if lv is not None:
            levels.add(lv)

    df_commands = sheets.get("commands", pd.DataFrame())
    if not df_commands.empty and "required_level" in df_commands.columns:
        for v in df_commands["required_level"].dropna().tolist():
            lv = _safe_int(v)
            if lv is not None:
                levels.add(lv)

    roles: dict[str, RoleDef] = {}
    for level in sorted(levels):
        role_key = f"level_{level}"
        roles[role_key] = RoleDef(
            role_key=role_key,
            role_level=level,
            display_name=f"Level {level}",
            enabled=True,
        )

    return roles


def _build_commands_and_policies(
    sheets: dict[str, pd.DataFrame],
) -> tuple[dict[str, CommandDef], dict[str, CommandPolicy]]:
    df = sheets.get("commands", pd.DataFrame())

    commands: dict[str, CommandDef] = {}
    policies: dict[str, CommandPolicy] = {}

    for _, row in df.iterrows():
        command_text = _as_str(row.get("command"))
        if not command_text:
            continue

        command_key = normalize_key(command_text)
        required_level = _safe_int(row.get("required_level")) or 0
        role_key = f"level_{required_level}"
        enabled = _is_enabled(row.get("enabled", 1))

        commands[command_key] = CommandDef(
            command_key=command_key,
            command_text=command_text,
            job_key=None,
            display_name=command_text,
            enabled=enabled,
        )

        policies[command_key] = CommandPolicy(
            command_key=command_key,
            min_role_key=role_key,
            allow_private=_is_enabled(row.get("allow_private", 1)),
            allow_groups=_is_enabled(row.get("allow_groups", 1)),
            enabled=enabled,
        )

    return commands, policies


def _build_methods(sheets: dict[str, pd.DataFrame]) -> dict[str, MethodDef]:
    method_keys: set[str] = set()

    if "wallet_limits" in sheets:
        df = sheets["wallet_limits"]
        if "method" in df.columns:
            for v in df["method"].dropna().tolist():
                s = normalize_key(v)
                if s:
                    method_keys.add(s)

    if "hourly_payout_methods" in sheets:
        df = sheets["hourly_payout_methods"]
        for col in ("method_code", "method_name"):
            if col in df.columns:
                for v in df[col].dropna().tolist():
                    s = normalize_key(v)
                    if s:
                        method_keys.add(s)

    if "partner_groups" in sheets and "default_method" in sheets["partner_groups"].columns:
        df = sheets["partner_groups"]
        for v in df["default_method"].dropna().tolist():
            s = normalize_key(v)
            if s:
                method_keys.add(s)

    methods: dict[str, MethodDef] = {}
    for method_key in sorted(method_keys):
        methods[method_key] = MethodDef(
            method_key=method_key,
            display_name=method_key.upper(),
            enabled=True,
            comment="Derived from legacy rules",
        )

    return methods


def _build_partners(sheets: dict[str, pd.DataFrame]) -> dict[str, PartnerDef]:
    raw_names: set[str] = set()

    for sheet_name, col_name in [
        ("partner_groups", "partner"),
        ("thresholds_partner", "partner"),
        ("exclude_time", "partner"),
        ("hourly_payins", "source_partners"),
        ("hourly_payout_methods", "source_partners"),
    ]:
        df = sheets.get(sheet_name)
        if df is None or col_name not in df.columns:
            continue

        for value in df[col_name].dropna().tolist():
            text = _as_str(value)
            if not text:
                continue

            if sheet_name in {"hourly_payins", "hourly_payout_methods"}:
                parts = [x.strip() for x in text.split(",") if x.strip()]
                raw_names.update(parts)
            else:
                raw_names.add(text)

    partners: dict[str, PartnerDef] = {}
    for raw_name in sorted(raw_names):
        partner_key = build_partner_key(raw_name)
        if not partner_key:
            continue

        partner_code = extract_partner_code(raw_name)

        partners[partner_key] = PartnerDef(
            partner_key=partner_key,
            partner_code=partner_code,
            source_name=raw_name,
            display_name=raw_name,
            short_name=raw_name,
            partner_type="legacy_partner",
            enabled=True,
            comment="Derived from legacy workbook",
        )

    return partners


def _build_partner_groups(sheets: dict[str, pd.DataFrame]) -> dict[str, PartnerGroupDef]:
    df = sheets.get("partner_groups", pd.DataFrame())

    groups: dict[str, PartnerGroupDef] = {}
    for _, row in df.iterrows():
        group_name = _as_str(row.get("group_name"))
        if not group_name:
            continue

        group_key = normalize_key(group_name)
        if group_key not in groups:
            groups[group_key] = PartnerGroupDef(
                group_key=group_key,
                display_name=group_name,
                enabled=True,
                comment="Derived from legacy partner_groups",
            )

    return groups


def _build_partner_group_members(
    sheets: dict[str, pd.DataFrame],
    partners: dict[str, PartnerDef],
) -> list[PartnerGroupMember]:
    df = sheets.get("partner_groups", pd.DataFrame())

    members: list[PartnerGroupMember] = []

    for _, row in df.iterrows():
        enabled = _is_enabled(row.get("enabled", 1))
        group_name = _as_str(row.get("group_name"))
        partner_name = _as_str(row.get("partner"))
        analyzers = _parse_analyzers_cell(row.get("analyzers"))
        default_method = _as_optional_str(row.get("default_method"))

        if not group_name or not partner_name:
            continue

        group_key = normalize_key(group_name)
        partner_key = build_partner_key(partner_name)
        default_method_key = normalize_key(default_method) if default_method else None

        is_primary = (
            _parse_bool_flag(row.get("is_primary"))
            if "is_primary" in df.columns
            else False
        )
        group_priority: int | None = None
        if "group_priority" in df.columns:
            group_priority = _safe_int(row.get("group_priority"))

        if partner_key not in partners:
            continue

        for job_key in analyzers or ["wallet"]:
            members.append(
                PartnerGroupMember(
                    group_key=group_key,
                    partner_key=partner_key,
                    job_key=job_key,
                    default_method_key=default_method_key,
                    enabled=enabled,
                    is_primary=is_primary,
                    group_priority=group_priority,
                )
            )

    return members


def _build_access_rules(
    sheets: dict[str, pd.DataFrame],
    roles: dict[str, RoleDef],
) -> list[AccessRule]:
    df = sheets.get("access", pd.DataFrame())

    rules: list[AccessRule] = []
    for _, row in df.iterrows():
        level = _safe_int(row.get("level"))
        if level is None:
            continue

        role_key = f"level_{level}"
        if role_key not in roles:
            continue

        rules.append(
            AccessRule(
                chat_id=_as_str(row.get("chat_id")),
                user_id=_as_str(row.get("user_id")),
                role_key=role_key,
                enabled=_is_enabled(row.get("enabled", 1)),
                comment=_as_optional_str(row.get("note")),
            )
        )

    return rules


def _build_schedule_rules(sheets: dict[str, pd.DataFrame]) -> list[ScheduleRule]:
    df = sheets.get("schedules", pd.DataFrame())

    rules: list[ScheduleRule] = []
    for _, row in df.iterrows():
        schedule_key = _as_str(row.get("id")) or normalize_key(row.get("job_type"))
        job_key = normalize_key(row.get("job_type"))
        schedule_type = normalize_key(row.get("schedule_type"))

        rules.append(
            ScheduleRule(
                schedule_key=schedule_key,
                job_key=job_key,
                schedule_type=schedule_type,
                every_seconds=_safe_int(row.get("every_seconds")),
                cron_expr=_as_optional_str(row.get("cron")),
                timezone=DEFAULT_TIMEZONE,
                coalesce=_is_enabled(row.get("coalesce", 1)),
                enabled=_is_enabled(row.get("enabled", 1)),
                comment="Derived from legacy schedules",
            )
        )

    return rules


def _build_job_params(sheets: dict[str, pd.DataFrame]) -> list[JobParam]:
    df = sheets.get("job_params", pd.DataFrame())

    params: list[JobParam] = []
    for _, row in df.iterrows():
        job_key = normalize_key(row.get("job"))
        scope_type = normalize_key(row.get("scope")) or "global"
        raw_scope_value = _as_str(row.get("scope_value"))
        scope_key = "*" if scope_type == "global" else (normalize_key(raw_scope_value) if raw_scope_value else "*")
        param_key = normalize_key(row.get("key"))
        value_type = normalize_key(row.get("value_type")) or "str"

        raw_value = row.get("value")
        value = _parse_job_param_value(raw_value, value_type)

        params.append(
            JobParam(
                job_key=job_key,
                scope_type=scope_type,
                scope_key=scope_key,
                param_key=param_key,
                value_type=value_type,
                value=value,
                enabled=_is_enabled(row.get("enabled", 1)),
                comment=_as_optional_str(row.get("comment")),
            )
        )

    return params


def _parse_job_param_value(value: Any, value_type: str) -> Any:
    if value_type == "int":
        return _safe_int(value)
    if value_type == "float":
        return _safe_float(value)
    if value_type == "bool":
        return _is_enabled(value)
    if value_type == "time":
        return parse_time_value(value)
    if value_type == "str":
        return _as_str(value)
    return value


def _build_limit_rules(sheets: dict[str, pd.DataFrame]) -> list[LimitRule]:
    df = sheets.get("wallet_limits", pd.DataFrame())

    rules: list[LimitRule] = []
    for _, row in df.iterrows():
        analyzers = _parse_analyzers_cell(row.get("analyzers"))
        scope_type = normalize_key(row.get("scope"))
        scope_value = _as_str(row.get("scope_value"))
        metric_key = normalize_key(row.get("limit_type"))
        method_key = normalize_key(row.get("method")) if _as_str(row.get("method")) else None
        limit_value = _safe_float(row.get("limit_value"))

        if limit_value is None:
            continue

        for job_key in analyzers or ["wallet"]:
            rules.append(
                LimitRule(
                    rule_key=_as_str(row.get("id")) or f"{job_key}_{scope_type}_{scope_value}_{metric_key}",
                    job_key=job_key,
                    scope_type=scope_type,
                    scope_key=_scope_key_from_legacy(scope_type, scope_value, {}),
                    metric_key=metric_key,
                    method_key=method_key,
                    limit_type="max",
                    limit_value=limit_value,
                    enabled=_is_enabled(row.get("enabled", 1)),
                    comment=_as_optional_str(row.get("comment")) or _as_optional_str(row.get("reason")),
                )
            )

    return rules


def _build_threshold_rules(sheets: dict[str, pd.DataFrame]) -> list[ThresholdRule]:
    df = sheets.get("thresholds_partner", pd.DataFrame())

    rules: list[ThresholdRule] = []
    for _, row in df.iterrows():
        job_key = normalize_key(row.get("analyzer"))
        partner_name = _as_str(row.get("partner"))
        if not partner_name:
            continue

        rules.append(
            ThresholdRule(
                rule_key=_as_str(row.get("id")) or f"{job_key}_{build_partner_key(partner_name)}_{normalize_key(row.get('metric'))}",
                job_key=job_key,
                scope_type="partner",
                scope_key=build_partner_key(partner_name),
                metric_key=normalize_key(row.get("metric")),
                threshold_min=_safe_float(row.get("threshold_min")),
                threshold_max=_safe_float(row.get("threshold_max")),
                min_events=_safe_int(row.get("min_events")),
                enabled=_is_enabled(row.get("enabled", 1)),
                comment=_as_optional_str(row.get("reason")),
            )
        )

    return rules


def _build_exclusion_rules(
    sheets: dict[str, pd.DataFrame],
    partners: dict[str, PartnerDef],
) -> list[ExclusionRule]:
    df = sheets.get("exclude_time", pd.DataFrame())

    rules: list[ExclusionRule] = []
    for _, row in df.iterrows():
        analyzers = _parse_analyzers_cell(row.get("analyzers"))
        partner_name = _as_str(row.get("partner"))
        if not partner_name:
            continue

        partner_key = build_partner_key(partner_name)
        if partner_key not in partners:
            # всё равно сохраняем, чтобы не потерять правило
            partner_key = build_partner_key(partner_name)

        start_dt = row.get("start_dt")
        end_dt = row.get("end_dt")

        if not isinstance(start_dt, datetime):
            start_dt = parse_msk_datetime(start_dt)
        if not isinstance(end_dt, datetime):
            end_dt = parse_msk_datetime(end_dt)

        if start_dt is None or end_dt is None:
            continue

        for job_key in analyzers or ["wallet"]:
            rules.append(
                ExclusionRule(
                    exclusion_key=_as_str(row.get("id")) or f"{job_key}_{partner_key}_{start_dt:%Y%m%d%H%M%S}",
                    job_key=job_key,
                    scope_type="partner",
                    scope_key=partner_key,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    reason=_as_str(row.get("reason")) or "legacy exclusion",
                    enabled=_is_enabled(row.get("enabled", 1)),
                )
            )

    return rules


def _telegram_chat_id_as_str(value: Any) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        try:
            iv = int(value)
            if float(value) == float(iv):
                return str(iv)
        except (TypeError, ValueError):
            pass
    return str(value).strip()


def _build_telegram_routes(
    sheets: dict[str, pd.DataFrame],
) -> tuple[dict[str, TelegramRoute], bool]:
    """Parse optional ``telegram_routes`` sheet. Missing sheet → empty dict."""

    if TELEGRAM_ROUTES_SHEET not in sheets:
        return {}, False

    df = sheets[TELEGRAM_ROUTES_SHEET]
    if df is None or df.empty:
        return {}, True

    df = df.rename(columns=lambda c: str(c).strip())
    missing_cols = TELEGRAM_ROUTES_REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        raise ValueError(
            f"telegram_routes missing required columns: {sorted(missing_cols)}"
        )

    routes: dict[str, TelegramRoute] = {}
    for _, row in df.iterrows():
        raw_key = _as_str(row.get("route_key"))
        if not raw_key:
            continue
        route_key = normalize_key(raw_key)
        if not route_key:
            continue
        chat_id = _telegram_chat_id_as_str(row.get("chat_id"))
        description = _as_str(row.get("description"))
        enabled = _is_enabled(row.get("enabled", 1))
        if route_key in routes:
            raise ValueError(f"telegram_routes duplicate route_key: {route_key!r}")
        routes[route_key] = TelegramRoute(
            route_key=route_key,
            chat_id=chat_id,
            enabled=enabled,
            description=description,
        )

    return routes, True