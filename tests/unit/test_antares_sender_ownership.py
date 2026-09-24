"""TASK-46: process-local Antares sender ownership claim (O1–O10)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from core.antares_sender_ownership import (
    AntaresSenderOwnershipAttestation,
    AntaresSenderOwnershipError,
    AntaresSenderOwnershipValidation,
    _reset_antares_sender_ownership_for_tests,
    claim_antares_sender_ownership,
    validate_antares_sender_ownership,
)

_OWNERSHIP_SRC = (
    Path(__file__).resolve().parents[2] / "core" / "antares_sender_ownership.py"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_ownership() -> None:
    _reset_antares_sender_ownership_for_tests()
    yield
    _reset_antares_sender_ownership_for_tests()


def test_o1_first_claim_creates_attestation() -> None:
    proof = claim_antares_sender_ownership()
    assert isinstance(proof, AntaresSenderOwnershipAttestation)


def test_o2_second_claim_same_object() -> None:
    first = claim_antares_sender_ownership()
    second = claim_antares_sender_ownership()
    assert first is second


def test_o3_validator_accepts_exact_proof() -> None:
    proof = claim_antares_sender_ownership()
    result = validate_antares_sender_ownership(proof)
    assert isinstance(result, AntaresSenderOwnershipValidation)
    assert result.ok is True
    assert result.reason is None


def test_o4_fake_equivalent_object_rejected() -> None:
    claim_antares_sender_ownership()
    fake = AntaresSenderOwnershipAttestation()
    result = validate_antares_sender_ownership(fake)
    assert result.ok is False
    assert result.reason == "proof_mismatch"


def test_o5_env_mutation_after_claim_irrelevant(monkeypatch: pytest.MonkeyPatch) -> None:
    proof = claim_antares_sender_ownership()
    monkeypatch.setenv("PROJECT_PROFILE", "raccoon")
    monkeypatch.setenv("ANTARES_SENDER_OWNER", "spoof")
    again = claim_antares_sender_ownership()
    assert again is proof
    assert validate_antares_sender_ownership(proof).ok is True


def test_o6_work_admission_seal_is_not_proof() -> None:
    # Do not import modules.antares.work_admission here (heavy deps). Prove seal-like
    # objects never mint/validate as process ownership proof.
    class FakeAdmission:
        def seal(self) -> str:
            return "sealed-token"

    assert validate_antares_sender_ownership(object()).reason == "unclaimed"
    admission = FakeAdmission()
    sealed = admission.seal()
    assert validate_antares_sender_ownership(sealed).ok is False
    assert validate_antares_sender_ownership(admission).ok is False
    assert validate_antares_sender_ownership(object()).reason == "unclaimed"

    proof = claim_antares_sender_ownership()
    assert proof is not admission
    assert proof is not sealed
    assert validate_antares_sender_ownership(proof).ok is True


def test_o7_reset_is_test_helper_only() -> None:
    import core.antares_sender_ownership as mod

    assert hasattr(mod, "_reset_antares_sender_ownership_for_tests")
    assert not hasattr(mod, "release_antares_sender_ownership")
    assert not hasattr(mod, "reset_antares_sender_ownership")
    proof = claim_antares_sender_ownership()
    _reset_antares_sender_ownership_for_tests()
    again = claim_antares_sender_ownership()
    assert again is not proof


def test_o8_ownership_module_import_does_not_pull_telegram() -> None:
    import ast

    tree = ast.parse(_OWNERSHIP_SRC)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".", 1)[0])
    assert "telegram" not in imported
    assert "integrations" not in imported
    assert "jobs" not in imported
    assert "playwright" not in imported

    banned = (
        "integrations.telegram_bot",
        "telegram",
        "telegram.ext",
        "jobs",
    )
    before = {name: name in sys.modules for name in banned}
    import core.antares_sender_ownership as mod

    assert mod.claim_antares_sender_ownership is not None
    for name in banned:
        assert (name in sys.modules) == before[name], name


def test_o9_claim_does_not_start_thread_or_network() -> None:
    before_threads = {t.ident for t in threading.enumerate()}
    claim_antares_sender_ownership()
    after_threads = {t.ident for t in threading.enumerate()}
    assert after_threads == before_threads
    assert "integrations.telegram_bot" not in sys.modules


def test_o10_corrupted_state_explicit_failure_no_replace() -> None:
    import core.antares_sender_ownership as mod

    claim_antares_sender_ownership()
    with mod._lock:
        mod._state = mod._CLAIMED
        mod._claim = None
    with pytest.raises(AntaresSenderOwnershipError):
        claim_antares_sender_ownership()
    with mod._lock:
        # Original claim must not be silently replaced.
        assert mod._claim is None
        assert mod._state == mod._CLAIMED
