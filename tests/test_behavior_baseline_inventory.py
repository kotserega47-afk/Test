"""Frozen registration inventory: JOB_REGISTRY keys and TG command names.

This is code-on-this-SHA registration, not a live schedule and not production.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = ROOT / "tests" / "fixtures" / "behavior_baseline"


def _string_keys_from_update_calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "update"):
            continue
        if not node.args:
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.Dict):
            for key in arg0.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def _command_handler_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "CommandHandler" and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                names.append(arg0.value)
    return names


def _script_job_types(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "SCRIPT_REGISTRY" and isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(f"script_job:{key.value}")
    return keys


def test_job_registry_keys_match_frozen_inventory() -> None:
    expected = json.loads((_FIXTURE / "expected_job_registry_keys.json").read_text(encoding="utf-8"))
    found = set()
    found |= _string_keys_from_update_calls(ROOT / "integrations" / "tg_commands.py")
    found |= _string_keys_from_update_calls(ROOT / "integrations" / "raccoon_jobs.py")
    found |= _script_job_types(ROOT / "integrations" / "script_jobs" / "registry.py")
    assert found == set(expected["keys"])


def test_tg_command_names_match_frozen_inventory() -> None:
    expected = json.loads((_FIXTURE / "expected_tg_commands.json").read_text(encoding="utf-8"))
    names = _command_handler_names(ROOT / "integrations" / "tg_commands.py")
    assert names == expected["commands"]
