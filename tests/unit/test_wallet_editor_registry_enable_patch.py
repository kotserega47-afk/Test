from __future__ import annotations

import io
from contextlib import contextmanager
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
    SHEET_ALL_RESULTS,
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
    """Postgres patch path with in-memory registry store."""
    from integrations.wallet_editor_registry_db.config import ENV_REGISTRY_SOURCE
    from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore
    from integrations.wallet_editor_registry_db.mapping import map_all_results_row

    state_root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(state_root))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
    monkeypatch.setenv(ENV_REGISTRY_SOURCE, "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "postgres")

    pg_store = InMemoryRegistryStore()
    seed_df = _recalc(_df(_row()))
    for index in seed_df.index:
        pg_store.upsert_result(
            map_all_results_row(seed_df.loc[index], source_row_index=int(index))
        )

    def _load_frames_from_store():
        from integrations.wallet_editor_registry_db.frames import result_row_to_dict

        if not pg_store.results:
            return pd.DataFrame(columns=ALL_RESULTS_COLUMNS), pd.DataFrame(
                columns=[
                    "started_at",
                    "finished_at",
                    "input_rows",
                    "success_rows",
                    "failed_rows",
                    "skipped_rows",
                    "output_file",
                ]
            )
        rows = [result_row_to_dict(row) for row in pg_store.results.values()]
        return pd.DataFrame(rows, columns=ALL_RESULTS_COLUMNS), pd.DataFrame(
            columns=[
                "started_at",
                "finished_at",
                "input_rows",
                "success_rows",
                "failed_rows",
                "skipped_rows",
                "output_file",
            ]
        )

    def _upsert_results_batch(rows):
        for row in rows:
            pg_store.upsert_result(row)
        return len(rows)

    pg_store.upsert_results_batch = _upsert_results_batch  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.postgres_source.load_registry_frames_from_postgres",
        _load_frames_from_store,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.postgres_source.load_hold_otlezka_for_runtime",
        lambda _path: (_otlezka_df().iloc[0:0], _otlezka_df(), False, True),
    )

    class _FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def commit(self):
            return None

    @contextmanager
    def fake_connect(**kwargs):
        yield _FakeConn()

    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.postgres_source.connect",
        fake_connect,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.postgres_source.PostgresRegistryStore",
        lambda cur: pg_store,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_lifecycle.now_msk",
        lambda: datetime(2026, 6, 6, 12, 0, 0, tzinfo=MSK),
    )

    yield pg_store


def test_patch_enable_results_postgres_success(registry_env):
    pg_store = registry_env
    result = patch_enable_results_in_dropbox_registry(
        [_update(vklyucheno="SKIP", comment="UNKNOWN_STATUS: test")],
        settings=RegistrySettings(60, 180, 1),
    )
    assert result.success is True
    assert result.patched_count == 1

    patched_row = next(iter(pg_store.results.values()))
    assert patched_row.vklyucheno == "SKIP"
    assert patched_row.enable_comment == "UNKNOWN_STATUS: test"
