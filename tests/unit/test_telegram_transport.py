from __future__ import annotations

from tests.unit.antares_send_child_runner import run_send_harness_pytest


def test_send_boundaries_in_isolated_harness() -> None:
    run_send_harness_pytest("test_send_boundaries.py")
