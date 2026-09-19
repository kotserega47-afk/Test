"""Pytest options for TASK-04 explicit Platform compare. Not production."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--platform-checkout",
        action="store",
        default=None,
        help=(
            "Path to a clean Platform_2.0 checkout at "
            "ebbcd6c11b0c2418575f807edd40feb1c4fed468. Required to collect "
            "tests/compare_platform_raccoon_payin.py."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "platform_compare: explicit Platform checkout compare (needs --platform-checkout)",
    )


def pytest_ignore_collect(collection_path, config: pytest.Config) -> bool:  # noqa: ANN001
    if collection_path.name == "compare_platform_raccoon_payin.py":
        return not bool(config.getoption("--platform-checkout"))
    if "script_jobs_import_harness" in collection_path.parts:
        if collection_path.name.startswith("test_") and collection_path.suffix == ".py":
            return os.environ.get("SCRIPT_JOBS_IMPORT_HARNESS") != "1"
    if "antares_assembly_harness" in collection_path.parts:
        if collection_path.name.startswith("test_") and collection_path.suffix == ".py":
            return os.environ.get("ANTARES_ASSEMBLY_HARNESS") != "1"
    return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--platform-checkout"):
        return
    items[:] = [item for item in items if item.get_closest_marker("platform_compare") is None]
