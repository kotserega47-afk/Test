"""Source inventory of JOB_REGISTRY.update / get_handlers() declarations.

This is not the assembled runtime registry. Live keys and handlers are
checked in tests/test_behavior_baseline_registration.py.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = ROOT / "tests" / "fixtures" / "behavior_baseline"


def _is_job_registry_update(func: ast.AST) -> bool:
    if not isinstance(func, ast.Attribute) or func.attr != "update":
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "JOB_REGISTRY"


def _string_keys_from_job_registry_update(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_job_registry_update(node.func):
            continue
        if not node.args:
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.Dict):
            for key in arg0.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def _get_handlers_fn(tree: ast.Module) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "get_handlers":
            return node
    raise AssertionError("get_handlers() not found at module level")


def _command_handler_names_in_get_handlers(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = _get_handlers_fn(tree)
    names: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "CommandHandler" and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                names.append(arg0.value)
    return names


def _document_handler_in_get_handlers(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = _get_handlers_fn(tree)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "MessageHandler":
            return True
    return False


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


def test_source_inventory_job_registry_update_keys() -> None:
    expected = json.loads((_FIXTURE / "expected_job_registry_keys.json").read_text(encoding="utf-8"))
    found = set()
    found |= _string_keys_from_job_registry_update(ROOT / "integrations" / "tg_commands.py")
    found |= _string_keys_from_job_registry_update(ROOT / "integrations" / "raccoon_jobs.py")
    found |= _script_job_types(ROOT / "integrations" / "script_jobs" / "registry.py")
    assert found == set(expected["keys"])


def test_source_inventory_get_handlers_commands_and_document() -> None:
    expected = json.loads((_FIXTURE / "expected_tg_commands.json").read_text(encoding="utf-8"))
    path = ROOT / "integrations" / "tg_commands.py"
    names = _command_handler_names_in_get_handlers(path)
    assert names == expected["commands"]
    assert _document_handler_in_get_handlers(path) is True
    assert expected.get("also_registers_document_handler") is True
