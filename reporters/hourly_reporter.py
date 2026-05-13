# reporters/hourly_reporter.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from analyzers.hourly_analyzer import HourlyDTO
from core.event_log import append_event
from core.rules_provider import get_snapshot_v2
from reporters.hourly_render_model import build_hourly_render_model


@dataclass(frozen=True)
class RenderedReport:
    text: str


def _apply_style(s: str, style: str) -> str:
    s = str(s).strip()
    if not s:
        return ""
    if style == "bold":
        return f"*{s}*"
    return s


_HOURLY_SECTION_LAYOUT_KEYS: dict[str, str] = {
    "payouts.title": "payouts",
    "payouts.items": "payouts",
    "payins.title": "payins",
    "payins.items": "payins",
}


def _hourly_section_id_for_layout_key(key: str) -> str | None:
    if not key:
        return None
    return _HOURLY_SECTION_LAYOUT_KEYS.get(key.strip())


def _hourly_layout_item_will_emit(*, val: Any, title: str, style: str) -> bool:
    if style == "hr":
        return True
    if val is None or val == "":
        return bool(title and style in ("bold", "text"))
    if isinstance(val, list):
        return bool(title) or bool(val)
    return True


def _render_by_snapshot_layout(*, view: str, render_model: Dict[str, Any]) -> str:
    snapshot = get_snapshot_v2(force_sync=False)

    layout_items = [
        x
        for x in snapshot.report_items
        if x.enabled
        and x.report_key == view
        and x.item_type == "layout_line"
    ]
    layout_items.sort(key=lambda x: (x.sort_order, x.item_key))

    if not layout_items:
        return ""

    out: List[str] = []
    hourly_section_lead_emitted: set[str] = set()

    for item in layout_items:
        key = str(item.source_key or "").strip()
        title = str(item.display_name or "").strip()
        style = str(item.comment or "").strip().lower() or "text"

        if key and key not in render_model:
            append_event(
                type="layout_key_missing",
                job_type=view,
                payload={
                    "view": view,
                    "key": key,
                    "item_key": item.item_key,
                    "section_key": item.section_key,
                },
            )
            continue

        val = render_model.get(key, None) if key else None

        def _hourly_prepend_section_blank_if_needed() -> None:
            if view != "hourly":
                return
            section = _hourly_section_id_for_layout_key(key)
            if not section or section in hourly_section_lead_emitted:
                return
            if not _hourly_layout_item_will_emit(val=val, title=title, style=style):
                return
            if not out or out[-1] == "":
                return
            out.append("")
            hourly_section_lead_emitted.add(section)

        if style == "hr":
            if val is None or str(val).strip() == "":
                out.append("_______________________")
            else:
                out.append(str(val).strip())
            continue

        if val is None or val == "":
            if title and style in ("bold", "text"):
                _hourly_prepend_section_blank_if_needed()
                out.append(_apply_style(title, style))
            continue

        if isinstance(val, list):
            _hourly_prepend_section_blank_if_needed()
            if title:
                out.append(_apply_style(title, style))
            for row in val:
                out.append(str(row).rstrip())
            continue

        _hourly_prepend_section_blank_if_needed()
        line = f"{title} {val}".strip() if title else str(val).strip()
        out.append(_apply_style(line, style))

    return "\n".join(out).strip()


def render_hourly(dto: HourlyDTO) -> RenderedReport:
    rm = build_hourly_render_model(dto)
    text = _render_by_snapshot_layout(view="hourly", render_model=rm.model)
    return RenderedReport(text=text.strip())