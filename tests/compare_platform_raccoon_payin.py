"""Explicit Platform payin format compare. Not collected without --platform-checkout."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.isolated_child_env import isolated_child_env, missing_dependency_hint

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA = (ROOT / "tests" / "fixtures" / "behavior_baseline" / "expected_platform_head.txt").read_text(
    encoding="utf-8"
).strip()
_GOLDEN = ROOT / "tests" / "fixtures" / "raccoon" / "golden" / "expected_payin_by_method.txt"

_DRIVER = r"""
import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import analyzers.raccoon_hourly_report as report
from utils.normalization import normalize_partner_name

root = Path(os.environ["PLATFORM_CHECKOUT"]).resolve()
mod_path = Path(report.__file__).resolve()
if not mod_path.is_relative_to(root):
    raise SystemExit(f"import not from checkout: {mod_path} vs {root}")

MSK = ZoneInfo("Europe/Moscow")
rows = [
    {"partner": "Alpha (1)", "amount": 100000, "method": "SBP"},
    {"partner": "Beta (2)", "amount": 50000, "method": "SBP"},
    {"partner": "Alpha (1)", "amount": 25000, "method": "Card"},
]
out = []
for r in rows:
    out.append(
        {
            "Партнер": r["partner"],
            "norm": normalize_partner_name(r["partner"]),
            "Сумма": r["amount"],
            "method_display": r["method"],
            "Дата/Время создания": datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
        }
    )
df = pd.DataFrame(out)
blocks = report.aggregate_payin_by_method(df)
txt = report.format_report(
    blocks,
    date(2026, 5, 20),
    datetime(2026, 5, 20, 14, 30, tzinfo=MSK),
    float(df["Сумма"].sum()),
)
Path(os.environ["BASELINE_PLATFORM_OUT"]).write_text(
    json.dumps({"text": txt, "module": str(mod_path)}, ensure_ascii=False),
    encoding="utf-8",
)
"""


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip() + "\n"


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.platform_compare
def test_platform_payin_format_matches_handwritten_golden(pytestconfig: pytest.Config, tmp_path: Path) -> None:
    raw = pytestconfig.getoption("--platform-checkout")
    assert raw, "internal: compare file should not collect without --platform-checkout"
    checkout = Path(raw).expanduser().resolve()
    if not checkout.is_dir():
        pytest.fail(f"Platform checkout is not a directory: {checkout}")

    head = _git(checkout, "rev-parse", "HEAD")
    if head.returncode != 0:
        pytest.fail(f"git rev-parse failed in {checkout}: {head.stderr or head.stdout}")
    sha = head.stdout.strip()
    if sha != EXPECTED_SHA:
        pytest.fail(
            f"Platform checkout HEAD {sha} != required {EXPECTED_SHA}. "
            "Compare is pinned to survey F26 / Platform develop."
        )
    dirty = _git(checkout, "status", "--porcelain")
    if dirty.returncode != 0:
        pytest.fail(f"git status failed in {checkout}: {dirty.stderr or dirty.stdout}")
    if dirty.stdout.strip():
        pytest.fail(f"Platform checkout is not clean:\n{dirty.stdout}")

    report_py = checkout / "analyzers" / "raccoon_hourly_report.py"
    if not report_py.is_file():
        pytest.fail(f"missing {report_py}")

    out_path = tmp_path / "platform_payin.json"
    driver = tmp_path / "platform_payin_driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    env = isolated_child_env(
        tmp_path / "sandbox",
        pythonpath=str(checkout),
        extra={
            "PLATFORM_CHECKOUT": str(checkout),
            "BASELINE_PLATFORM_OUT": str(out_path),
            "TELEGRAM_CHAT_ID_HOURLY_RACCOON": "-payin-10m-chat",
            "TELEGRAM_CHAT_ID_RACCOON_WALLET": "-hourly-wallet-chat",
            "TELEGRAM_BOT_TOKEN": "123456:platform-compare-dummy",
            "TG_BOT_TOKEN": "123456:platform-compare-dummy",
        },
    )
    (tmp_path / "cwd").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(driver)],
        cwd=str(tmp_path / "cwd"),
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr + proc.stdout
        if "ModuleNotFoundError" in err:
            pytest.fail(missing_dependency_hint(err))
        pytest.fail(err)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    module_path = Path(payload["module"]).resolve()
    assert module_path.is_relative_to(checkout)
    expected = _GOLDEN.read_text(encoding="utf-8")
    assert _norm(payload["text"]) == _norm(expected)
