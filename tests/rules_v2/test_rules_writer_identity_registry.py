"""PR-9: identity registry sync after successful ``rules_writer.update_sheet`` upload."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from core.rules_v2.identity_registry_io import (
    build_registry_from_manifest,
    identity_registry_dropbox_path,
)
from core.rules_writer import (
    _TMP_DIR,
    _try_sync_identity_registry_after_workbook_upload,
    update_sheet,
)

_SYNC_FN = "core.rules_writer._try_sync_identity_registry_after_workbook_upload"
_BUILD_FN = "core.rules_v2.identity_registry_io.build_registry_from_manifest"
_UPLOAD_FN = "core.rules_writer.upload_file"
_DOWNLOAD_FN = "core.rules_writer.download_file"
_VALIDATE_FN = "core.rules_writer._validate_rules_file"
_RULES_DB = "/TestRules/rules.xlsx"


@pytest.fixture
def writer_rules_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("RULES_XLSX_PATH", _RULES_DB)
    monkeypatch.setenv("RULES_IDENTITY_REGISTRY_PATH", str(tmp_path / "rules_identity_registry.v1.json"))
    local_wb = _TMP_DIR / "rules.xlsx"
    local_wb.parent.mkdir(parents=True, exist_ok=True)
    frames = {"meta": pd.DataFrame([{"key": "version", "value": "9"}])}
    with pd.ExcelWriter(local_wb, engine="openpyxl") as writer:
        frames["meta"].to_excel(writer, sheet_name="meta", index=False)
    return local_wb.resolve()


def _run_update_sheet(local_wb: Path) -> list[tuple[str, str]]:
    upload_calls: list[tuple[str, str]] = []

    def _upload(local_path: str, dropbox_path: str) -> bool:
        upload_calls.append((local_path, dropbox_path))
        return True

    with (
        patch(_DOWNLOAD_FN, return_value="ok"),
        patch(
            "core.rules_writer.pd.read_excel",
            return_value=pd.DataFrame([{"key": "version", "value": "9"}]),
        ),
        patch(_VALIDATE_FN, return_value="9"),
        patch(_UPLOAD_FN, side_effect=_upload),
        patch("core.rules_writer.clear_rules_caches"),
        patch("core.rules_writer.state_update_meta"),
        patch("core.rules_writer.append_event"),
    ):
        update_sheet("meta", {"user": "test"}, lambda df: df)
    return upload_calls


def test_workbook_upload_success_triggers_registry_upload(writer_rules_env: Path) -> None:
    with patch(_SYNC_FN, wraps=_try_sync_identity_registry_after_workbook_upload) as sync_m:
        upload_calls = _run_update_sheet(writer_rules_env)
    sync_m.assert_called_once()
    assert sync_m.call_args.kwargs["meta_version"] == "9"
    assert len(upload_calls) == 2
    assert upload_calls[1][1] == identity_registry_dropbox_path()


def test_workbook_upload_failure_skips_registry_sync(writer_rules_env: Path) -> None:
    def _upload(local_path: str, dropbox_path: str) -> bool:
        return False

    with (
        patch(_DOWNLOAD_FN, return_value="ok"),
        patch(
            "core.rules_writer.pd.read_excel",
            return_value=pd.DataFrame([{"key": "version", "value": "9"}]),
        ),
        patch(_VALIDATE_FN, return_value="9"),
        patch(_UPLOAD_FN, side_effect=_upload),
        patch(_SYNC_FN) as sync_m,
    ):
        with pytest.raises(RuntimeError, match="Failed to upload rules.xlsx"):
            update_sheet("meta", {"user": "test"}, lambda df: df)
    sync_m.assert_not_called()


def test_registry_upload_failure_does_not_raise_or_rollback(writer_rules_env: Path) -> None:
    calls: list[tuple[str, str]] = []

    def _upload(local_path: str, dropbox_path: str) -> bool:
        calls.append((local_path, dropbox_path))
        if dropbox_path == identity_registry_dropbox_path():
            return False
        return True

    with (
        patch(_DOWNLOAD_FN, return_value="ok"),
        patch(
            "core.rules_writer.pd.read_excel",
            return_value=pd.DataFrame([{"key": "version", "value": "9"}]),
        ),
        patch(_VALIDATE_FN, return_value="9"),
        patch(_UPLOAD_FN, side_effect=_upload),
        patch("core.rules_writer.clear_rules_caches") as clear_m,
        patch("core.rules_writer.state_update_meta") as meta_m,
        patch("core.rules_writer.append_event") as event_m,
    ):
        update_sheet("meta", {"user": "test"}, lambda df: df)

    assert len(calls) == 2
    clear_m.assert_called_once()
    meta_m.assert_called_once()
    event_m.assert_called_once()


def test_registry_built_from_same_local_workbook_path(
    writer_rules_env: Path,
    tmp_path: Path,
) -> None:
    from tests.rules_v2.test_identity_manifest import _minimal_frames, _write_workbook

    wb = _write_workbook(tmp_path, _minimal_frames(), "writer_rules.xlsx")
    with patch(_BUILD_FN, wraps=build_registry_from_manifest) as build_m:
        _try_sync_identity_registry_after_workbook_upload(wb, meta_version="3")
    build_m.assert_called_once()
    assert Path(build_m.call_args.kwargs["workbook_path"]).resolve() == wb.resolve()


def test_registry_upload_uses_identity_registry_dropbox_path(
    writer_rules_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_XLSX_PATH", "/Fleet/rules.xlsx")
    expected = identity_registry_dropbox_path()
    assert expected == "/Fleet/state/rules_identity_registry.v1.json"

    upload_calls: list[tuple[str, str]] = []

    def _upload(local_path: str, dropbox_path: str) -> bool:
        upload_calls.append((local_path, dropbox_path))
        return True

    with patch(_UPLOAD_FN, side_effect=_upload):
        _try_sync_identity_registry_after_workbook_upload(writer_rules_env, meta_version="9")

    assert upload_calls[0][1] == expected


def test_update_sheet_workbook_upload_only_when_identity_sync_mocked(writer_rules_env: Path) -> None:
    """Pre-PR-9 observable: one Dropbox workbook upload; post-steps still run."""

    upload_calls: list[tuple[str, str]] = []

    def _upload(local_path: str, dropbox_path: str) -> bool:
        upload_calls.append((local_path, dropbox_path))
        return True

    with (
        patch(_SYNC_FN) as sync_m,
        patch(_DOWNLOAD_FN, return_value="ok"),
        patch(
            "core.rules_writer.pd.read_excel",
            return_value=pd.DataFrame([{"key": "version", "value": "9"}]),
        ),
        patch(_VALIDATE_FN, return_value="9"),
        patch(_UPLOAD_FN, side_effect=_upload),
        patch("core.rules_writer.clear_rules_caches") as clear_m,
        patch("core.rules_writer.state_update_meta") as meta_m,
        patch("core.rules_writer.append_event") as event_m,
    ):
        update_sheet("meta", {"user": "test"}, lambda df: df)

    sync_m.assert_called_once()
    assert sync_m.call_args.kwargs["meta_version"] == "9"
    assert Path(sync_m.call_args.args[0]).resolve() == writer_rules_env.resolve()
    assert len(upload_calls) == 1
    assert upload_calls[0][1] == _RULES_DB
    clear_m.assert_called_once()
    meta_m.assert_called_once()
    event_m.assert_called_once()
