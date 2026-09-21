from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.unit.antares_boot_child_runner import run_antares_boot

_ROOT = Path(__file__).resolve().parents[2]
_SYNTHETIC_XLSX = _ROOT / "tests" / "rules_v2" / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"
_EXPECTED_CMDS = json.loads(
    (_ROOT / "tests" / "fixtures" / "behavior_baseline" / "expected_antares_tg_commands.json").read_text(
        encoding="utf-8"
    )
)["commands"]
_PTB_TOKEN = "123456:AA-TEST-antares-build-only"

_SUCCESS_KEYS = (
    "download",
    "hourly",
    "rate",
    "script_job:operator_wallets_ready",
    "wallet",
    "wallet_editor_registry_refresh",
    "wallet_editor_registry_replay",
)


def _run(setup=None, **kwargs):
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        extra_env = setup(sandbox) if setup is not None else None
        if extra_env:
            merged = dict(kwargs.get("process_env") or {})
            merged.update(extra_env)
            kwargs["process_env"] = merged
        return run_antares_boot(sandbox, **kwargs)


def _assert_no_forbidden(result) -> None:
    assert result.harness_ready is True
    assert result.import_attempts == [], result.import_attempts


def _assert_no_workbook_io(result) -> None:
    kinds = _event_kinds(result)
    assert "snapshot_called" not in kinds, result.events
    assert "rules_download_attempted" not in kinds, result.events


def _events_of(result, kind: str) -> list[dict]:
    return [item for item in result.events if item.get("kind") == kind]


def _first_event_index(result, kind: str) -> int:
    kinds = _event_kinds(result)
    assert kind in kinds, result.events
    return kinds.index(kind)


def _assert_run_snapshot_order(result) -> None:
    assembly_at = _first_event_index(result, "assembly_called")
    snapshot_at = _first_event_index(result, "snapshot_called")
    assert assembly_at < snapshot_at, result.events
    snap_events = _events_of(result, "snapshot_called")
    assert snap_events, result.events
    assert snap_events[0].get("force_sync") is True, snap_events
    assert "rules_download_attempted" not in _event_kinds(result), result.events


def _assert_handlers_identity(result) -> None:
    assembled = _events_of(result, "assembly_handlers")
    assert assembled, result.events
    assembled_handlers = assembled[0]["handlers"]
    added = _events_of(result, "handler_added")
    assert [item["handler_id"] for item in added] == [
        item["handler_id"] for item in assembled_handlers
    ], (assembled_handlers, added)
    assert len(added) == 18, added
    commands = []
    documents = []
    for item in added:
        assert item.get("group") == 0, item
        if item.get("class") == "CommandHandler":
            commands.extend(item.get("commands") or [])
        elif item.get("class") == "MessageHandler":
            documents.append(item)
        else:
            raise AssertionError(item)
    assert commands == _EXPECTED_CMDS, commands
    assert len(documents) == 1, documents
    assert "Document.ALL" in str(documents[0].get("filters", "")), documents[0]
    assert documents[0].get("callback") == (
        "modules.antares.document_ingest.handle_wallet_editor_document"
    )
    assert [item.get("callback") for item in added] == [
        item.get("callback") for item in assembled_handlers
    ]


def _assert_successful_application(result) -> None:
    _assert_no_forbidden(result)
    _assert_no_lifecycle(result)
    _assert_run_snapshot_order(result)
    kinds = _event_kinds(result)
    assert kinds.index("snapshot_called") < kinds.index("application_build_called"), result.events
    assert "application_build_ok" in kinds, result.events
    added = _events_of(result, "handler_added")
    build_at = _first_event_index(result, "application_build_ok")
    first_add = kinds.index("handler_added")
    assert build_at < first_add, result.events
    _assert_handlers_identity(result)
    success_lines = [line for line in result.stdout.splitlines() if line.startswith("antares run ok")]
    assert len(success_lines) == 1, result.stdout
    assert "handlers=18" in success_lines[0]
    assert "commands=17" in success_lines[0]
    assert "document=1" in success_lines[0]
    assert "antares boot ok" not in result.stdout
    assert _PTB_TOKEN not in result.stdout
    assert _PTB_TOKEN not in result.stderr


def _assert_no_application(result) -> None:
    kinds = _event_kinds(result)
    assert "application_build_called" not in kinds, result.events
    assert "application_build_ok" not in kinds, result.events
    assert "handler_added" not in kinds, result.events
    assert "handler_add_attempt" not in kinds, result.events


def _assert_no_lifecycle(result) -> None:
    forbidden = _events_of(result, "forbidden_lifecycle")
    assert forbidden == [], forbidden
    combined = result.stdout + result.stderr
    assert "InvalidToken" not in combined


def _assert_boot_isolation(result) -> None:
    _assert_no_forbidden(result)
    _assert_no_workbook_io(result)
    _assert_no_application(result)
    _assert_no_lifecycle(result)


def _event_kinds(result) -> list[str]:
    return [str(item.get("kind", "")) for item in result.events]


def _refusal_reasons(result) -> list[str]:
    return [
        str(item.get("reason", ""))
        for item in result.events
        if item.get("kind") == "assembly_refused"
    ]


def _assert_not_false_refusal(result) -> None:
    combined = result.stdout + result.stderr
    assert "RecursionError" not in combined
    assert "AttributeError" not in combined
    assert "forbidden import" not in combined


def test_boot_rejects_unset_profile() -> None:
    result = _run(dotenv_lines={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN})
    assert result.returncode == 2, result.stderr + result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_boot_isolation(result)


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_boot_rejects_blank_profile(value: str) -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": value},
        dotenv_lines={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN},
    )
    assert result.returncode == 2, result.stderr + result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_boot_isolation(result)


@pytest.mark.parametrize("value", ["raccoon", "wr", "not-a-profile", "Antares"])
def test_boot_rejects_foreign_or_unknown_profile(value: str) -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": value, "TELEGRAM_BOT_TOKEN": _PTB_TOKEN},
    )
    assert result.returncode == 2, result.stderr + result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_boot_isolation(result)


def test_boot_profile_only_in_dotenv_exits_2() -> None:
    result = _run(
        dotenv_lines={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN},
    )
    assert result.returncode == 2, result.stderr + result.stdout
    assert "PROJECT_PROFILE" in result.stderr
    _assert_boot_isolation(result)


def test_boot_accepts_parser_normalized_antares() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "  antares  ", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "antares boot ok" in result.stdout
    assert "commands=17" in result.stdout
    assert "document=1" in result.stdout
    assert "jobs=7" in result.stdout
    for key in _SUCCESS_KEYS:
        assert key in result.stdout
    _assert_boot_isolation(result)


@pytest.mark.parametrize("token", ["", "   "])
def test_boot_rejects_empty_token_after_dotenv(token: str) -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": token},
        dotenv_lines={"TELEGRAM_BOT_TOKEN": "123456:from-file-should-not-override"},
    )
    assert result.returncode == 1, result.stderr + result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_boot_isolation(result)


def test_boot_dotenv_supplies_token_when_process_has_none() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares"},
        dotenv_lines={"TELEGRAM_BOT_TOKEN": "123456:sandbox-from-file"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "antares boot ok" in result.stdout
    assert "sandbox-from-file" not in result.stdout
    assert "sandbox-from-file" not in result.stderr
    _assert_boot_isolation(result)


def test_boot_process_env_wins_over_dotenv_token() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": "   "},
        dotenv_lines={"TELEGRAM_BOT_TOKEN": "123456:sandbox-from-file"},
    )
    assert result.returncode != 0, result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_boot_isolation(result)


def test_boot_success_seven_jobs_and_handlers() -> None:
    result = _run(process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN})
    assert result.returncode == 0, result.stderr + result.stdout
    assert "antares boot ok commands=17 document=1 jobs=7" in result.stdout
    for key in _SUCCESS_KEYS:
        assert key in result.stdout
    _assert_boot_isolation(result)


def test_boot_refuses_polluted_registry() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN},
        pollute_registry=True,
    )
    assert result.returncode != 0, result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_boot_isolation(result)
    _assert_not_false_refusal(result)
    assert "conflict_injected" in _event_kinds(result)
    assert "assembly_called" in _event_kinds(result)
    reasons = _refusal_reasons(result)
    assert reasons, result.events
    assert any("foreign keys" in reason for reason in reasons), reasons
    assert "antares assembly failed:" in result.stderr
    assert "foreign keys" in result.stderr


def test_boot_refuses_incompatible_bind() -> None:
    result = _run(
        process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN},
        pollute_bind=True,
    )
    assert result.returncode != 0, result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_boot_isolation(result)
    _assert_not_false_refusal(result)
    assert "conflict_injected" in _event_kinds(result)
    assert "assembly_called" in _event_kinds(result)
    reasons = _refusal_reasons(result)
    assert reasons, result.events
    assert any(
        "different AccessRules" in reason or "different logger" in reason for reason in reasons
    ), reasons
    assert "antares assembly failed:" in result.stderr
    assert "different AccessRules" in result.stderr or "different logger" in result.stderr


def test_boot_process_exits_after_success() -> None:
    result = _run(process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN})
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.returncode == 0
    _assert_boot_isolation(result)


def _run_ok_env(**kwargs):
    process_env = {
        "PROJECT_PROFILE": "antares",
        "TELEGRAM_BOT_TOKEN": _PTB_TOKEN,
        **(kwargs.pop("process_env", None) or {}),
    }
    return _run(process_env=process_env, **kwargs)


def _accepted_local_xlsx(dest: Path) -> Path:
    """C5 synthetic copy whose access ids AccessRules can consume.

    Baseline stores chat_id/user_id as Excel numbers; the v2 bridge stringifies
    them as ``'1.0'``, and ``AccessRules.get_snapshot`` then does ``int(...)``.
    Keep the published workbook, write ids as integer-looking text without a
    decimal so the real provider+AccessRules path succeeds.
    """

    import pandas as pd

    dest.parent.mkdir(parents=True, exist_ok=True)
    sheets = pd.read_excel(_SYNTHETIC_XLSX, sheet_name=None, engine="openpyxl")
    access = sheets.get("access")
    if access is not None and not access.empty:
        if "chat_id" in access.columns:
            access["chat_id"] = "private"
        if "user_id" in access.columns:
            # Keep a non-numeric Excel type so pandas does not coerce to 1.0.
            access["user_id"] = access["user_id"].map(
                lambda v: "" if pd.isna(v) else f"{int(float(v))} "
            )
        sheets["access"] = access
    with pd.ExcelWriter(dest, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return dest


def _empty_jobs_workbook(path: Path) -> Path:
    import pandas as pd

    from core.rules_v2.contract_schema import SHEET_SCHEMAS

    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 3},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
            {"key": "updated_by", "value": "test"},
        ]
    )
    frames["access"] = pd.DataFrame(
        [{"chat_id": "1", "user_id": "1", "level": 1, "enabled": 1, "note": ""}]
    )
    frames["commands"] = pd.DataFrame(
        [
            {
                "command": "/start",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
                "note": "",
            }
        ]
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
    return path


def test_explicit_boot_matches_default_and_skips_workbook() -> None:
    result = _run_ok_env(argv=["boot"])
    assert result.returncode == 0, result.stderr + result.stdout
    assert "antares boot ok commands=17 document=1 jobs=7" in result.stdout
    assert "antares run ok" not in result.stdout
    _assert_boot_isolation(result)


def test_run_success_reads_synthetic_local_xlsx() -> None:
    def setup(sandbox: Path) -> None:
        _accepted_local_xlsx(sandbox / "rules.xlsx")

    result = _run_ok_env(setup=setup, argv=["run"])
    assert result.returncode == 0, result.stderr + result.stdout
    _assert_successful_application(result)


def test_run_dotenv_supplies_rules_path_when_process_has_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        dest = sandbox / "from_dotenv.xlsx"
        _accepted_local_xlsx(dest)
        result = run_antares_boot(
            sandbox,
            argv=["run"],
            process_env={"PROJECT_PROFILE": "antares", "TELEGRAM_BOT_TOKEN": _PTB_TOKEN},
            dotenv_lines={"RULES_XLSX_PATH": str(dest)},
            pop_env=("RULES_XLSX_PATH",),
        )
    assert result.returncode == 0, result.stderr + result.stdout
    _assert_successful_application(result)
    assert "snapshot_called" in _event_kinds(result)
    assert "rules_download_attempted" not in _event_kinds(result)


def test_run_process_env_rules_path_wins_over_dotenv() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        good = sandbox / "good.xlsx"
        _accepted_local_xlsx(good)
        missing = sandbox / "missing.xlsx"
        result = run_antares_boot(
            sandbox,
            argv=["run"],
            process_env={
                "PROJECT_PROFILE": "antares",
                "TELEGRAM_BOT_TOKEN": _PTB_TOKEN,
                "RULES_XLSX_PATH": str(missing),
            },
            dotenv_lines={"RULES_XLSX_PATH": str(good)},
        )
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)
    _assert_no_workbook_io(result)
    assert "not an existing file" in result.stderr
    _assert_no_application(result)
    _assert_no_lifecycle(result)


def test_run_empty_rules_path_skips_snapshot() -> None:
    result = _run_ok_env(argv=["run"], process_env={"RULES_XLSX_PATH": ""})
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    assert "RULES_XLSX_PATH is not set" in result.stderr
    _assert_boot_isolation(result)


def test_run_missing_file_skips_snapshot() -> None:
    result = _run_ok_env(argv=["run"])
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    assert "not an existing file" in result.stderr
    _assert_boot_isolation(result)


def test_run_directory_skips_snapshot() -> None:
    def setup(sandbox: Path) -> dict[str, str]:
        target = sandbox / "as_dir.xlsx"
        target.mkdir()
        return {"RULES_XLSX_PATH": str(target)}

    result = _run_ok_env(setup=setup, argv=["run"])
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    _assert_boot_isolation(result)
    assert "not an existing file" in result.stderr


def test_run_wrong_suffix_skips_snapshot() -> None:
    def setup(sandbox: Path) -> dict[str, str]:
        target = sandbox / "rules.txt"
        target.write_bytes(_SYNTHETIC_XLSX.read_bytes())
        return {"RULES_XLSX_PATH": str(target)}

    result = _run_ok_env(setup=setup, argv=["run"])
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    _assert_boot_isolation(result)
    assert "must be a .xlsx file" in result.stderr


def test_run_corrupt_xlsx_no_success_line() -> None:
    def setup(sandbox: Path) -> dict[str, str]:
        target = sandbox / "corrupt.xlsx"
        target.write_bytes(b"PK\x03\x04truncated-not-a-workbook")
        return {"RULES_XLSX_PATH": str(target)}

    result = _run_ok_env(setup=setup, argv=["run"])
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)
    assert "snapshot_called" in _event_kinds(result)
    assert "rules_download_attempted" not in _event_kinds(result)
    assert "antares local rules failed:" in result.stderr
    _assert_no_application(result)
    _assert_no_lifecycle(result)


def test_run_publish_policy_reject_no_success_line() -> None:
    def setup(sandbox: Path) -> dict[str, str]:
        target = sandbox / "empty_jobs.xlsx"
        _empty_jobs_workbook(target)
        return {"RULES_XLSX_PATH": str(target), "RULES_CONTRACT_STRICT": "1"}

    result = _run_ok_env(setup=setup, argv=["run"])
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)
    _assert_run_snapshot_order(result)
    assert "antares local rules failed: ContractPublishRejected:" in result.stderr
    assert "RULE_EMPTY_JOBS" in result.stderr
    rejected = _events_of(result, "publish_rejected")
    assert rejected, result.events
    assert rejected[0].get("exc_name") == "ContractPublishRejected"
    assert "core.rules_v2.contract_publish.ContractPublishRejected" in str(
        rejected[0].get("exc_type", "")
    ) or str(rejected[0].get("exc_type", "")).endswith("ContractPublishRejected")
    assert "RULE_EMPTY_JOBS" in list(rejected[0].get("blocking_issue_codes") or [])
    assert rejected[0].get("policy_mode") == "strict"
    evaluated = _events_of(result, "publish_evaluated")
    assert evaluated, result.events
    assert evaluated[-1].get("publish_allowed") is False
    assert "RULE_EMPTY_JOBS" in list(evaluated[-1].get("blocking_issue_codes") or [])
    failed = _events_of(result, "snapshot_failed")
    assert failed, result.events
    assert failed[0].get("exc_name") == "ContractPublishRejected"
    assert "ValueError" not in result.stderr
    assert "rules download blocked" not in result.stderr
    _assert_no_application(result)
    _assert_no_lifecycle(result)


@pytest.mark.parametrize(
    ("pollute", "reason"),
    [
        ({"pollute_registry": True}, "foreign keys"),
        ({"pollute_bind": True}, "different AccessRules"),
    ],
)
def test_run_assembly_conflict_skips_snapshot(pollute: dict[str, bool], reason: str) -> None:
    result = _run_ok_env(argv=["run"], **pollute)
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)
    _assert_no_workbook_io(result)
    assert "antares assembly failed:" in result.stderr
    assert reason in result.stderr
    assert "assembly_called" in _event_kinds(result)
    refused = _refusal_reasons(result)
    assert refused, result.events
    assert any(reason in item for item in refused), refused
    _assert_no_application(result)
    _assert_no_lifecycle(result)


def test_unknown_arguments_exit_nonzero() -> None:
    result = _run_ok_env(argv=["run", "extra"])
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    assert "unknown antares argument" in result.stderr
    _assert_no_forbidden(result)
    assert "assembly_called" not in _event_kinds(result)
    _assert_no_workbook_io(result)
    _assert_no_application(result)
    _assert_no_lifecycle(result)


def test_run_injected_build_failure_is_specific() -> None:
    def setup(sandbox: Path) -> None:
        _accepted_local_xlsx(sandbox / "rules.xlsx")

    result = _run_ok_env(setup=setup, argv=["run"], fail_build=True)
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)
    _assert_run_snapshot_order(result)
    kinds = _event_kinds(result)
    assert kinds.index("snapshot_called") < kinds.index("application_build_called"), result.events
    assert "application_build_injected_failure" in kinds, result.events
    assert "application_build_ok" not in kinds, result.events
    assert "handler_added" not in kinds, result.events
    _assert_no_lifecycle(result)
    assert "antares application build failed: RuntimeError: injected application build failure" in result.stderr
    assert "InvalidToken" not in result.stderr
    assert "blocked in antares boot harness" not in result.stderr or "injected application build failure" in result.stderr


def test_run_injected_add_handler_failure_after_real_adds() -> None:
    def setup(sandbox: Path) -> None:
        _accepted_local_xlsx(sandbox / "rules.xlsx")

    result = _run_ok_env(setup=setup, argv=["run"], fail_add_handler=True)
    assert result.returncode != 0, result.stdout
    assert "antares run ok" not in result.stdout
    assert "antares boot ok" not in result.stdout
    _assert_no_forbidden(result)
    _assert_run_snapshot_order(result)
    assert "application_build_ok" in _event_kinds(result), result.events
    added = _events_of(result, "handler_added")
    assert len(added) == 3, added
    injected = _events_of(result, "handler_add_injected_failure")
    assert injected, result.events
    assert injected[0].get("added_before") == 3
    attempts = _events_of(result, "handler_add_attempt")
    assert len(attempts) == 4, attempts
    assembled = _events_of(result, "assembly_handlers")[0]["handlers"]
    assert [item["handler_id"] for item in added] == [
        item["handler_id"] for item in assembled[:3]
    ]
    _assert_no_lifecycle(result)
    assert "antares handler attach failed: RuntimeError: injected add_handler failure" in result.stderr
    assert "InvalidToken" not in result.stderr


