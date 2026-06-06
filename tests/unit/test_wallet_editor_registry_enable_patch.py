from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from integrations.wallet_editor_registry import (
    EnableRegistryUpdate,
    apply_enable_updates_to_all_results,
    patch_enable_results_in_dropbox_registry,
)
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_REMOVE_PARTNER,
    ALL_RESULTS_COLUMNS,
    STATUS_K_VKLUCHENIYU,
    STATUS_OSHIBKA,
    STATUS_PROPUSHENO,
    STATUS_VKLUCHENO,
    recalculate_all_results,
)
from integrations.wallet_editor_registry_settings import RegistrySettings
from integrations.wallet_editor_registry_xlsx import save_registry_workbook

MSK = ZoneInfo("Europe/Moscow")
TODAY = date(2026, 6, 6)
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"


def _row(
    *,
    card: str = "4111",
    partner: str = "Ostin",
    disable_at: str = "01.06.2026 10:00:00",
    vklyucheno: str = "",
    comment: str = "",
) -> dict[str, str]:
    return {
        "Дата отключения": disable_at,
        "Дата включения": "06.06.2026",
        "Статус включения": STATUS_K_VKLUCHENIYU,
        "Включено": vklyucheno,
        "Комментарий включения": comment,
        "card": card,
        "partner": partner,
        "action": ACTION_REMOVE_PARTNER,
        "status": "OK",
        "comment": "",
        "hold": "",
    }


def _df(*rows: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=ALL_RESULTS_COLUMNS)


def _otlezka_df() -> pd.DataFrame:
    return pd.DataFrame([{"partner": "Ostin", "Полные дни": 5, "comment": ""}])


def _recalc(df: pd.DataFrame) -> pd.DataFrame:
    recalculated, _ = recalculate_all_results(
        df,
        pd.DataFrame(),
        _otlezka_df(),
        today=TODAY,
    )
    return recalculated


def _update(**kwargs) -> EnableRegistryUpdate:
    base = dict(
        card="4111",
        partner="Ostin",
        disable_date="01.06.2026 10:00:00",
        vklyucheno="OK",
        comment="Партнёр добавлен",
        source_row_index=0,
    )
    base.update(kwargs)
    return EnableRegistryUpdate(**base)


@pytest.mark.parametrize(
    "vklyucheno,comment,expected_status",
    [
        ("OK", "Партнёр добавлен", STATUS_VKLUCHENO),
        ("SKIP", "SERVICE_WORKS: test", STATUS_PROPUSHENO),
        ("FAIL", "TECHNICAL: timeout", STATUS_OSHIBKA),
    ],
)
def test_apply_enable_patch_sets_lifecycle_status(vklyucheno, comment, expected_status):
    df = _df(_row())
    patched, count = apply_enable_updates_to_all_results(
        df,
        [_update(vklyucheno=vklyucheno, comment=comment)],
    )
    assert count == 1
    assert patched.at[0, "Включено"] == vklyucheno
    assert patched.at[0, "Комментарий включения"] == comment

    recalculated = _recalc(patched)
    assert recalculated.at[0, "Статус включения"] == expected_status


def test_patch_only_matching_row_by_card_partner_disable_date():
    df = _df(
        _row(card="4111", disable_at="01.06.2026 10:00:00"),
        _row(card="4111", disable_at="05.06.2026 11:00:00"),
        _row(card="4222", partner="Other", disable_at="01.06.2026 10:00:00"),
    )
    patched, count = apply_enable_updates_to_all_results(
        df,
        [_update(disable_date="05.06.2026 11:00:00", source_row_index=1, comment="newer")],
    )
    assert count == 1
    assert patched.at[0, "Включено"] == ""
    assert patched.at[1, "Включено"] == "OK"
    assert patched.at[1, "Комментарий включения"] == "newer"
    assert patched.at[2, "Включено"] == ""


def test_duplicate_older_row_not_patched():
    df = _df(
        _row(disable_at="01.06.2026 10:00:00"),
        _row(disable_at="05.06.2026 11:00:00"),
    )
    patched, count = apply_enable_updates_to_all_results(
        df,
        [_update(disable_date="05.06.2026 11:00:00", source_row_index=1)],
    )
    assert count == 1
    assert patched.at[0, "Включено"] == ""
    assert patched.at[1, "Включено"] == "OK"


def test_unrelated_rows_unchanged():
    df = _df(
        _row(card="4111"),
        _row(card="4222", partner="Other", disable_at="02.06.2026 09:00:00"),
    )
    patched, count = apply_enable_updates_to_all_results(df, [_update()])
    assert count == 1
    assert patched.at[1, "Включено"] == ""
    assert patched.at[1, "Комментарий включения"] == ""


@pytest.fixture
def registry_env(monkeypatch, tmp_path):
    store: dict[str, bytes] = {}
    revs: dict[str, str] = {}

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
        revs[dropbox_path] = f"rev-after-{len(store)}"
        return "uploaded"

    state_root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(state_root))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)

    workbook = tmp_path / "seed.xlsx"
    seed_df = _recalc(_df(_row()))
    save_registry_workbook(
        workbook,
        all_results=seed_df,
        runs=pd.DataFrame(),
        hold_exists=False,
        otlezka_exists=True,
        is_new_file=True,
    )
    store[DROPBOX_PATH] = workbook.read_bytes()
    revs[DROPBOX_PATH] = "rev-initial"

    with patch(
        "integrations.wallet_editor_registry.download_file_with_rev",
        side_effect=fake_download_with_rev,
    ):
        with patch(
            "integrations.wallet_editor_registry.upload_file_if_rev",
            side_effect=fake_upload_if_rev,
        ):
            with patch(
                "integrations.dropbox_watcher.get_dropbox_file_rev",
                side_effect=fake_get_rev,
            ):
                with patch(
                    "integrations.wallet_editor_registry_lifecycle.now_msk",
                    return_value=datetime(2026, 6, 6, 12, 0, 0, tzinfo=MSK),
                ):
                    yield store, revs


def _read_all_results(store: dict[str, bytes]) -> pd.DataFrame:
    with pd.ExcelFile(io.BytesIO(store[DROPBOX_PATH]), engine="openpyxl") as book:
        return pd.read_excel(book, "all_results")


def test_patch_enable_results_in_dropbox_registry_success(registry_env):
    store, _revs = registry_env
    result = patch_enable_results_in_dropbox_registry(
        [_update(vklyucheno="SKIP", comment="UNKNOWN_STATUS: test")],
        settings=RegistrySettings(60, 180, 1),
    )
    assert result.success is True
    assert result.patched_count == 1

    all_results = _read_all_results(store)
    assert all_results.at[0, "Включено"] == "SKIP"
    assert all_results.at[0, "Комментарий включения"] == "UNKNOWN_STATUS: test"
    assert all_results.at[0, "Статус включения"] == STATUS_PROPUSHENO


def test_patch_enable_results_rev_conflict_then_success(registry_env):
    store, _revs = registry_env
    attempts = {"count": 0}

    def flaky_upload(local_path, dropbox_path, expected_rev):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return "rev_conflict"
        store[dropbox_path] = Path(local_path).read_bytes()
        return "uploaded"

    with patch(
        "integrations.wallet_editor_registry.upload_file_if_rev",
        side_effect=flaky_upload,
    ):
        result = patch_enable_results_in_dropbox_registry(
            [_update()],
            settings=RegistrySettings(60, 180, 0),
        )

    assert result.success is True
    assert result.patched_count == 1
    assert attempts["count"] == 2


def test_patch_enable_results_upload_failure(registry_env):
    store, _revs = registry_env

    with patch(
        "integrations.wallet_editor_registry.upload_file_if_rev",
        return_value="error",
    ):
        result = patch_enable_results_in_dropbox_registry(
            [_update()],
            settings=RegistrySettings(60, 5, 1),
        )

    assert result.success is False
    assert result.patched_count == 0
    assert result.error_reason is not None
