from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from analyzers.hourly_analyzer import HourlyDTO


@dataclass(frozen=True)
class HourlyRenderModel:
    model: Dict[str, Any]


def _fmt_amount(v) -> str:
    """
    Contract: amount formatting is allowed here (presentation layer),
    reporter must not format anything.
    """
    try:
        n = int(round(float(v)))
        return f"{n:,}".replace(",", " ")
    except Exception:
        return "0"


def build_hourly_render_model(dto: HourlyDTO) -> HourlyRenderModel:
    # 1) payouts (nested)
    payout_items: List[str] = []
    for b in dto.payout:
        payout_items.append(f"{b.title}:")
        for m in b.methods:
            c = f" ({m.comment})" if m.comment else ""
            payout_items.append(f" - {m.title} – {_fmt_amount(m.amount)}{c}")

    # 2) payins (flat)
    payin_items: List[str] = []
    for r in dto.payin:
        c = f" ({r.comment})" if r.comment else ""
        payin_items.append(f"{r.title} – {_fmt_amount(r.amount)}{c}")

    model: Dict[str, Any] = {
        "header.period": f"на {dto.header_date.strftime('%d.%m')} с 00:00 по {dto.end_dt.strftime('%H:%M')}",
        "payouts.items": payout_items,
        "separator.line": "_______________________",
        "payins.items": payin_items,
    }
    return HourlyRenderModel(model=model)