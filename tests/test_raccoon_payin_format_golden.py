"""Independent golden for Raccoon payin-by-method formatting (Test SHA).

Expected text was written by hand from the synthetic rows (sort by method
total desc, then partner amount desc, fmt_int thousands). The test must not
rebuild expected via format_report.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:ABCDEF-test-token-for-unittest")
os.environ.setdefault("TELEGRAM_CHAT_ID_HOURLY_RACCOON", "-payin-10m-chat")
os.environ.setdefault("TELEGRAM_CHAT_ID_RACCOON_WALLET", "-hourly-wallet-chat")

from analyzers.raccoon_hourly_report import (  # noqa: E402
    aggregate_payin_by_method,
    format_report,
)
from utils.normalization import normalize_partner_name  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
_GOLDEN = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "raccoon"
    / "golden"
    / "expected_payin_by_method.txt"
)
_PLATFORM = Path(__file__).resolve().parents[1].parent / "Platform_2.0_survey"


def _synth_df() -> pd.DataFrame:
    rows = [
        {"Партнер": "Alpha (1)", "Сумма": 100000, "method_display": "SBP"},
        {"Партнер": "Beta (2)", "Сумма": 50000, "method_display": "SBP"},
        {"Партнер": "Alpha (1)", "Сумма": 25000, "method_display": "Card"},
    ]
    out = []
    for r in rows:
        out.append(
            {
                "Партнер": r["Партнер"],
                "norm": normalize_partner_name(r["Партнер"]),
                "Сумма": r["Сумма"],
                "method_display": r["method_display"],
                "Дата/Время создания": datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
            }
        )
    return pd.DataFrame(out)


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip() + "\n"


def test_raccoon_payin_format_matches_handwritten_golden() -> None:
    df = _synth_df()
    blocks = aggregate_payin_by_method(df)
    actual = format_report(
        blocks,
        date(2026, 5, 20),
        datetime(2026, 5, 20, 14, 30, tzinfo=MSK),
        float(df["Сумма"].sum()),
    )
    expected = _GOLDEN.read_text(encoding="utf-8")
    assert _norm(actual) == _norm(expected)


def test_raccoon_payin_format_same_on_platform_survey_clone(tmp_path: Path) -> None:
    """Same synthetic rows on Platform develop SHA, separate tree. Do not copy modules."""
    if not (_PLATFORM / "analyzers" / "raccoon_hourly_report.py").is_file():
        pytest.skip(f"Platform survey clone not found at {_PLATFORM}")
    out_path = tmp_path / "platform_payin.txt"
    driver = tmp_path / "platform_payin_driver.py"
    driver.write_text(
        "from datetime import date, datetime\n"
        "from pathlib import Path\n"
        "from zoneinfo import ZoneInfo\n"
        "import os\n"
        "import pandas as pd\n"
        "from utils.normalization import normalize_partner_name\n"
        "from analyzers.raccoon_hourly_report import aggregate_payin_by_method, format_report\n"
        "MSK = ZoneInfo('Europe/Moscow')\n"
        "rows = [\n"
        "    {'partner': 'Alpha (1)', 'amount': 100000, 'method': 'SBP'},\n"
        "    {'partner': 'Beta (2)', 'amount': 50000, 'method': 'SBP'},\n"
        "    {'partner': 'Alpha (1)', 'amount': 25000, 'method': 'Card'},\n"
        "]\n"
        "out = []\n"
        "for r in rows:\n"
        "    out.append({\n"
        "        'Партнер': r['partner'],\n"
        "        'norm': normalize_partner_name(r['partner']),\n"
        "        'Сумма': r['amount'],\n"
        "        'method_display': r['method'],\n"
        "        'Дата/Время создания': datetime(2026, 5, 20, 12, 0, tzinfo=MSK),\n"
        "    })\n"
        "df = pd.DataFrame(out)\n"
        "blocks = aggregate_payin_by_method(df)\n"
        "txt = format_report(blocks, date(2026, 5, 20), datetime(2026, 5, 20, 14, 30, tzinfo=MSK), float(df['Сумма'].sum()))\n"
        "Path(os.environ['BASELINE_PLATFORM_OUT']).write_text(txt, encoding='utf-8')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_PLATFORM)
    env["PYTHONIOENCODING"] = "utf-8"
    env["TELEGRAM_CHAT_ID_HOURLY_RACCOON"] = "-payin-10m-chat"
    env["TELEGRAM_CHAT_ID_RACCOON_WALLET"] = "-hourly-wallet-chat"
    env["TELEGRAM_BOT_TOKEN"] = "123456:ABCDEF-test-token-for-unittest"
    env["BASELINE_PLATFORM_OUT"] = str(out_path)
    proc = subprocess.run(
        [sys.executable, str(driver)],
        cwd=str(_PLATFORM),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        if "ModuleNotFoundError" in (proc.stderr or ""):
            pytest.skip(
                "Platform raccoon_hourly_report could not import on this interpreter "
                f"(sys.executable={sys.executable}): {proc.stderr[-500:]}"
            )
        pytest.fail(proc.stderr + proc.stdout)
    expected = _GOLDEN.read_text(encoding="utf-8")
    assert _norm(out_path.read_text(encoding="utf-8")) == _norm(expected)
