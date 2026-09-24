"""Process-local immutable Antares sender ownership claim (TASK-46).

Side-effect-free: must not import integrations.telegram_bot or Telegram stacks.
Exact object identity is the proof (TASK-45 C+D).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Final

_UNCLAIMED: Final = "UNCLAIMED"
_CLAIMED: Final = "CLAIMED_ANTARES"


@dataclass(frozen=True, slots=True)
class AntaresSenderOwnershipAttestation:
    """Immutable process-local ownership proof. Identity is the object itself."""


@dataclass(frozen=True, slots=True)
class AntaresSenderOwnershipValidation:
    ok: bool
    reason: str | None = None


class AntaresSenderOwnershipError(RuntimeError):
    """Claim state is corrupted or conflicting; no silent replace."""


_lock = threading.Lock()
_state: str = _UNCLAIMED
_claim: AntaresSenderOwnershipAttestation | None = None


def claim_antares_sender_ownership() -> AntaresSenderOwnershipAttestation:
    """Claim once per process; repeat returns the exact same attestation."""

    global _state, _claim
    with _lock:
        if _state == _CLAIMED and _claim is not None:
            return _claim
        if _state == _UNCLAIMED and _claim is None:
            attestation = AntaresSenderOwnershipAttestation()
            _claim = attestation
            _state = _CLAIMED
            return attestation
        raise AntaresSenderOwnershipError(
            f"antares sender ownership state inconsistent: "
            f"state={_state!r} claim_is_none={_claim is None}"
        )


def validate_antares_sender_ownership(proof: object) -> AntaresSenderOwnershipValidation:
    """PASS only when ``proof is`` the current process attestation."""

    with _lock:
        if _state != _CLAIMED or _claim is None:
            return AntaresSenderOwnershipValidation(ok=False, reason="unclaimed")
        if proof is _claim:
            return AntaresSenderOwnershipValidation(ok=True, reason=None)
        return AntaresSenderOwnershipValidation(ok=False, reason="proof_mismatch")


def _reset_antares_sender_ownership_for_tests() -> None:
    """Test-only: clear process claim. Not a production API."""

    global _state, _claim
    with _lock:
        _state = _UNCLAIMED
        _claim = None
