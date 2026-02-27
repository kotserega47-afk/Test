# analyzers/hourly_report.py
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from core.event_log import append_event
from core.job_state import get_last_fingerprint

from integrations.hourly_downloader import run_hourly_cycle
from analyzers.hourly_analyzer import build_hourly_dto_from_files
from reporters.hourly_reporter import render_hourly


MSK = ZoneInfo("Europe/Moscow")

HOURLY_DIR = Path("/tmp/hourly")
PAYIN_PATH = str(HOURLY_DIR / "payin.xlsx")
PAYOUT_PATH = str(HOURLY_DIR / "payout.xlsx")


@dataclass(frozen=True)
class HourlyRunResult:
    skipped_no_changes: bool
    fingerprint: Optional[str]
    text: str


def _target_header_date(now: datetime) -> datetime:
    # contract: in 00:xx we download previous day
    if now.hour == 0:
        now = now - timedelta(days=1)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _file_meta(p: str) -> Optional[dict]:
    pp = Path(p)
    if not pp.exists():
        return None
    st = pp.stat()
    return {"path": str(pp), "size": st.st_size, "mtime": st.st_mtime}


def _calc_fp(payin_path: str, payout_path: str) -> Optional[str]:
    a = _file_meta(payin_path)
    b = _file_meta(payout_path)
    if not a or not b:
        return None
    raw = json.dumps({"payin": a, "payout": b}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run_hourly_report(*, job: str = "hourly") -> HourlyRunResult:
    """
    Rule-driven hourly pipeline (NO transport, NO fp commit):
      - download
      - fingerprint compare (state.json via job_state)
      - skip/no-changes -> event_log only
      - analyzer -> DTO
      - reporter -> text
      - returns (fp, text). fp must be committed only AFTER successful send.
    """
    # 1) Download files (today / yesterday @ 00:xx handled inside downloader)
    run_hourly_cycle()

    fp = _calc_fp(PAYIN_PATH, PAYOUT_PATH)
    if not fp:
        append_event(type="job_skipped_missing_inputs", job_type="hourly")
        return HourlyRunResult(skipped_no_changes=True, fingerprint=None, text="")

    last = get_last_fingerprint("hourly")
    if last == fp:
        append_event(type="job_skipped_no_changes", job_type="hourly", payload={"fingerprint": fp[:10]})
        return HourlyRunResult(skipped_no_changes=True, fingerprint=fp, text="")

    # 2) Build DTO window
    now = datetime.now(MSK)
    header_date = _target_header_date(now)
    start_dt = header_date
    end_dt = header_date.replace(hour=23, minute=59, second=0, microsecond=0) if header_date.date() < now.date() else now

    dto = build_hourly_dto_from_files(
        payin_path=PAYIN_PATH,
        payout_path=PAYOUT_PATH,
        start_dt=start_dt,
        end_dt=end_dt,
        header_date=header_date,
    )

    # 3) Render text (layout from rules.xlsx job_params inside reporter)
    rendered = render_hourly(dto, job=job)

    return HourlyRunResult(skipped_no_changes=False, fingerprint=fp, text=rendered.text)