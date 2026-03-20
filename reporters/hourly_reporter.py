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

        if style == "hr":
            if val is None or str(val).strip() == "":
                out.append("_______________________")
            else:
                out.append(str(val).strip())
            continue

        if val is None or val == "":
            if title and style in ("bold", "text"):
                out.append(_apply_style(title, style))
            continue

        if isinstance(val, list):
            if title:
                out.append(_apply_style(title, style))
            for row in val:
                out.append(str(row).rstrip())
            continue

        line = f"{title} {val}".strip() if title else str(val).strip()
        out.append(_apply_style(line, style))

    return "\n".join(out).strip()


def render_hourly(dto: HourlyDTO) -> RenderedReport:
    rm = build_hourly_render_model(dto)
    text = _render_by_snapshot_layout(view="hourly", render_model=rm.model)
    return RenderedReport(text=text.strip())