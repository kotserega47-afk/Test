from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from analyzers.wallet_analyzer import WalletStatsDTO, WalletPartnerStats, WalletAlertBlock
from datetime import datetime

@dataclass(frozen=True)
class WalletRenderModel:
    model: Dict[str, Any]


def _fmt_amount(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{int(round(float(value))):,}".replace(",", " ")


def _conversion_line(p: WalletPartnerStats) -> str:
    if p.total_ops == 0:
        return "  Конверсия: —"
    base = f"{p.success_ops} / {p.total_ops} = {p.conversion_pct:.1f}%"

    if p.conversion_insufficient:
        return f"  Конверсия: {base} — ℹ️ Недостаточно данных"

    if p.conversion_threshold_pct is None:
        return f"  Конверсия: {base} — ℹ️"

    icon = "🔴" if p.conversion_bad else "🟢"

    return (
        f"  Конверсия: {base} "
        f"(< {p.conversion_threshold_pct:.1f}%) — {icon}"
    )


def _payin_line(p: WalletPartnerStats) -> str:
    amount = _fmt_amount(p.payin_amount)

    if p.daily_limit is None:
        return f"  Поступления: {amount} — ℹ️"

    limit = _fmt_amount(p.daily_limit)
    icon = "🟢"
    if p.limit_bad:
        icon = "🔴"
    elif p.limit_warn:
        icon = "🟡"

    suffix = f" ({p.daily_limit_comment})" if p.daily_limit_comment else ""
    pct = f"{p.percent_filled}%" if p.percent_filled is not None else "—"
    return f"  Поступления: {amount} / {limit} ({pct}) — {icon}{suffix}"


def _api_line(p: WalletPartnerStats) -> str:


    if p.api_total_ops == 0:
        return "  Отмен по API: —"

    base = f"{p.api_cancel_count} / {p.api_total_ops} ({p.api_cancel_pct:.1f}%)"

    if p.api_insufficient:
        return f"  Отмен по API: {base} — ℹ️"

    icon = "🔴" if p.api_bad else "🟢"

    return f"  Отмен по API: {base} — {icon}"


def _last_success_line(p: WalletPartnerStats) -> str:

    if p.last_success_at is None:
        return "  Последний успех: —"

    exact = p.last_success_at.strftime("%d.%m %H:%M:%S")

    if p.last_success_minutes_ago is None:
        return f"  Последний успех: ({exact})"

    return (
        f"  Последний успех: "
        f"{p.last_success_minutes_ago} мин назад ({exact})"
    )

def _partner_block(p: WalletPartnerStats) -> List[str]:
    block = [
        p.partner,
        "",
        _conversion_line(p),
        _payin_line(p),
        _api_line(p),
    ]
    if p.nok_wallets_count > 0:
        block.append(f"  Нет доступных аккаунтов: {p.nok_wallets_count} — 🔴")
    block.append(_last_success_line(p))
    return block


def _alert_block(a: WalletAlertBlock) -> List[str]:
    return [a.partner, *a.lines]


def build_wallet_render_model(dto: WalletStatsDTO) -> WalletRenderModel:
    partners_blocks = [_partner_block(p) for p in dto.partners]
    alerts_title = "❗ Обнаружены отклонения:"
    alerts_blocks = [_alert_block(a) for a in dto.alerts]
    if not alerts_blocks:
        alerts_title = ""

    model = {
        "stuck.title": "⏳ Зависшие:",
        "stuck.items": [
            f"• Поступления: {dto.stuck_payins_count} шт",
            f"• Выплаты: {dto.stuck_payouts_count} шт",
        ],
        "stuck.spacer": "",

        "header.title": "📦 Wallet Analyzer",
        "header.window": f"🕒 Окно: {dto.window_minutes} мин (смещение {dto.offset_minutes})",
        "header.spacer": "",

        "partners.blocks": partners_blocks,
        "partners.spacer": "",

        "alerts.title": alerts_title,
        "alerts.blocks": alerts_blocks,

    }
    return WalletRenderModel(model=model)
