from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from analyzers.hourly_analyzer import HourlyDTO, HourlyMethodRow, HourlyRow
from core.config_manager import get_job_params
from core.rules_provider import get_snapshot_v2


@dataclass(frozen=True)
class HourlyRenderModel:
    model: Dict[str, Any]


def _fmt_amount(v: object) -> str:
    try:
        n = int(round(float(v or 0)))
        return f"{n:,}".replace(",", " ")
    except Exception:
        return "0"


def _clean_comment(value: object) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in {"", "nan", "none"}:
        return ""
    return s


def _is_active_row(*, amount: float, count: int | None = None) -> bool:
    """
    Activity for hourly line rendering. Comment-only (amount 0) is not active.
    When ``count`` is added to DTO later: count > 0 or amount != 0.
    """
    if count is not None:
        return count > 0 or amount != 0.0
    return amount != 0.0


def _finalize_hourly_section_lines(lines: List[str], *, hide_inactive_rows: bool) -> List[str]:
    if not hide_inactive_rows:
        return lines
    out: List[str] = []
    prev_blank = False
    for line in lines:
        is_blank = line == ""
        if is_blank:
            if prev_blank:
                continue
            out.append("")
            prev_blank = True
        else:
            out.append(line)
            prev_blank = False
    while out and out[-1] == "":
        out.pop()
    return out


def _build_period_text(dto: HourlyDTO) -> str:
    day = dto.header_date.strftime("%d.%m")
    start_hm = dto.start_dt.strftime("%H:%M")
    end_hm = dto.end_dt.strftime("%H:%M")
    return f"Данные на {day} с {start_hm} по {end_hm}"


def _hourly_items(snapshot, section_key: str, item_type: str | None = None):
    items = [
        x
        for x in snapshot.report_items
        if x.enabled and x.report_key == "hourly" and x.section_key == section_key
    ]
    if item_type is not None:
        items = [x for x in items if x.item_type == item_type]
    items.sort(key=lambda x: (x.sort_order, x.item_key))
    return items


def _members_for_item(snapshot, item_key: str, member_type: str | None = None):
    members = [
        x for x in snapshot.report_item_members
        if x.enabled and x.item_key == item_key
    ]
    if member_type is not None:
        members = [x for x in members if x.member_type == member_type]
    members.sort(key=lambda x: (x.sort_order, x.member_key))
    return members


def _build_payin_fact(dto: HourlyDTO) -> dict[str, HourlyRow]:
    out: dict[str, HourlyRow] = {}
    for row in dto.payin:
        key = str(row.entity_code or "").strip()
        if not key:
            continue

        if key not in out:
            out[key] = row
        else:
            prev = out[key]
            merged_comment = _clean_comment(prev.comment) or _clean_comment(row.comment)
            out[key] = HourlyRow(
                entity_code=key,
                title=prev.title or row.title,
                amount=float(prev.amount or 0.0) + float(row.amount or 0.0),
                comment=merged_comment,
            )
    return out


def _build_payout_fact(dto: HourlyDTO) -> dict[tuple[str, str], HourlyMethodRow]:
    out: dict[tuple[str, str], HourlyMethodRow] = {}

    for block in dto.payout:
        group_code = str(block.group_code or "").strip()
        if not group_code:
            continue

        for method in block.methods:
            method_key = str(method.method_code or "").strip().lower() or "uni"
            key = (group_code, method_key)

            if key not in out:
                out[key] = HourlyMethodRow(
                    method_code=method_key.upper(),
                    title=method.title,
                    amount=float(method.amount or 0.0),
                    comment=_clean_comment(method.comment),
                )
            else:
                prev = out[key]
                merged_comment = _clean_comment(prev.comment) or _clean_comment(method.comment)
                out[key] = HourlyMethodRow(
                    method_code=prev.method_code,
                    title=prev.title or method.title,
                    amount=float(prev.amount or 0.0) + float(method.amount or 0.0),
                    comment=merged_comment,
                )

    return out


def _render_payout_items(dto: HourlyDTO, snapshot, *, hide_inactive_rows: bool) -> List[str]:
    group_section = "hourly.config_payouts"
    method_section = "hourly.config_payout_methods"

    payout_fact = _build_payout_fact(dto)

    method_items = _hourly_items(snapshot, method_section, item_type="payout_method")
    group_items = _hourly_items(snapshot, group_section, item_type="payout_group")

    methods_by_group: dict[str, list] = {}
    for item in method_items:
        group_code = str(item.source_key or "").strip()
        if not group_code:
            continue
        methods_by_group.setdefault(group_code, []).append(item)

    break_after_groups: set[str] = set()
    for group_item in group_items:
        has_break = any(
            m.member_type == "group_break_after" and str(m.member_key) == "1"
            for m in _members_for_item(snapshot, group_item.item_key)
        )
        if has_break:
            break_after_groups.add(group_item.item_key)

    lines: List[str] = []
    visible_group_idx = 0

    for group_ord, group_item in enumerate(group_items, start=1):
        group_code = str(group_item.source_key or "").strip()
        title = str(group_item.display_name or group_code).rstrip(":").strip()

        if not group_code:
            continue

        group_methods = methods_by_group.get(group_code, [])
        method_lines: List[str] = []

        for method_item in group_methods:
            method_key = str(method_item.method_key or "").strip().lower() or "uni"
            fact = payout_fact.get((group_code, method_key))
            amount = float(fact.amount) if fact else 0.0

            comment = _clean_comment(method_item.comment)
            if not comment and fact:
                comment = _clean_comment(fact.comment)

            suffix = f" ({comment})" if comment else ""
            method_name = str(method_item.display_name or method_key.upper()).strip()
            line = f" - {method_name} – {_fmt_amount(amount)}{suffix}"

            if hide_inactive_rows:
                if _is_active_row(amount=amount, count=None):
                    method_lines.append(line)
            else:
                method_lines.append(line)

        if hide_inactive_rows and not method_lines:
            continue

        header_idx = visible_group_idx + 1 if hide_inactive_rows else group_ord
        if hide_inactive_rows:
            visible_group_idx = header_idx
        lines.append(f"{header_idx}) {title}:")
        lines.extend(method_lines)

        if group_item.item_key in break_after_groups:
            lines.append("")

    return _finalize_hourly_section_lines(lines, hide_inactive_rows=hide_inactive_rows)


def _render_payin_items(dto: HourlyDTO, snapshot, *, hide_inactive_rows: bool) -> List[str]:
    section_key = "hourly.config_payins"
    payin_items = _hourly_items(snapshot, section_key, item_type="payin_row")
    payin_fact = _build_payin_fact(dto)

    lines: List[str] = []
    visible_idx = 0

    for item in payin_items:
        source_key = str(item.source_key or "").strip()
        fact = payin_fact.get(source_key)

        amount = float(fact.amount) if fact else 0.0
        if hide_inactive_rows and not _is_active_row(amount=amount, count=None):
            continue

        visible_idx += 1
        comment = _clean_comment(item.comment)
        if not comment and fact:
            comment = _clean_comment(fact.comment)

        suffix = f" ({comment})" if comment else ""
        title = str(item.display_name or source_key).strip()

        lines.append(f"{visible_idx}) {title} – {_fmt_amount(amount)}{suffix}")

        has_break = any(
            m.member_type == "group_break_after" and str(m.member_key) == "1"
            for m in _members_for_item(snapshot, item.item_key)
        )
        if has_break:
            lines.append("")

    return _finalize_hourly_section_lines(lines, hide_inactive_rows=hide_inactive_rows)


def build_hourly_render_model(dto: HourlyDTO) -> HourlyRenderModel:
    snapshot = get_snapshot_v2(force_sync=False)
    hide_inactive_rows = bool(get_job_params(job="hourly", force_sync=False).get("hide_inactive_rows", False))

    model: Dict[str, Any] = {
        "header.period": _build_period_text(dto),
        "payouts.title": "Выплаты:",
        "payouts.items": _render_payout_items(dto, snapshot, hide_inactive_rows=hide_inactive_rows),
        "separator.line": "",
        "payins.title": "Поступления:",
        "payins.items": _render_payin_items(dto, snapshot, hide_inactive_rows=hide_inactive_rows),
    }

    return HourlyRenderModel(model=model)
