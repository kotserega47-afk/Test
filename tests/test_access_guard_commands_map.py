"""
Guard ``unknown_command`` diagnostics: ``commands_map`` is built in ``core.access_rules``
from snapshot.commands + roles + command_policies. Roles must include every ``level_N``
referenced by ``commands.required_level``, not only levels present on ``access``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from core.access_guard import AccessContext, check_access
from core.access_rules import AccessRules
from core.rules_v2.bridge_legacy import _build_access_rules, _build_commands_and_policies, _build_roles
from core.rules_v2.models import (
    AccessRule,
    CommandDef,
    CommandPolicy,
    MetaInfo,
    RoleDef,
    RulesSnapshotV2,
)


def test_build_roles_includes_required_level_from_commands_sheet() -> None:
    sheets = {
        "access": pd.DataFrame(
            [{"chat_id": "-1003429793111", "user_id": 8116498347, "level": 2, "enabled": 1, "note": ""}]
        ),
        "commands": pd.DataFrame(
            [
                {
                    "command": "rules_validate",
                    "required_level": 1,
                    "allow_private": 1,
                    "allow_groups": 1,
                    "enabled": 1,
                }
            ]
        ),
    }
    roles = _build_roles(sheets)
    assert "level_1" in roles
    assert "level_2" in roles
    assert roles["level_1"].role_level == 1


def test_commands_map_contains_rules_validate_when_access_users_higher_level() -> None:
    """Regression: command requires level 1 while users in access are level 2 — role level_1 must still exist."""
    sheets = {
        "access": pd.DataFrame(
            [{"chat_id": "-1003429793111", "user_id": 8116498347, "level": 2, "enabled": 1, "note": ""}]
        ),
        "commands": pd.DataFrame(
            [
                {
                    "command": "rules_validate",
                    "required_level": 1,
                    "allow_private": 1,
                    "allow_groups": 1,
                    "enabled": 1,
                }
            ]
        ),
    }
    roles = _build_roles(sheets)
    commands, policies = _build_commands_and_policies(sheets)
    access_rules_list = _build_access_rules(sheets, roles)

    meta = MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc),
        updated_by="test",
    )
    snap = RulesSnapshotV2(
        meta=meta,
        roles=roles,
        commands=commands,
        command_policies=policies,
        access_rules=access_rules_list,
    )

    with patch("core.access_rules.get_snapshot_v2", return_value=snap):
        rules = AccessRules()
        rules.invalidate()
        wrapper = rules.get_snapshot()

    assert "rules_validate" in wrapper.commands_map
    rule = wrapper.commands_map["rules_validate"]
    assert rule.required_level == 1
    assert rule.allow_private is True
    assert rule.allow_groups is True


def test_guard_accepts_slash_or_no_slash_command_name() -> None:
    """``check_access`` normalizes the same way as ``commands_map`` keys (no leading slash, lower)."""
    roles = {
        "level_1": RoleDef(role_key="level_1", role_level=1, display_name="L1"),
    }
    cmd_key = "rules_validate"
    commands = {
        cmd_key: CommandDef(
            command_key=cmd_key,
            command_text="rules_validate",
            job_key=None,
            display_name="rules_validate",
            enabled=True,
        )
    }
    policies = {
        cmd_key: CommandPolicy(
            command_key=cmd_key,
            min_role_key="level_1",
            allow_private=True,
            allow_groups=True,
            enabled=True,
        )
    }
    access_rules_list = [
        AccessRule(chat_id="private", user_id="1", role_key="level_1", enabled=True),
    ]
    meta = MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc),
        updated_by="test",
    )
    snap = RulesSnapshotV2(
        meta=meta,
        roles=roles,
        commands=commands,
        command_policies=policies,
        access_rules=access_rules_list,
    )

    with patch("core.access_rules.get_snapshot_v2", return_value=snap):
        rules = AccessRules()
        rules.invalidate()
        ctx = AccessContext(chat_type="private", chat_id=1, user_id=1)

        ok1, r1, _ = check_access(rules, ctx, "rules_validate")
        ok2, r2, _ = check_access(rules, ctx, "/rules_validate")
        assert ok1 and r1 == "ok"
        assert ok2 and r2 == "ok"
