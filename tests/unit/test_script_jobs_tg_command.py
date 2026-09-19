from __future__ import annotations

from tests.unit.script_jobs_child_runner import run_import_harness_pytest


def test_script_jobs_tg_command_in_import_harness():
    run_import_harness_pytest("test_script_jobs_tg_command.py")
