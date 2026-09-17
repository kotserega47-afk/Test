"""Assembled JOB_REGISTRY and get_handlers() via isolated subprocess.

Real registration modules run; downloaders/send/Playwright/DB are stubs.
This does not start jobs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.unit.isolated_child_env import isolated_child_env, missing_dependency_hint

ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = ROOT / "tests" / "fixtures" / "behavior_baseline"
_DUMP = ROOT / "tests" / "unit" / "registration_harness" / "dump_registration.py"
_RACCOON_KEYS = {"raccoon_wallet", "raccoon_hourly", "raccoon_daily_conversion"}


def _dump(extra: dict[str, str] | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        dump_path = sandbox / "registration.json"
        env = isolated_child_env(
            sandbox,
            with_registration_harness=True,
            extra={
                "REGISTRATION_DUMP_PATH": str(dump_path),
                "TELEGRAM_BOT_TOKEN": "123456:registration-dump-test",
                **(extra or {}),
            },
        )
        proc = subprocess.run(
            [sys.executable, str(_DUMP)],
            cwd=str(sandbox),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
            raise AssertionError(missing_dependency_hint(proc.stderr))
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "registration_dump_ok" in proc.stdout
        return json.loads(dump_path.read_text(encoding="utf-8"))


def test_assembled_job_registry_and_handlers_match_frozen_inventory() -> None:
    expected_keys = json.loads((_FIXTURE / "expected_job_registry_keys.json").read_text(encoding="utf-8"))
    expected_cmds = json.loads((_FIXTURE / "expected_tg_commands.json").read_text(encoding="utf-8"))
    payload = _dump()
    assert set(payload["keys"]) == set(expected_keys["keys"])
    assert payload["commands"] == expected_cmds["commands"]
    assert payload["document_handlers"] == 1
    assert payload["document_filter_is_all"] is True
    raccoon_file = payload["raccoon_jobs_file"] or ""
    assert raccoon_file.replace("\\", "/").endswith("integrations/raccoon_jobs.py")
    antares_file = payload["antares_jobs_file"] or ""
    assert antares_file.replace("\\", "/").endswith("modules/antares/jobs.py")
    assert payload["hourly_is_run_hourly_job"] is True
    assert payload["download_is_run_download_job"] is True
    assert payload["wallet_is_run_wallet_cycle"] is True
    assert payload["rate_is_run_rate_monitor_safe"] is True
    assert payload["refresh_is_lifecycle_job"] is True
    assert payload["replay_is_outbox_job"] is True
    assert payload["stub_executors_pairwise_distinct"] is True
    assert payload["run_wallet_callback_is_antares"] is True
    assert payload["run_hourly_callback_is_antares"] is True
    assert payload["run_download_callback_is_antares"] is True
    assert payload["run_rate_callback_is_antares"] is True
    assert payload["run_wallet_reexport_is_antares"] is True
    assert payload["run_hourly_reexport_is_antares"] is True
    assert payload["run_download_reexport_is_antares"] is True
    assert payload["run_rate_reexport_is_antares"] is True
    assert payload["operator_wallets_ready_callback_is_antares"] is True
    assert payload["wallet_editor_refresh_callback_is_antares"] is True
    assert payload["operator_wallets_ready_reexport_is_antares"] is True
    assert payload["wallet_editor_refresh_reexport_is_antares"] is True
    assert payload["registry_health_callback_is_antares"] is True
    assert payload["registry_replay_callback_is_antares"] is True
    assert payload["registry_health_reexport_is_antares"] is True
    assert payload["registry_replay_reexport_is_antares"] is True
    assert payload["run_raccoon_callback_is_tg"] is True
    assert payload["registry_export_callback_is_tg"] is True
    assert payload["antares_run_callbacks_pairwise_distinct"] is True


def test_assembled_registry_detects_missing_raccoon_jobs_import() -> None:
    payload = _dump({"REGISTRATION_OMIT_RACCOON_JOBS": "1"})
    keys = set(payload["keys"])
    assert _RACCOON_KEYS.isdisjoint(keys)
    expected_keys = json.loads((_FIXTURE / "expected_job_registry_keys.json").read_text(encoding="utf-8"))
    assert set(expected_keys["keys"]) - _RACCOON_KEYS <= keys
    assert payload["hourly_is_run_hourly_job"] is True
