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
    from core.config_manager import (
        get_hourly_payins_df,
        get_hourly_payouts_df,
        get_hourly_payout_methods_df,
    )

    # ===== загрузка rules =====

    payins_df = get_hourly_payins_df()
    payouts_df = get_hourly_payouts_df()
    methods_df = get_hourly_payout_methods_df()

    # ===== индексы факта =====

    fact_payins = {r.entity_code: r for r in dto.payin}

    fact_payout = {}
    for block in dto.payout:
        for m in block.methods:
            fact_payout[(block.group_code, m.method_code)] = m

    # ===== payouts =====

    payout_items: List[str] = []

    groups = payouts_df[payouts_df["enabled"] == 1].sort_values("sort_order")

    for i, (_, g) in enumerate(groups.iterrows(), start=1):

        group_code = g["group_code"]
        title = g["display_name"]

        payout_items.append(f"{i}) {title}:")

        methods = (
            methods_df[
                (methods_df["group_code"] == group_code)
                & (methods_df["enabled"] == 1)
                ]
            .sort_values("sort_order")
        )

        for _, m in methods.iterrows():
            key = (group_code, m["method_code"])
            fact = fact_payout.get(key)

            amount = fact.amount if fact else 0

            comment = m.get("comment", "")
            c = f" ({comment})" if comment else ""

            payout_items.append(
                f" - {m['method_name']} – {_fmt_amount(amount)}{c}"
            )

        if int(g.get("group_break_after", 0)) == 1:
            payout_items.append("")

    # ===== payins =====

    payin_items: List[str] = []

    rows = payins_df[payins_df["enabled"] == 1].sort_values("sort_order")

    for i, (_, r) in enumerate(rows.iterrows(), start=1):

        key = r["entity_code"]
        fact = fact_payins.get(key)

        amount = fact.amount if fact else 0

        comment = r.get("comment", "")
        c = f" ({comment})" if comment else ""

        payin_items.append(
            f"{i}) {r['display_name']} – {_fmt_amount(amount)}{c}"
        )

        if int(r.get("group_break_after", 0)) == 1:
            payin_items.append("")