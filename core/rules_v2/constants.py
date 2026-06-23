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

# Phase 2 — telegram_routes (delivery destinations; sheet optional in workbook)
TELEGRAM_ROUTES_SHEET = "telegram_routes"
TELEGRAM_ROUTES_REQUIRED_COLUMNS = frozenset(
    {"route_key", "chat_id", "enabled", "description"}
)
TELEGRAM_CHAT_ID_EMERGENCY_ENV = "TELEGRAM_CHAT_ID_EMERGENCY"

ALLOWED_TELEGRAM_ROUTE_KEYS: frozenset[str] = frozenset(
    {
        "analiz_downloader_status",
        "analiz_conversion_report",
        "analiz_payout_report",
        "analiz_main_pipeline",
        "platform_hourly_report",
        "raccoon_hourly_payin_report",
        "raccoon_hourly_conversion_alert",
        "raccoon_wallet_report",
        "raccoon_daily_conversion_report",
        "raccoon_wallet_downloader_error",
        "platform_wallet_download_report",
        "conversion_wallet_editor",
        "bakai_rate_current",
        "bakai_rate_alert",
        "wallet_editor_registry_warnings",
        "wallet_editor_auto_enable",
        "wallet_editor_auto_enable_alert",
        "wallet_editor_registry_refresh",
    }
)

# Legacy ENV → route_key (one ENV may map to multiple routes; shadow compares each pair)
LEGACY_ENV_TO_TELEGRAM_ROUTE_KEYS: tuple[tuple[str, str], ...] = (
    ("TELEGRAM_CHAT_ID_ANALIZ", "analiz_downloader_status"),
    ("TELEGRAM_CHAT_ID_ANALIZ", "analiz_conversion_report"),
    ("TELEGRAM_CHAT_ID_ANALIZ", "analiz_payout_report"),
    ("TELEGRAM_CHAT_ID_ANALIZ", "analiz_main_pipeline"),
    ("TELEGRAM_CHAT_ID_HOURLY", "platform_hourly_report"),
    ("TELEGRAM_CHAT_ID_HOURLY_RACCOON", "raccoon_hourly_payin_report"),
    ("TELEGRAM_CHAT_ID_RACCOON_WALLET", "raccoon_hourly_conversion_alert"),
    ("TELEGRAM_CHAT_ID_RACCOON_WALLET", "raccoon_wallet_report"),
    ("TELEGRAM_CHAT_ID_RACCOON_WALLET", "raccoon_daily_conversion_report"),
    ("TELEGRAM_CHAT_ID_RACCOON_WALLET", "raccoon_wallet_downloader_error"),
    ("TELEGRAM_CHAT_ID_WALLET", "platform_wallet_download_report"),
    ("CONVERSION_WALLET_EDITOR", "conversion_wallet_editor"),
    ("CURRENT_RATE_BAKAI_CHAT_ID", "bakai_rate_current"),
    ("NEW_RATE_BAKAI_CHAT_ID", "bakai_rate_alert"),
)

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
    "script_job:hello_world": {
        "enabled": "bool",
        "telegram_route_report": "str",
    },
}