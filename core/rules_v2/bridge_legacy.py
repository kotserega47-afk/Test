from __future__ import annotations

from pathlib import Path

import pandas as pd

from .models import MetaInfo, RulesSnapshotV2


def load_legacy_workbook(path: str | Path) -> dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(path)
    return {sheet: pd.read_excel(path, sheet_name=sheet).rename(columns=lambda c: str(c).strip()) for sheet in xls.sheet_names}


def build_snapshot_v2_from_legacy(path: str | Path) -> RulesSnapshotV2:
    sheets = load_legacy_workbook(path)

    meta = _build_meta(sheets)
    snapshot = RulesSnapshotV2(meta=meta)

    # Временный порядок загрузки
    snapshot.jobs = _build_jobs(sheets)
    snapshot.methods = _build_methods(sheets)
    snapshot.roles = _build_roles(sheets)
    snapshot.commands = _build_commands(sheets)

    snapshot.partners = _build_partners(sheets)
    snapshot.partner_groups = _build_partner_groups(sheets)
    snapshot.partner_group_members = _build_partner_group_members(sheets)

    snapshot.access_rules = _build_access_rules(sheets)
    snapshot.command_policies = _build_command_policies(sheets)
    snapshot.schedule_rules = _build_schedule_rules(sheets)
    snapshot.job_params = _build_job_params(sheets)

    snapshot.limit_rules = _build_limit_rules(sheets)
    snapshot.threshold_rules = _build_threshold_rules(sheets)
    snapshot.exclusion_rules = _build_exclusion_rules(sheets)

    snapshot.reports = _build_reports(sheets)
    snapshot.report_sections = _build_report_sections(sheets)
    snapshot.report_items = _build_report_items(sheets)
    snapshot.report_item_members = _build_report_item_members(sheets)

    return snapshot


def _build_meta(sheets: dict[str, pd.DataFrame]) -> MetaInfo:
    return MetaInfo(
        ruleset_version="legacy-bridge",
        updated_at=pd.Timestamp.now().to_pydatetime(),
        updated_by="bridge_legacy",
        comment="Built from legacy workbook",
        is_active=True,
    )


def _build_jobs(sheets: dict[str, pd.DataFrame]) -> dict:
    return {}


def _build_methods(sheets: dict[str, pd.DataFrame]) -> dict:
    return {}


def _build_roles(sheets: dict[str, pd.DataFrame]) -> dict:
    return {}


def _build_commands(sheets: dict[str, pd.DataFrame]) -> dict:
    return {}


def _build_partners(sheets: dict[str, pd.DataFrame]) -> dict:
    return {}


def _build_partner_groups(sheets: dict[str, pd.DataFrame]) -> dict:
    return {}


def _build_partner_group_members(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_access_rules(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_command_policies(sheets: dict[str, pd.DataFrame]) -> dict:
    return {}


def _build_schedule_rules(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_job_params(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_limit_rules(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_threshold_rules(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_exclusion_rules(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_reports(sheets: dict[str, pd.DataFrame]) -> dict:
    return {}


def _build_report_sections(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_report_items(sheets: dict[str, pd.DataFrame]) -> list:
    return []


def _build_report_item_members(sheets: dict[str, pd.DataFrame]) -> list:
    return []