from analyzers.wallet_analyzer import build_wallet_stats_dto
from reporters.wallet_reporter import render_wallet
from datetime import datetime
from zoneinfo import ZoneInfo

payin = r"..."
payout = r"..."

dto = build_wallet_stats_dto(
    payin_path=payin,
    payout_path=payout,
    now=datetime.now(ZoneInfo("Europe/Moscow")),
)

print("partners:", len(dto.partners))
print("alerts:", len(dto.alerts))

report = render_wallet(dto)

print(report.main_text[:500])
print("----")
print(report.alerts_text[:500])