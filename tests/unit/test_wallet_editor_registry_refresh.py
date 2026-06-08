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


def _seed_registry(
    store: dict[str, bytes],
    tmp_path: Path,
    *,
    rows: list[dict],
    hold: pd.DataFrame | None = None,
    otlezka: pd.DataFrame | None = None,
) -> None:
    all_results = pd.DataFrame(rows, columns=ALL_RESULTS_COLUMNS)
    hold_df = hold if hold is not None else pd.DataFrame(columns=HOLD_COLUMNS)
    otlezka_df = otlezka if otlezka is not None else _default_otlezka()
    local = tmp_path / "we_refresh_seed.xlsx"
    with pd.ExcelWriter(local, engine="openpyxl") as writer:
        all_results.to_excel(writer, sheet_name=SHEET_ALL_RESULTS, index=False)
        pd.DataFrame(columns=RUNS_COLUMNS).to_excel(writer, sheet_name=SHEET_RUNS, index=False)
        hold_df.to_excel(writer, sheet_name=SHEET_HOLD, index=False)
        otlezka_df.to_excel(writer, sheet_name=SHEET_OTLEZKA, index=False)
    store[DROPBOX_PATH] = local.read_bytes()


def _read_all_results(store: dict[str, bytes]) -> pd.DataFrame:
    with pd.ExcelFile(io.BytesIO(store[DROPBOX_PATH]), engine="openpyxl") as book:
        return pd.read_excel(book, SHEET_ALL_RESULTS)


@pytest.fixture
def refresh_env(monkeypatch, tmp_path):
    store: dict[str, bytes] = {}
    revs: dict[str, str] = {}
    uploads: list[str] = []

    def fake_download_with_rev(dropbox_path: str, local_path: str) -> tuple[str, str | None]:
        content = store.get(dropbox_path)
        if content is None:
            return "not_found", None
        Path(local_path).write_bytes(content)
        return "ok", revs.get(dropbox_path, "rev-initial")

    def fake_get_rev(dropbox_path: str) -> str | None:
        if dropbox_path not in store:
            return None
        return revs.get(dropbox_path, "rev-initial")

    def fake_upload_if_rev(
        local_path: str, dropbox_path: str, expected_rev: str | None
    ) -> str:
        from integrations import dropbox_watcher

        if expected_rev is not None:
            current = dropbox_watcher.get_dropbox_file_rev(dropbox_path)
            if current != expected_rev:
                return "rev_conflict"
        store[dropbox_path] = Path(local_path).read_bytes()
        revs[dropbox_path] = f"rev-after-{len(uploads)}"
        uploads.append(dropbox_path)
        return "uploaded"

    state_root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(state_root))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
    revs[DROPBOX_PATH] = "rev-initial"

    with patch(
        "integrations.wallet_editor_registry_refresh.download_file_with_rev",
        side_effect=fake_download_with_rev,
    ):
        with patch(
            "integrations.wallet_editor_registry_refresh.upload_file_if_rev",
            side_effect=fake_upload_if_rev,
        ):
            with patch(
                "integrations.dropbox_watcher.get_dropbox_file_rev",
                side_effect=fake_get_rev,
            ):
                with patch(
                    "integrations.wallet_editor_registry_refresh.resolve_route_chat_id",
                    return_value=MagicMock(chat_id=-9001, source="test"),
                ):
                    with patch(
                        "integrations.wallet_editor_registry_refresh.send_message_sync",
                        return_value=None,
                    ):
                        yield store, revs, uploads, tmp_path


def test_waiting_to_ready_transition(refresh_env):
    store, _, uploads, tmp_path = refresh_env
    _seed_registry(store, tmp_path, rows=[_row()])
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.success is True
    assert result.changed_rows == 1
    assert result.uploaded is True
    assert result.breakdown.waiting_to_ready == 1
    assert len(uploads) == 1
    df = _read_all_results(store)
    assert df.iloc[0]["Статус включения"] == STATUS_K_VKLUCHENIYU


def test_ready_to_overdue_transition(refresh_env):
    store, _, uploads, tmp_path = refresh_env
    _seed_registry(
        store,
        tmp_path,
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
    assert len(uploads) == 1
    df = _read_all_results(store)
    assert df.iloc[0]["Статус включения"] == STATUS_PROSROCHENO


def test_hold_row_unchanged(refresh_env):
    store, _, uploads, tmp_path = refresh_env
    hold = pd.DataFrame(
        [{"Дата добавления": "01.06.2026", "card": "4111111111111111", "partner": "Ostin", "comment": "hold"}]
    )
    _seed_registry(
        store,
        tmp_path,
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
    assert len(uploads) == 0


def test_ok_skip_fail_rows_unchanged(refresh_env):
    store, _, uploads, tmp_path = refresh_env
    _seed_registry(
        store,
        tmp_path,
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
    assert len(uploads) == 0


def test_no_changes_skips_upload(refresh_env):
    store, revs, uploads, tmp_path = refresh_env
    _seed_registry(
        store,
        tmp_path,
        rows=[
            _row(
                **{
                    "Дата включения": "07.06.2026",
                    "Статус включения": STATUS_K_VKLUCHENIYU,
                }
            )
        ],
    )
    rev_before = revs[DROPBOX_PATH]
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.skipped_no_changes is True
    assert result.uploaded is False
    assert len(uploads) == 0
    assert revs[DROPBOX_PATH] == rev_before


def test_changes_trigger_upload(refresh_env):
    store, _, uploads, tmp_path = refresh_env
    _seed_registry(store, tmp_path, rows=[_row()])
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.uploaded is True
    assert len(uploads) == 1


def test_rev_conflict_retry(refresh_env):
    store, _, uploads, tmp_path = refresh_env
    _seed_registry(store, tmp_path, rows=[_row()])
    attempts = {"count": 0}

    def flaky_upload(local_path, dropbox_path, expected_rev):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return "rev_conflict"
        store[dropbox_path] = Path(local_path).read_bytes()
        uploads.append(dropbox_path)
        return "uploaded"

    with patch(
        "integrations.wallet_editor_registry_refresh.upload_file_if_rev",
        side_effect=flaky_upload,
    ):
        result = refresh_wallet_editor_registry_lifecycle(
            today=date(2026, 6, 7),
            settings=FAST_SETTINGS,
        )

    assert result.success is True
    assert result.uploaded is True
    assert attempts["count"] == 2
    assert len(uploads) == 1


def test_refresh_failure_on_missing_registry(refresh_env):
    store, _, _, _ = refresh_env
    store.pop(DROPBOX_PATH, None)
    result = refresh_wallet_editor_registry_lifecycle(
        today=date(2026, 6, 7),
        settings=FAST_SETTINGS,
    )
    assert result.success is False
    assert "not found" in (result.error or "").lower()


def test_refresh_failure_on_upload_error(refresh_env):
    store, _, _, tmp_path = refresh_env
    _seed_registry(store, tmp_path, rows=[_row()])

    with patch(
        "integrations.wallet_editor_registry_refresh.upload_file_if_rev",
        return_value="error",
    ):
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
    store, _, uploads, tmp_path = refresh_env
    _seed_registry(store, tmp_path, rows=[_row()])

    with patch(
        "integrations.wallet_editor_registry_refresh.refresh_wallet_editor_registry_lifecycle",
        wraps=refresh_wallet_editor_registry_lifecycle,
    ) as refresh_fn:
        run_wallet_editor_registry_refresh_job()
        refresh_fn.assert_called_once()
        actor = refresh_fn.call_args.kwargs["actor"]
        assert actor.kind == "scheduler"

    assert len(uploads) == 1


def test_run_job_raises_on_failure(refresh_env):
    store, _, _, _ = refresh_env
    store.pop(DROPBOX_PATH, None)
    with pytest.raises(RuntimeError, match="not found"):
        run_wallet_editor_registry_refresh_job()
