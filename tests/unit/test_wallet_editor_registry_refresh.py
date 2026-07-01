"""Wallet Editor registry lifecycle refresh — daily/manual Dropbox sync."""
from __future__ import annotations

import asyncio
import io
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.job_runner import JOB_REGISTRY
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    HOLD_COLUMNS,
    HOLD_MARK,
    OTLEZKA_COLUMNS,
    RUNS_COLUMNS,
    SHEET_ALL_RESULTS,
    SHEET_HOLD,
    SHEET_OTLEZKA,
    SHEET_RUNS,
    STATUS_HOLD,
    STATUS_K_VKLUCHENIYU,
    STATUS_OSHIBKA,
    STATUS_OZHIDAET,
    STATUS_PROPUSHENO,
    STATUS_PROSROCHENO,
    STATUS_VKLUCHENO,
)
from integrations.wallet_editor_registry_refresh import (
    RefreshBreakdown,
    build_refresh_report,
    compute_lifecycle_diff,
    refresh_wallet_editor_registry_lifecycle,
    run_wallet_editor_registry_refresh_job,
)
from integrations.wallet_editor_registry_settings import RegistrySettings

MSK = __import__("zoneinfo").ZoneInfo("Europe/Moscow")
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"
FAST_SETTINGS = RegistrySettings(30, 60, 0)


def _row(**overrides) -> dict:
    base = {
        "Дата операции": "02.06.2026",
        "Дата отключения": "02.06.2026 10:00:00",
        "Дата включения": "07.06.2026",
        "Статус включения": STATUS_OZHIDAET,
        "Включено": "",
        "Комментарий включения": "",
        "card": "4111111111111111",
        "partner": "Ostin",
        "action": "remove_partner",
        "status": "OK",
        "comment": "",
        "hold": "",
    }
    base.update(overrides)
    return base


def _default_otlezka() -> pd.DataFrame:
    return pd.DataFrame([{"partner": "Ostin", "Полные дни": 5, "comment": ""}])


def _seed_registry_state(
    registry_state: dict,
    *,
    rows: list[dict],
    hold: pd.DataFrame | None = None,
    otlezka: pd.DataFrame | None = None,
) -> None:
    registry_state["all_results"] = pd.DataFrame(rows, columns=ALL_RESULTS_COLUMNS)
    if hold is not None:
        registry_state["hold"] = hold
    if otlezka is not None:
        registry_state["otlezka"] = otlezka


@pytest.fixture
def refresh_env(monkeypatch, tmp_path):
    from integrations.wallet_editor_registry_db.config import ENV_REGISTRY_SOURCE
    from integrations.wallet_editor_registry_refresh import RefreshBreakdown, _RefreshOutcome

    registry_state = {
        "all_results": pd.DataFrame(columns=ALL_RESULTS_COLUMNS),
        "runs": pd.DataFrame(columns=RUNS_COLUMNS),
        "hold": pd.DataFrame(columns=HOLD_COLUMNS),
        "otlezka": _default_otlezka(),
        "commits": 0,
    }

    def fake_refresh_attempt_postgres(
        *,
        dropbox_path: str,
        today,
        normalize_all_results,
        recalculate_all_results,
        compute_lifecycle_diff,
        lifecycle_row_changed,
    ):
        before_df = normalize_all_results(registry_state["all_results"])
        recalculated, _missing = recalculate_all_results(
            before_df,
            registry_state["hold"],
            registry_state["otlezka"],
            today=today,
        )
        changed_rows, breakdown = compute_lifecycle_diff(before_df, recalculated)
        if changed_rows == 0:
            return _RefreshOutcome.SKIPPED, 0, breakdown, None, None
        registry_state["all_results"] = recalculated
        registry_state["commits"] += 1
        return _RefreshOutcome.SUCCESS, changed_rows, breakdown, None, None

    state_root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(state_root))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
    monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "postgres")

    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.postgres_source.refresh_attempt_postgres",
        fake_refresh_attempt_postgres,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.manual_sync.run_manual_sync_prerun_gate",
        lambda **kwargs: MagicMock(ok=True),
    )

    with (
        patch(
            "integrations.wallet_editor_registry_refresh.resolve_route_chat_id",
            return_value=MagicMock(chat_id=-9001, source="test"),
        ),
        patch(
            "integrations.wallet_editor_registry_refresh.send_message_sync",
            return_value=None,
        ),
    ):
        yield registry_state, tmp_path


def test_waiting_to_ready_transition(refresh_env):
    registry_state, _tmp_path = refresh_env
    _seed_registry_state(registry_state, rows=[_row()])
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.success is True
    assert result.changed_rows == 1
    assert result.uploaded is True
    assert result.breakdown.waiting_to_ready == 1
    assert registry_state["commits"] == 1
    assert registry_state["all_results"].iloc[0]["Статус включения"] == STATUS_K_VKLUCHENIYU


def test_ready_to_overdue_transition(refresh_env):
    registry_state, _tmp_path = refresh_env
    _seed_registry_state(
        registry_state,
        rows=[
            _row(
                **{
                    "Дата отключения": "01.06.2026 10:00:00",
                    "Дата включения": "06.06.2026",
                    "Статус включения": STATUS_K_VKLUCHENIYU,
                }
            )
        ],
    )
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.success is True
    assert result.changed_rows == 1
    assert result.breakdown.ready_to_overdue == 1
    assert registry_state["commits"] == 1
    assert registry_state["all_results"].iloc[0]["Статус включения"] == STATUS_PROSROCHENO


def test_hold_row_unchanged(refresh_env):
    registry_state, _tmp_path = refresh_env
    hold = pd.DataFrame(
        [{"Дата добавления": "01.06.2026", "card": "4111111111111111", "partner": "Ostin", "comment": "hold"}]
    )
    _seed_registry_state(
        registry_state,
        rows=[
            _row(
                **{
                    "hold": HOLD_MARK,
                    "Дата включения": "",
                    "Статус включения": STATUS_HOLD,
                    "Включено": "",
                }
            )
        ],
        hold=hold,
    )
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.skipped_no_changes is True
    assert result.uploaded is False
    assert registry_state["commits"] == 0


def test_ok_skip_fail_rows_unchanged(refresh_env):
    registry_state, _tmp_path = refresh_env
    _seed_registry_state(
        registry_state,
        rows=[
            _row(
                card="4111111111111112",
                partner="Included",
                **{
                    "Дата отключения": "02.06.2026 10:00:00",
                    "Дата включения": "07.06.2026",
                    "Статус включения": STATUS_VKLUCHENO,
                    "Включено": "OK",
                    "Комментарий включения": "done",
                },
            ),
            _row(
                card="4111111111111113",
                partner="Skipped",
                status="SKIP",
                **{"Дата включения": "", "Статус включения": STATUS_PROPUSHENO, "Включено": "SKIP"},
            ),
            _row(
                card="4111111111111114",
                partner="Failed",
                status="FAIL",
                **{"Дата включения": "", "Статус включения": STATUS_OSHIBKA, "Включено": "FAIL"},
            ),
        ],
        otlezka=pd.DataFrame(
            [
                {"partner": "Included", "Полные дни": 5, "comment": ""},
                {"partner": "Skipped", "Полные дни": 5, "comment": ""},
                {"partner": "Failed", "Полные дни": 5, "comment": ""},
            ]
        ),
    )
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.skipped_no_changes is True
    assert registry_state["commits"] == 0


def test_no_changes_skips_upload(refresh_env):
    registry_state, _tmp_path = refresh_env
    _seed_registry_state(
        registry_state,
        rows=[
            _row(
                **{
                    "Дата включения": "07.06.2026",
                    "Статус включения": STATUS_K_VKLUCHENIYU,
                }
            )
        ],
    )
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.skipped_no_changes is True
    assert result.uploaded is False
    assert registry_state["commits"] == 0


def test_changes_trigger_upload(refresh_env):
    registry_state, _tmp_path = refresh_env
    _seed_registry_state(registry_state, rows=[_row()])
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.uploaded is True
    assert registry_state["commits"] == 1


def test_refresh_failure_on_manual_sync_gate(refresh_env, monkeypatch):
    registry_state, _tmp_path = refresh_env
    _seed_registry_state(registry_state, rows=[_row()])
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.manual_sync.run_manual_sync_prerun_gate",
        lambda **kwargs: MagicMock(ok=False, operator_message="manual sync required"),
    )
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.success is False
    assert "manual sync" in (result.error or "").lower()


def test_refresh_failure_on_postgres_load_error(refresh_env, monkeypatch):
    registry_state, _tmp_path = refresh_env
    _seed_registry_state(registry_state, rows=[_row()])

    def _fail_refresh(**kwargs):
        from integrations.wallet_editor_registry_refresh import RefreshBreakdown, _RefreshOutcome

        return _RefreshOutcome.TRANSIENT, 0, RefreshBreakdown(), "postgres load failed", None

    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.postgres_source.refresh_attempt_postgres",
        _fail_refresh,
    )
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=RegistrySettings(30, 60, 0),
    )

    assert result.success is False
    assert result.uploaded is False
    assert "timeout" in (result.error or "").lower() or "upload" in (result.error or "").lower()


def test_build_refresh_report_formats():
    success = build_refresh_report(
        today=date(2026, 6, 7),
        changed_rows=2,
        breakdown=RefreshBreakdown(waiting_to_ready=1, ready_to_overdue=1),
        uploaded=True,
        skipped_no_changes=False,
        duration_sec=1.25,
        success=True,
    )
    assert "date: 2026-06-07" in success
    assert "ОЖИДАЕТ → К ВКЛЮЧЕНИЮ: 1" in success
    assert "upload: OK" in success
    assert "duration: 1.2s" in success

    skipped = build_refresh_report(
        today=date(2026, 6, 7),
        changed_rows=0,
        breakdown=RefreshBreakdown(),
        uploaded=False,
        skipped_no_changes=True,
        duration_sec=0.5,
        success=True,
    )
    assert "date:" not in skipped
    assert "changed rows: 0" in skipped
    assert "upload: skipped (no changes)" in skipped

    failed = build_refresh_report(
        today=date(2026, 6, 7),
        changed_rows=0,
        breakdown=RefreshBreakdown(),
        uploaded=False,
        skipped_no_changes=False,
        duration_sec=2.0,
        success=False,
        error="registry download failed",
    )
    assert "upload: failed" in failed
    assert "error: registry download failed" in failed
    assert "date:" not in failed


def test_compute_lifecycle_diff_breakdown():
    before = pd.DataFrame([_row()], columns=ALL_RESULTS_COLUMNS)
    after = before.copy()
    after.at[0, "Статус включения"] = STATUS_K_VKLUCHENIYU
    changed, breakdown = compute_lifecycle_diff(before, after)
    assert changed == 1
    assert breakdown.waiting_to_ready == 1


def test_cmd_wallet_editor_refresh_acl_deny():
    from integrations.tg_commands import cmd_wallet_editor_refresh

    update = MagicMock()
    update.effective_chat.id = -100
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    with patch(
        "integrations.tg_commands._guard_or_deny",
        new=AsyncMock(return_value=False),
    ) as guard:
        with patch("integrations.tg_commands.dispatch_job_async") as dispatch_fn:
            asyncio.run(cmd_wallet_editor_refresh(update, MagicMock()))
            guard.assert_awaited_once_with(update, "wallet_editor_refresh")
            dispatch_fn.assert_not_called()


def test_cmd_wallet_editor_refresh_acl_allow():
    from integrations.tg_commands import cmd_wallet_editor_refresh

    update = MagicMock()
    update.effective_chat.id = -100
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    with patch(
        "integrations.tg_commands._guard_or_deny",
        new=AsyncMock(return_value=True),
    ):
        with patch(
            "integrations.tg_commands.dispatch_job_async",
            new=AsyncMock(return_value="job-42"),
        ) as dispatch_fn:
            asyncio.run(cmd_wallet_editor_refresh(update, MagicMock()))
            dispatch_fn.assert_awaited_once()
            assert dispatch_fn.call_args.args[0] == "wallet_editor_registry_refresh"


def test_job_registry_registration():
    from integrations import tg_commands  # noqa: F401 — registers jobs

    assert "wallet_editor_registry_refresh" in JOB_REGISTRY
    assert callable(JOB_REGISTRY["wallet_editor_registry_refresh"])


def test_scheduler_invokes_refresh_job(refresh_env):
    registry_state, _tmp_path = refresh_env
    _seed_registry_state(registry_state, rows=[_row()])

    with patch(
        "integrations.wallet_editor_registry_refresh.refresh_wallet_editor_registry_lifecycle",
        wraps=refresh_wallet_editor_registry_lifecycle,
    ) as refresh_fn:
        run_wallet_editor_registry_refresh_job()
        refresh_fn.assert_called_once()
        actor = refresh_fn.call_args.kwargs["actor"]
        assert actor.kind == "scheduler"

    assert registry_state["commits"] == 1


def test_run_job_raises_on_failure(refresh_env, monkeypatch):
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.manual_sync.run_manual_sync_prerun_gate",
        lambda **kwargs: MagicMock(ok=False, operator_message="manual sync required"),
    )
    with pytest.raises(RuntimeError, match="manual sync"):
        run_wallet_editor_registry_refresh_job()
