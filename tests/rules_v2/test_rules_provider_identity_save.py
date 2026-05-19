"""PR-8: identity registry save after successful ``get_snapshot_v2`` publish."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.rules_provider import (
    ContractPublishRejected,
    get_snapshot_v2,
    invalidate_rules_v2_cache,
)
from core.rules_v2.contract_errors import RULE_DUPLICATE_LIMIT, make_issue
from core.rules_v2.identity_registry_io import build_registry_from_manifest
from core.rules_v2.ops_rules_validate_summary import build_rules_validate_payload
from core.rules_v2.validation_issues import ValidationSeverity
from core.rules_v2.validation_snapshot import validate_snapshot as real_validate_snapshot

C5_BASELINE = Path(__file__).resolve().parent / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"

_SAVE_FN = "core.rules_v2.identity_registry_io.save_identity_registry"
_BUILD_FN = "core.rules_v2.identity_registry_io.build_registry_from_manifest"


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    invalidate_rules_v2_cache()
    yield
    invalidate_rules_v2_cache()


@pytest.fixture(autouse=True)
def _clear_identity_save_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RULES_IDENTITY_SAVE", raising=False)


@pytest.fixture
def rules_xlsx_baseline(monkeypatch: pytest.MonkeyPatch) -> Path:
    assert C5_BASELINE.is_file()
    monkeypatch.setenv("RULES_XLSX_PATH", str(C5_BASELINE))
    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    monkeypatch.delenv("RULES_CONTRACT_SHADOW", raising=False)
    return C5_BASELINE


def test_save_off_does_not_call_save_identity_registry(rules_xlsx_baseline: Path) -> None:
    with patch(_SAVE_FN) as save_m:
        snap = get_snapshot_v2(force_sync=True)
    save_m.assert_not_called()
    assert snap.meta.ruleset_version


def test_save_on_publish_success_calls_save_once(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")
    with patch(_SAVE_FN) as save_m:
        get_snapshot_v2(force_sync=True)
    save_m.assert_called_once()


def test_save_oserror_does_not_block_snapshot_return(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("simulated registry fs failure")

    with patch(_SAVE_FN, side_effect=_boom):
        snap = get_snapshot_v2(force_sync=True)
    assert snap.meta.ruleset_version


def test_publish_rejected_does_not_call_save(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")
    monkeypatch.setenv("RULES_CONTRACT_STRICT", "1")

    def _with_dup(snap, *, strict: bool = False):
        return list(real_validate_snapshot(snap, strict=strict)) + [
            make_issue(
                RULE_DUPLICATE_LIMIT,
                "blocking for PR-8 save hook",
                sheet="wallet_limits",
                severity=ValidationSeverity.ERROR,
            )
        ]

    with (
        patch("core.rules_v2.contract_publish.validate_snapshot", side_effect=_with_dup),
        patch(_SAVE_FN) as save_m,
    ):
        with pytest.raises(ContractPublishRejected):
            get_snapshot_v2(force_sync=True)
    save_m.assert_not_called()


def test_cache_hit_does_not_call_save_again(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")
    with patch(_SAVE_FN) as save_m:
        get_snapshot_v2(force_sync=False)
        get_snapshot_v2(force_sync=False)
    save_m.assert_called_once()


def test_build_registry_receives_meta_version_from_snapshot(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")
    with patch(_BUILD_FN, wraps=build_registry_from_manifest) as build_m:
        snap = get_snapshot_v2(force_sync=True)
    build_m.assert_called_once()
    assert build_m.call_args.kwargs["meta_version"] == snap.meta.ruleset_version


def test_rules_validate_cold_cache_does_not_save_registry(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/rules_validate`` ``runtime_get`` uses ``suppress_identity_registry_save`` on cold cache."""

    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")
    with patch(_SAVE_FN) as save_m:
        build_rules_validate_payload()
    save_m.assert_not_called()


def test_rules_validate_warm_cache_does_not_save_again(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")
    get_snapshot_v2(force_sync=True)
    with patch(_SAVE_FN) as save_m:
        build_rules_validate_payload()
    save_m.assert_not_called()


def _payload_invariant_fields(p) -> dict:
    return {
        "meta_version": p.meta_version,
        "validation_mode": p.validation_mode,
        "publish_allowed": p.publish_allowed,
        "contract_issue_total": p.contract_issue_total,
        "contract_error_count": p.contract_error_count,
        "contract_warning_count": p.contract_warning_count,
        "has_blocking_contract": p.has_blocking_contract,
        "snapshot_fingerprint": p.snapshot_fingerprint,
        "active_runtime_readable": p.active_runtime_readable,
        "active_ruleset_version": p.active_ruleset_version,
        "active_snapshot_fingerprint": p.active_snapshot_fingerprint,
        "fingerprint_matches_evaluated": p.fingerprint_matches_evaluated,
        "identity_shadow_issue_total": p.identity_shadow_issue_total,
    }


def test_rules_validate_payload_invariant_with_identity_save_enabled(
    rules_xlsx_baseline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")
    with patch(_SAVE_FN):
        with_save = build_rules_validate_payload()
    invalidate_rules_v2_cache()
    baseline = build_rules_validate_payload()
    assert _payload_invariant_fields(baseline) == _payload_invariant_fields(with_save)
