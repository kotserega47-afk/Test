"""Tests for observability.process_resource_health."""

from __future__ import annotations

from pathlib import Path

import observability.process_resource_health as prh


def test_parse_proc_stat_zombie_chrome() -> None:
    stat = "12345 (chrome-headless) Z 1 2 3 4 5 6 7 8 9"
    comm, state = prh._parse_proc_stat(stat)
    assert comm == "chrome-headless"
    assert state == "Z"


def test_collect_process_resource_snapshot_from_fake_proc(tmp_path: Path) -> None:
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "stat").write_text("1 (chrome-headless) Z 0 0", encoding="utf-8")
    (tmp_path / "2").mkdir()
    (tmp_path / "2" / "stat").write_text("2 (python) R 0 0", encoding="utf-8")

    snap = prh.collect_process_resource_snapshot(proc_root=tmp_path)

    assert snap["process_count"] == 2
    assert snap["zombie_count"] == 1
    assert snap["zombie_chrome_count"] == 1


def test_log_process_resource_health_critical(caplog) -> None:
    import unittest.mock as mock

    prh._reset_process_resource_health_for_tests()
    fake = {
        "process_count": 100,
        "thread_count": 50,
        "zombie_count": 25,
        "zombie_chrome_count": 25,
    }

    with mock.patch.object(prh, "collect_process_resource_snapshot", return_value=fake):
        with caplog.at_level("CRITICAL"):
            prh.log_process_resource_health_if_due(force=True)

    assert any("CRITICAL" in r.message for r in caplog.records)
    assert any("zombie_chrome_count=25" in r.message for r in caplog.records)
