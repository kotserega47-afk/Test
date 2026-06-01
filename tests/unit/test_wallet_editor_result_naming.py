"""WalletEditor result file naming."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from automation.runtime import (
    build_wallet_editor_result_path,
    sanitize_input_name,
)


def test_sanitize_input_name_strips_xlsx_and_spaces() -> None:
    assert sanitize_input_name("My Batch.xlsx") == "My_Batch"
    assert sanitize_input_name("My Batch.XLSX") == "My_Batch"


def test_sanitize_input_name_rejects_path_traversal() -> None:
    assert sanitize_input_name("../../etc/passwd.xlsx") == "passwd"
    assert sanitize_input_name(r"..\secret\file.xlsx") == "file"
    assert ".." not in sanitize_input_name("../bad/name.xlsx")


def test_sanitize_input_name_filesystem_safe() -> None:
    assert sanitize_input_name("a/b\\c:file?.xlsx") == "c_file"
    assert sanitize_input_name("  ") == "input"


def test_sanitize_input_name_truncates_long_names() -> None:
    long_name = "a" * 200 + ".xlsx"
    assert len(sanitize_input_name(long_name)) <= 80


def test_build_result_path_format() -> None:
    with patch("automation.runtime.WALLET_EDITOR_RESULT_DIR", "/tmp/we_test_naming"):
        path = build_wallet_editor_result_path("My Batch.xlsx", "denis")
    assert path.endswith("wallet_editor_result_My_Batch_DENIS.xlsx")
    assert "OK" not in Path(path).name
    assert "FAIL" not in Path(path).name


def test_build_result_path_uses_normalized_operator() -> None:
    with patch("automation.runtime.WALLET_EDITOR_RESULT_DIR", "/tmp/we_test_naming_op"):
        path = build_wallet_editor_result_path("batch.xlsx", "ivan")
    assert path.endswith("wallet_editor_result_batch_IVAN.xlsx")


def test_build_result_path_avoids_collision_with_numeric_suffix(tmp_path: Path) -> None:
    base_dir = tmp_path / "results"
    first = build_wallet_editor_result_path("batch.xlsx", "DENIS", base_dir=str(base_dir))
    Path(first).write_text("existing", encoding="utf-8")

    second = build_wallet_editor_result_path("batch.xlsx", "DENIS", base_dir=str(base_dir))
    assert second.endswith("wallet_editor_result_batch_DENIS_2.xlsx")
    assert second != first


def test_build_result_path_uuid_suffix_after_many_collisions(tmp_path: Path) -> None:
    base_dir = tmp_path / "results"
    base_dir.mkdir(parents=True)
    stem = "wallet_editor_result_batch_DENIS"
    (base_dir / f"{stem}.xlsx").write_text("x", encoding="utf-8")
    for n in range(2, 100):
        (base_dir / f"{stem}_{n}.xlsx").write_text("x", encoding="utf-8")

    path = build_wallet_editor_result_path("batch.xlsx", "DENIS", base_dir=str(base_dir))
    name = Path(path).name
    assert name.startswith(f"{stem}_")
    assert name.endswith(".xlsx")
    assert name != f"{stem}.xlsx"
    assert not any(name == f"{stem}_{n}.xlsx" for n in range(2, 100))


def test_worker_passes_result_path_to_run_config() -> None:
    import time
    from unittest.mock import MagicMock, patch

    import automation.worker as worker_mod
    from automation.runtime import WalletEditorTask

    captured: dict[str, str] = {}

    def fake_run(file_path: str, cfg):  # noqa: ARG001
        captured["result_file_path"] = cfg.result_file_path
        stats = MagicMock()
        stats.summary.return_value = "ok"
        return cfg.result_file_path, stats

    with worker_mod._registry_lock:
        worker_mod._profile_workers.clear()
    with patch("automation.worker.run", side_effect=fake_run):
        with patch("automation.worker.send_text"):
            with patch("automation.worker.send_document"):
                worker_mod.add_task(
                    WalletEditorTask(
                        file_path="/tmp/in.xlsx",
                        chat_id=1,
                        telegram_user_id=2,
                        operator_profile="DENIS",
                        source_file_name="Partner List.xlsx",
                        login="l",
                        password="p",
                        auth_state_path="/tmp/auth.json",
                    )
                )
                deadline = time.time() + 2
                while worker_mod._profile_workers["DENIS"].queue.unfinished_tasks > 0:
                    if time.time() > deadline:
                        break
                    time.sleep(0.02)

    assert captured["result_file_path"].endswith(
        "wallet_editor_result_Partner_List_DENIS.xlsx"
    )
