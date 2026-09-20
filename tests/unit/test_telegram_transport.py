from __future__ import annotations

import pytest

from tests.unit.antares_send_child_runner import run_send_harness_pytest

_HARNESS_FILES = (
    "test_send_boundaries.py",
    "test_registry_lazy_send.py",
)


@pytest.mark.parametrize("filename", _HARNESS_FILES)
def test_send_and_registry_lazy_send_in_isolated_harness(filename: str) -> None:
    run_send_harness_pytest(filename)
