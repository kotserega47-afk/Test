from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.unit.antares_boot_child_runner import run_antares_boot

_SUCCESS_KEYS = (
    "download",
    "hourly",
    "rate",
    "script_job:operator_wallets_ready",
    "wallet",
    "wallet_editor_registry_refresh",
    "wallet_editor_registry_replay",
)


def _run(**kwargs):
    with tempfile.TemporaryDirectory() as tmp:
        return run_antares_boot(Path(tmp), **kwargs)


def _assert_no_forbidden(result) -> None:
    assert result.import_attempts == [], result.import_attempts


def test_boot_rejects_unset_profile() -> None:
    result = _run(dotenv_lines={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "sandbox-token"})
    assert result.returncode == 2, result.stderr + result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_boot_rejects_blank_profile(value: str) -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": value},
        dotenv_lines={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "sandbox-token"},
    )
    assert result.returncode == 2, result.stderr + result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)


@pytest.mark.parametrize("value", ["raccoon", "wr", "not-a-profile", "Antares"])
def test_boot_rejects_foreign_or_unknown_profile(value: str) -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": value, "TELEGRAM_BOT_TOKEN": "sandbox-token"},
    )
    assert result.returncode == 2, result.stderr + result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)


def test_boot_profile_only_in_dotenv_exits_2() -> None:
    result = _run(
        dotenv_lines={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "sandbox-token"},
    )
    assert result.returncode == 2, result.stderr + result.stdout
    assert "PROJECT_PROFILE" in result.stderr
    _assert_no_forbidden(result)


def test_boot_accepts_parser_normalized_antares() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "  antares  ", "TELEGRAM_BOT_TOKEN": "sandbox-token"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "antares boot ok" in result.stdout
    assert "commands=17" in result.stdout
    assert "document=1" in result.stdout
    assert "jobs=7" in result.stdout
    for key in _SUCCESS_KEYS:
        assert key in result.stdout
    _assert_no_forbidden(result)


@pytest.mark.parametrize("token", ["", "   "])
def test_boot_rejects_empty_token_after_dotenv(token: str) -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": token},
        dotenv_lines={"TELEGRAM_BOT_TOKEN": "from-file-should-not-override"},
    )
    assert result.returncode == 1, result.stderr + result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)


def test_boot_dotenv_supplies_token_when_process_has_none() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares"},
        dotenv_lines={"TELEGRAM_BOT_TOKEN": "sandbox-from-file"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "antares boot ok" in result.stdout
    assert "sandbox-from-file" not in result.stdout
    assert "sandbox-from-file" not in result.stderr
    _assert_no_forbidden(result)


def test_boot_process_env_wins_over_dotenv_token() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "   "},
        dotenv_lines={"TELEGRAM_BOT_TOKEN": "sandbox-from-file"},
    )
    assert result.returncode != 0, result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)


def test_boot_success_seven_jobs_and_handlers() -> None:
    result = _run(process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "sandbox-token"})
    assert result.returncode == 0, result.stderr + result.stdout
    assert "antares boot ok commands=17 document=1 jobs=7" in result.stdout
    for key in _SUCCESS_KEYS:
        assert key in result.stdout
    _assert_no_forbidden(result)


def test_boot_refuses_polluted_registry() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "sandbox-token"},
        pollute_registry=True,
    )
    assert result.returncode != 0, result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)


def test_boot_refuses_incompatible_bind() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "sandbox-token"},
        pollute_bind=True,
    )
    assert result.returncode != 0, result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)


def test_boot_process_exits_after_success() -> None:
    result = _run(process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "sandbox-token"})
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.returncode == 0
    _assert_no_forbidden(result)
