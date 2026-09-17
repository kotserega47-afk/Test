from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tests.unit.isolated_child_env import isolated_child_env

from core.project_profile import (
    InvalidProjectProfileError,
    ProjectProfileSelection,
    parse_project_profile,
)
import core.project_profile as project_profile_mod


def test_explicit_antares() -> None:
    result = parse_project_profile("antares")
    assert result == ProjectProfileSelection(name="antares", implicit_default=False)


def test_explicit_raccoon() -> None:
    result = parse_project_profile("raccoon")
    assert result == ProjectProfileSelection(name="raccoon", implicit_default=False)


def test_explicit_wr() -> None:
    result = parse_project_profile("wr")
    assert result == ProjectProfileSelection(name="wr", implicit_default=False)


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_none_empty_and_whitespace_are_implicit_antares(value: str | None) -> None:
    result = parse_project_profile(value)
    assert result == ProjectProfileSelection(name="antares", implicit_default=True)


def test_strip_around_exact_token() -> None:
    result = parse_project_profile("  raccoon  ")
    assert result == ProjectProfileSelection(name="raccoon", implicit_default=False)


@pytest.mark.parametrize("value", ["Antares", "ANTARES", "Raccoon", "WR", "unknown", "antaresX"])
def test_unknown_or_wrong_case_raises(value: str) -> None:
    with pytest.raises(InvalidProjectProfileError, match="Invalid project profile"):
        parse_project_profile(value)


def test_calls_are_independent() -> None:
    first = parse_project_profile("wr")
    second = parse_project_profile(None)
    third = parse_project_profile("antares")
    assert first == ProjectProfileSelection(name="wr", implicit_default=False)
    assert second == ProjectProfileSelection(name="antares", implicit_default=True)
    assert third == ProjectProfileSelection(name="antares", implicit_default=False)
    assert first is not second
    assert parse_project_profile("raccoon").name == "raccoon"


def test_selection_is_immutable() -> None:
    result = parse_project_profile("raccoon")
    with pytest.raises(FrozenInstanceError):
        result.name = "wr"  # type: ignore[misc]


def test_parser_source_has_no_env_jobs_or_exit() -> None:
    file_source = Path(project_profile_mod.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in file_source
    assert "os.getenv" not in file_source
    assert "SystemExit" not in file_source
    assert "scheduler" not in file_source
    assert "playwright" not in file_source.lower()
    assert "telegram" not in file_source.lower()
    assert "JOB_REGISTRY" not in file_source


_CLEAN_IMPORT = r"""
import os
for env_key in (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID_ANALIZ",
    "ANTARES_LOGIN",
    "PROJECT_PROFILE",
    "DATABASE_URL",
):
    os.environ.pop(env_key, None)
import modules
import modules.antares
import modules.raccoon
import modules.wr
from core.project_profile import parse_project_profile
assert parse_project_profile("antares").name == "antares"
assert modules.antares.__doc__
assert modules.raccoon.__doc__
assert modules.wr.__doc__
print("clean_import_ok")
"""


def test_import_parser_and_empty_packages_without_production_env() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated_child_env(Path(tmp), with_harness=False)
        proc = subprocess.run(
            [sys.executable, "-c", _CLEAN_IMPORT],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    assert proc.returncode == 0, proc.stderr
    assert "clean_import_ok" in proc.stdout
