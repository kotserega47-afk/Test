"""Read-only PTB compatibility / request-graph inspector for sender gates (TASK-46).

Does not import ``integrations.telegram_bot`` (that module starts the global loop).
Caller passes ``bot`` and ``expected_general_request``. Never mutates Bot/HTTP/queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

REASON_SENDER_BOT_UNAVAILABLE: Final = "sender_bot_unavailable"
REASON_BOT_SHUTDOWN_UNAVAILABLE: Final = "bot_shutdown_unavailable"
REASON_REQUEST_GRAPH_UNAVAILABLE: Final = "request_graph_unavailable"
REASON_REQUEST_GRAPH_AMBIGUOUS: Final = "request_graph_ambiguous"
REASON_GENERAL_REQUEST_MISMATCH: Final = "general_request_mismatch"
REASON_REQUEST_SHUTDOWN_UNAVAILABLE: Final = "request_shutdown_unavailable"
REASON_LEFTOVER_DIAGNOSTIC_UNAVAILABLE: Final = "leftover_diagnostic_unavailable"

_ABSENT: Final = object()


@dataclass(frozen=True, slots=True)
class SenderRequestRolePlan:
    """One logical role in the Bot request graph."""

    role: str
    request: Any


@dataclass(frozen=True, slots=True)
class SenderPtbCompatibilityResult:
    """Structured capability / graph inspection result (no mutation)."""

    supported: bool
    reason: str | None
    roles: tuple[SenderRequestRolePlan, ...]
    close_targets: tuple[Any, ...]
    telegram_version: str | None = None


@dataclass(frozen=True, slots=True)
class _GraphProbe:
    """Internal discovery outcome: roles XOR refuse_reason."""

    roles: tuple[SenderRequestRolePlan, ...] | None = None
    refuse_reason: str | None = None


def _telegram_version() -> str | None:
    try:
        import telegram

        return getattr(telegram, "__version__", None)
    except Exception:
        return None


def _refuse(
    reason: str,
    *,
    roles: tuple[SenderRequestRolePlan, ...] = (),
) -> SenderPtbCompatibilityResult:
    return SenderPtbCompatibilityResult(
        supported=False,
        reason=reason,
        roles=roles,
        close_targets=(),
        telegram_version=_telegram_version(),
    )


def _probe_getattr(obj: Any, name: str) -> tuple[Any, bool]:
    """Return ``(value_or_ABSENT, ok)``. ``ok=False`` when observation raises."""

    try:
        return getattr(obj, name, _ABSENT), True
    except Exception:
        return None, False


def _discover_owned_request_roles(bot: Any) -> _GraphProbe:
    """Recognize full owned Bot request graph or fail-closed reason.

    Recognized PTB shape: ``bot._request`` is a tuple of **exactly** 2
    ``(getUpdates request, general API request)``. Longer/shorter/non-tuple
    shapes and public-only ``bot.request`` are not accepted (TASK-45/46).
    """

    pair, pair_ok = _probe_getattr(bot, "_request")
    if not pair_ok:
        return _GraphProbe(refuse_reason=REASON_REQUEST_GRAPH_AMBIGUOUS)

    public, public_ok = _probe_getattr(bot, "request")
    if not public_ok:
        return _GraphProbe(refuse_reason=REASON_REQUEST_GRAPH_AMBIGUOUS)
    public_observed = public is not _ABSENT

    if pair is _ABSENT or pair is None:
        if public_observed and public is not None:
            return _GraphProbe(refuse_reason=REASON_REQUEST_GRAPH_AMBIGUOUS)
        return _GraphProbe(refuse_reason=REASON_REQUEST_GRAPH_UNAVAILABLE)

    if not isinstance(pair, tuple):
        return _GraphProbe(refuse_reason=REASON_REQUEST_GRAPH_UNAVAILABLE)

    if len(pair) != 2:
        # Trailing/extra (or short) request objects → ambiguous, not "first two".
        return _GraphProbe(refuse_reason=REASON_REQUEST_GRAPH_AMBIGUOUS)

    get_updates, general = pair[0], pair[1]
    if get_updates is None or general is None:
        return _GraphProbe(refuse_reason=REASON_REQUEST_GRAPH_UNAVAILABLE)

    if public_observed and public is not general:
        # Explicit contradiction between public request and private pair[1].
        return _GraphProbe(refuse_reason=REASON_REQUEST_GRAPH_AMBIGUOUS)

    return _GraphProbe(
        roles=(
            SenderRequestRolePlan(role="get_updates_request", request=get_updates),
            SenderRequestRolePlan(role="request", request=general),
        )
    )


def _has_leftover_diagnostic(req: Any) -> bool | None:
    """True/False for capability, or None if observation itself raises."""

    client, client_ok = _probe_getattr(req, "_client")
    if not client_ok:
        return None
    if client is _ABSENT:
        return False
    if client is None:
        # Recognized: client absent/cleared still allows closed-state diagnosis.
        return True
    closed, closed_ok = _probe_getattr(client, "is_closed")
    if not closed_ok:
        return None
    if closed is _ABSENT:
        return False
    return True


def _request_shutdown_callable(req: Any) -> bool | None:
    """True/False for callable shutdown, or None if observation raises."""

    shutdown, ok = _probe_getattr(req, "shutdown")
    if not ok:
        return None
    if shutdown is _ABSENT:
        return False
    return callable(shutdown)


def _dedupe_close_targets(roles: tuple[SenderRequestRolePlan, ...]) -> tuple[Any, ...]:
    seen: set[int] = set()
    targets: list[Any] = []
    for role in roles:
        key = id(role.request)
        if key in seen:
            continue
        seen.add(key)
        targets.append(role.request)
    return tuple(targets)


def inspect_sender_ptb_compatibility(
    *,
    bot: Any,
    expected_general_request: Any,
) -> SenderPtbCompatibilityResult:
    """Read-only compatibility inspection for future sender HTTP close.

    Never calls ``shutdown`` / ``initialize`` / queue mutation.
    Unknown PTB shapes and raising property probes → fail-closed result.
    """

    if bot is None:
        return _refuse(REASON_SENDER_BOT_UNAVAILABLE)

    shutdown, shutdown_ok = _probe_getattr(bot, "shutdown")
    if not shutdown_ok or shutdown is _ABSENT or not callable(shutdown):
        return _refuse(REASON_BOT_SHUTDOWN_UNAVAILABLE)

    if expected_general_request is None:
        return _refuse(REASON_GENERAL_REQUEST_MISMATCH)

    try:
        probe = _discover_owned_request_roles(bot)
    except Exception:
        return _refuse(REASON_REQUEST_GRAPH_AMBIGUOUS)

    if probe.refuse_reason is not None:
        return _refuse(probe.refuse_reason)

    roles = probe.roles
    if roles is None:
        return _refuse(REASON_REQUEST_GRAPH_UNAVAILABLE)

    general_role = next((r for r in roles if r.role == "request"), None)
    if general_role is None:
        return _refuse(REASON_REQUEST_GRAPH_UNAVAILABLE, roles=roles)
    if general_role.request is not expected_general_request:
        return _refuse(REASON_GENERAL_REQUEST_MISMATCH, roles=roles)

    for role in roles:
        shutdown_ok = _request_shutdown_callable(role.request)
        if shutdown_ok is None or not shutdown_ok:
            return _refuse(REASON_REQUEST_SHUTDOWN_UNAVAILABLE, roles=roles)
        diagnostic = _has_leftover_diagnostic(role.request)
        if diagnostic is None or not diagnostic:
            return _refuse(REASON_LEFTOVER_DIAGNOSTIC_UNAVAILABLE, roles=roles)

    return SenderPtbCompatibilityResult(
        supported=True,
        reason=None,
        roles=roles,
        close_targets=_dedupe_close_targets(roles),
        telegram_version=_telegram_version(),
    )
