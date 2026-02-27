# reporters/hourly_reporter.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from analyzers.hourly_analyzer import HourlyDTO, HourlyPayoutBlock, HourlyRow, HourlyMethodRow
from core.config_manager import get_job_params


@dataclass(frozen=True)
class RenderedReport:
    text: str


def _fmt_int(v: float) -> str:
    try:
        n = int(round(float(v)))
        return f"{n:,}".replace(",", " ")
    except Exception:
        return "0"


def render_hourly(dto: HourlyDTO, *, job: str = "hourly") -> RenderedReport:
    """
    Pure rendering.
    Ordering / layout is controlled via rules.xlsx -> job_params (json).
    No Telegram here.
    """
    params = get_job_params(job=job)  # already rules-driven

    # layout JSON example (stored in job_params.value for key=layout_json, type=json):
    # {
    #   "payout_layout": [{"group": ["A-мобайл", "АБХСбер (116)"], "spacing": 1}],
    #   "payin_layout":  [{"group": ["A-мобайл", "Aurora"], "spacing": 0}]
    # }
    layout = params.get("layout_json") or {}
    payout_layout = layout.get("payout_layout") or []
    payin_layout = layout.get("payin_layout") or []

    # build fast lookup by title
    payout_by_title: Dict[str, HourlyPayoutBlock] = {b.title: b for b in dto.payout}
    payin_by_title: Dict[str, HourlyRow] = {r.title: r for r in dto.payin}

    lines: List[str] = []
    lines.append(
        f"Данные на {dto.header_date.strftime('%d.%m')} с 00:00 по {dto.end_dt.strftime('%H:%M')}"
    )
    lines.append("")

    # PAYOUT
    lines.append("Выплаты:")
    counter = 1
    if payout_layout:
        for block in payout_layout:
            group = block.get("group") or []
            spacing = int(block.get("spacing") or 0)
            for key in group:
                b = payout_by_title.get(key)
                if not b:
                    continue
                lines.append(f"{counter}) {b.title}:")
                for m in b.methods:
                    c = f" ({m.comment})" if m.comment else ""
                    lines.append(f" - {m.title} – {_fmt_int(m.amount)}{c}")
                counter += 1
            for _ in range(spacing):
                lines.append("")
    else:
        # fallback: natural order
        for b in dto.payout:
            lines.append(f"{counter}) {b.title}:")
            for m in b.methods:
                c = f" ({m.comment})" if m.comment else ""
                lines.append(f" - {m.title} – {_fmt_int(m.amount)}{c}")
            counter += 1

    lines.append("_______________________")
    lines.append("")

    # PAYIN
    lines.append("Поступления:")
    counter = 1
    if payin_layout:
        for block in payin_layout:
            group = block.get("group") or []
            spacing = int(block.get("spacing") or 0)
            for key in group:
                r = payin_by_title.get(key)
                if not r:
                    continue
                c = f" ({r.comment})" if r.comment else ""
                lines.append(f"{counter}) {r.title} – {_fmt_int(r.amount)}{c}")
                counter += 1
            for _ in range(spacing):
                lines.append("")
    else:
        for r in dto.payin:
            c = f" ({r.comment})" if r.comment else ""
            lines.append(f"{counter}) {r.title} – {_fmt_int(r.amount)}{c}")
            counter += 1

    return RenderedReport(text="\n".join(lines))
