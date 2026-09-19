from __future__ import annotations

import pytest

from tests.unit.antares_assembly_child_runner import run_assembly_harness_pytest

_HARNESS_FILES = (
    "test_assembly_import.py",
    "test_assembly_success.py",
    "test_assembly_refuse_foreign.py",
    "test_assembly_refuse_conflict.py",
    "test_assembly_refuse_bind.py",
    "test_assembly_refuse_logger.py",
    "test_assembly_import_fail.py",
    "test_assembly_commands.py",
)


@pytest.mark.parametrize("filename", _HARNESS_FILES)
def test_antares_assembly_in_import_harness(filename: str) -> None:
    run_assembly_harness_pytest(filename)
