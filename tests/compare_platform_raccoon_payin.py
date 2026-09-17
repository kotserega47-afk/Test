"""Explicit Platform payin format compare. Not collected without --platform-checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.isolated_child_env import isolated_child_env, missing_dependency_hint

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "unit" / "platform_compare_harness"
EXPECTED_SHA = (ROOT / "tests" / "fixtures" / "behavior_baseline" / "expected_platform_head.txt").read_text(
    encoding="utf-8"
).strip()
_GOLDEN = ROOT / "tests" / "fixtures" / "raccoon" / "golden" / "expected_payin_by_method.txt"

_DRIVER = r"""
import json
import os
import socket
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import analyzers.raccoon_hourly_report as report
from core.rules_provider import get_rules_snapshot
from integrations.telegram_bot import send_message_sync
from utils.normalization import normalize_partner_name

root = Path(os.environ["PLATFORM_CHECKOUT"]).resolve()
mod_path = Path(report.__file__).resolve()
norm_path = Path(sys.modules["utils.normalization"].__file__).resolve()
if not mod_path.is_relative_to(root):
    raise SystemExit(f"import not from checkout: {mod_path} vs {root}")
if not norm_path.is_relative_to(root):
    raise SystemExit(f"normalize_partner_name not from checkout: {norm_path}")

def _caught(fn):
    try:
        fn()
    except Exception as exc:
        return type(exc).__name__ + ": " + str(exc)
    return "NO_EXCEPTION"

isolation = {
    "telegram_bot_file": getattr(sys.modules.get("integrations.telegram_bot"), "__file__", None),
    "rules_provider_file": getattr(sys.modules.get("core.rules_provider"), "__file__", None),
    "normalization_file": str(norm_path),
    "report_file": str(mod_path),
    "dotenv_in_sys_modules": "dotenv" in sys.modules,
    "dropbox_watcher_in_sys_modules": "integrations.dropbox_watcher" in sys.modules,
    "telegram_pkg_in_sys_modules": "telegram" in sys.modules,
    "send_message_sync_error": _caught(lambda: send_message_sync("-1", "probe")),
    "get_rules_snapshot_error": _caught(lambda: get_rules_snapshot()),
    "thread_start_error": _caught(
        lambda: threading.Thread(target=lambda: None, name="platform-compare-probe").start()
    ),
    "network_error": _caught(lambda: socket.create_connection(("127.0.0.1", 1), timeout=0.2)),
}

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
    json.dumps({"text": txt, "module": str(mod_path), "isolation": isolation}, ensure_ascii=False),
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

    sandbox = tmp_path / "sandbox"
    out_path = tmp_path / "platform_payin.json"
    events_path = sandbox / "isolation_events.json"
    driver = tmp_path / "platform_payin_driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    env = isolated_child_env(
        sandbox,
        pythonpath=os.pathsep.join((str(HARNESS), str(checkout))),
        extra={
            "PLATFORM_CHECKOUT": str(checkout),
            "BASELINE_PLATFORM_OUT": str(out_path),
            "PLATFORM_COMPARE_EVENTS_PATH": str(events_path),
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
    isolation = payload["isolation"]
    assert str(isolation["telegram_bot_file"]).startswith(
        "<platform-compare-stub:integrations.telegram_bot>"
    )
    assert str(isolation["rules_provider_file"]).startswith(
        "<platform-compare-stub:core.rules_provider>"
    )
    assert Path(isolation["normalization_file"]).resolve().is_relative_to(checkout)
    assert isolation["dotenv_in_sys_modules"] is False
    assert isolation["dropbox_watcher_in_sys_modules"] is False
    assert isolation["telegram_pkg_in_sys_modules"] is False
    assert "send_message_sync blocked in platform compare" in isolation["send_message_sync_error"]
    assert "get_rules_snapshot blocked in platform compare" in isolation["get_rules_snapshot_error"]
    assert "Thread.start blocked in platform compare" in isolation["thread_start_error"]
    assert "blocked in platform compare" in isolation["network_error"]
    events = json.loads(events_path.read_text(encoding="utf-8"))
    assert events["send_message_sync_calls"] >= 1
    assert events["get_rules_snapshot_calls"] >= 1
    assert events["thread_rejected"]
    kinds = {item["kind"] for item in events["blocked"]}
    assert "send_message_sync" in kinds
    assert "get_rules_snapshot" in kinds
    assert "create_connection" in kinds
    assert events["dotenv_load_calls"] == 0
    expected = _GOLDEN.read_text(encoding="utf-8")
    assert _norm(payload["text"]) == _norm(expected)
