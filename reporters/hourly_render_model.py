from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from analyzers.hourly_analyzer import HourlyDTO, HourlyMethodRow, HourlyRow
from core.config_manager import get_job_params
from core.rules_provider import get_snapshot_v2
from core.rules_v2.normalizers import normalize_key


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


def _source_partner_keys(snapshot, item_key: str) -> list[str]:
    return [
        str(m.member_key or "").strip()
        for m in _members_for_item(snapshot, item_key, member_type="source_partner")
        if str(m.member_key or "").strip()
    ]


def _method_lookup_key(value: object) -> str:
    return normalize_key(str(value or "").strip().upper()) or "uni"


def _build_payout_fact(
    dto: HourlyDTO,
) -> tuple[dict[tuple[str, str], HourlyMethodRow], dict[tuple[str, str, str], HourlyMethodRow]]:
    legacy: dict[tuple[str, str], HourlyMethodRow] = {}
    by_partner: dict[tuple[str, str, str], HourlyMethodRow] = {}

    for block in dto.payout:
        group_code = str(block.group_code or "").strip()
        if not group_code:
            continue

        for method in block.methods:
            method_key = _method_lookup_key(method.method_code)
            partner_key = str(method.partner_key or "").strip()
            legacy_key = (group_code, method_key)
            partner_key_tuple = (group_code, partner_key, method_key)

            if legacy_key not in legacy:
                legacy[legacy_key] = HourlyMethodRow(
                    method_code=method_key.upper(),
                    title=method.title,
                    amount=float(method.amount or 0.0),
                    comment=_clean_comment(method.comment),
                    partner_key=partner_key,
                )
            else:
                prev = legacy[legacy_key]
                merged_comment = _clean_comment(prev.comment) or _clean_comment(method.comment)
                legacy[legacy_key] = HourlyMethodRow(
                    method_code=prev.method_code,
                    title=prev.title or method.title,
                    amount=float(prev.amount or 0.0) + float(method.amount or 0.0),
                    comment=merged_comment,
                    partner_key=prev.partner_key,
                )

            if partner_key_tuple not in by_partner:
                by_partner[partner_key_tuple] = HourlyMethodRow(
                    method_code=method_key.upper(),
                    title=method.title,
                    amount=float(method.amount or 0.0),
                    comment=_clean_comment(method.comment),
                    partner_key=partner_key,
                )
            else:
                prev = by_partner[partner_key_tuple]
                merged_comment = _clean_comment(prev.comment) or _clean_comment(method.comment)
                by_partner[partner_key_tuple] = HourlyMethodRow(
                    method_code=prev.method_code,
                    title=prev.title or method.title,
                    amount=float(prev.amount or 0.0) + float(method.amount or 0.0),
                    comment=merged_comment,
                    partner_key=partner_key,
                )

    return legacy, by_partner


def _lookup_payout_fact(
    *,
    group_code: str,
    method_key: str,
    partner_keys: list[str],
    use_partner_lookup: bool,
    legacy_fact: dict[tuple[str, str], HourlyMethodRow],
    partner_fact: dict[tuple[str, str, str], HourlyMethodRow],
) -> HourlyMethodRow | None:
    if use_partner_lookup:
        for partner_key in partner_keys:
            fact = partner_fact.get((group_code, partner_key, method_key))
            if fact is not None:
                return fact
        return None
    return legacy_fact.get((group_code, method_key))


def _payout_line_label(method_item) -> str:
    return str(method_item.display_name or "").strip()


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


def _render_payout_items(dto: HourlyDTO, snapshot, *, hide_inactive_rows: bool) -> List[str]:
    group_section = "hourly.config_payouts"
    method_section = "hourly.config_payout_methods"

    legacy_fact, partner_fact = _build_payout_fact(dto)

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
            method_key = _method_lookup_key(method_item.method_key)
            partner_keys = _source_partner_keys(snapshot, method_item.item_key)
            use_partner_lookup = bool(partner_keys)
            fact = _lookup_payout_fact(
                group_code=group_code,
                method_key=method_key,
                partner_keys=partner_keys,
                use_partner_lookup=use_partner_lookup,
                legacy_fact=legacy_fact,
                partner_fact=partner_fact,
            )
            amount = float(fact.amount) if fact else 0.0

            comment = _clean_comment(method_item.comment)
            if not comment and fact:
                comment = _clean_comment(fact.comment)

            suffix = f" ({comment})" if comment else ""
            line_label = _payout_line_label(method_item)
            line = f" - {line_label} – {_fmt_amount(amount)}{suffix}"

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


def _payin_item_has_group_break_after(snapshot, item_key: str) -> bool:
    return any(
        m.member_type == "group_break_after" and str(m.member_key) == "1"
        for m in _members_for_item(snapshot, item_key)
    )


def _split_payin_items_into_segments(payin_items, snapshot) -> List[List]:
    segments: List[List] = []
    current: List = []
    for item in payin_items:
        current.append(item)
        if _payin_item_has_group_break_after(snapshot, item.item_key):
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return segments


def _render_payin_items(dto: HourlyDTO, snapshot, *, hide_inactive_rows: bool) -> List[str]:
    section_key = "hourly.config_payins"
    payin_items = _hourly_items(snapshot, section_key, item_type="payin_row")
    payin_fact = _build_payin_fact(dto)
    segments = _split_payin_items_into_segments(payin_items, snapshot)

    rendered_segments: List[List[str]] = []
    visible_idx = 0

    for segment in segments:
        segment_lines: List[str] = []
        for item in segment:
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
            segment_lines.append(f"{visible_idx}) {title} – {_fmt_amount(amount)}{suffix}")

        if segment_lines:
            rendered_segments.append(segment_lines)

    lines: List[str] = []
    for seg_idx, segment_lines in enumerate(rendered_segments):
        if seg_idx > 0:
            lines.append("")
        lines.extend(segment_lines)

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
