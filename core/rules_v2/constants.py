from __future__ import annotations

DEFAULT_TIMEZONE = "Europe/Moscow"
EXCEL_DATETIME_FORMAT = "%d.%m.%Y %H:%M:%S"
EXCEL_DATE_FORMAT = "%d.%m.%Y"
EXCEL_TIME_FORMAT = "%H:%M:%S"

REQUIRED_SHEETS_V2 = {
    "meta",
    "jobs",
    "partners",
    "partner_groups",
    "partner_group_members",
    "methods",
    "roles",
    "commands",
    "access_rules",
    "command_policies",
    "schedule_rules",
    "job_params",
    "limit_rules",
    "threshold_rules",
    "exclusion_rules",
    "reports",
    "report_sections",
    "report_items",
    "report_item_members",
}

SCOPE_TYPES = {"global", "group", "partner"}
SCHEDULE_TYPES = {"interval", "cron"}
VALUE_TYPES = {"int", "float", "str", "bool", "time", "json"}
LIMIT_TYPES = {"min", "max", "target", "soft_max", "hard_max"}
ITEM_TYPES = {"partner", "group", "metric", "aggregate", "layout_line", "payin_row", "payout_group", "payout_method"}
MEMBER_TYPES = {"partner", "group", "source_partner", "group_break_after", "parent_group_item"}

ALLOWED_JOB_PARAMS: dict[str, dict[str, str]] = {
    "wallet": {
        "window_minutes": "int",
        "offset_minutes": "int",
        "min_events": "int",
        "pending_payin_minutes": "int",
        "pending_payout_minutes": "int",
        "payin_days_back": "int",
        "payout_days_back": "int",
    },
    "raccoon_wallet": {
        "window_minutes": "int",
        "offset_minutes": "int",
        "min_events": "int",
        "pending_payin_minutes": "int",
        "payin_days_back": "int",
    },
    "hourly": {
        "intraday_interval_minutes": "int",
        "final_daily_time": "time",
        "include_empty_sections": "bool",
        "hide_inactive_rows": "bool",
    },
}