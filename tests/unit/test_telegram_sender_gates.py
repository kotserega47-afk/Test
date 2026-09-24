"""TASK-46: read-only PTB sender compatibility inspector (P1–P12)."""

from __future__ import annotations

from types import SimpleNamespace

from integrations.telegram_sender_gates import (
    REASON_BOT_SHUTDOWN_UNAVAILABLE,
    REASON_GENERAL_REQUEST_MISMATCH,
    REASON_LEFTOVER_DIAGNOSTIC_UNAVAILABLE,
    REASON_REQUEST_GRAPH_AMBIGUOUS,
    REASON_REQUEST_GRAPH_UNAVAILABLE,
    REASON_REQUEST_SHUTDOWN_UNAVAILABLE,
    REASON_SENDER_BOT_UNAVAILABLE,
    inspect_sender_ptb_compatibility,
)


class _CallCounter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("shutdown must not be invoked by inspector")


def _request(*, shutdown: bool = True, diagnostic: bool = True):
    shutdown_fn: object = _CallCounter() if shutdown else object()
    if not shutdown:
        # Explicit non-callable when shutdown=False for missing-capability cases.
        payload = SimpleNamespace(shutdown=None)
    else:
        payload = SimpleNamespace(shutdown=shutdown_fn)
    if diagnostic:
        payload._client = SimpleNamespace(is_closed=False)
    return payload


def _bot(*, graph, shutdown: bool = True):
    bot_shutdown = _CallCounter() if shutdown else object()
    return SimpleNamespace(
        shutdown=bot_shutdown if shutdown else object(),
        _request=graph,
        request=graph[1] if isinstance(graph, tuple) and len(graph) >= 2 else None,
    )


def test_p1_bot_none_sender_bot_unavailable() -> None:
    result = inspect_sender_ptb_compatibility(bot=None, expected_general_request=object())
    assert result.supported is False
    assert result.reason == REASON_SENDER_BOT_UNAVAILABLE
    assert result.close_targets == ()


def test_p2_bot_shutdown_missing_or_non_callable() -> None:
    gu = _request()
    general = _request()
    bot = SimpleNamespace(_request=(gu, general), request=general, shutdown=None)
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is False
    assert result.reason == REASON_BOT_SHUTDOWN_UNAVAILABLE

    bot2 = SimpleNamespace(_request=(gu, general), request=general, shutdown=object())
    result2 = inspect_sender_ptb_compatibility(bot=bot2, expected_general_request=general)
    assert result2.reason == REASON_BOT_SHUTDOWN_UNAVAILABLE


def test_p3_request_graph_missing() -> None:
    bot = SimpleNamespace(shutdown=_CallCounter())
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=object())
    assert result.supported is False
    assert result.reason == REASON_REQUEST_GRAPH_UNAVAILABLE


def test_p4_graph_ambiguous_public_only() -> None:
    general = _request()
    bot = SimpleNamespace(shutdown=_CallCounter(), request=general)
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is False
    assert result.reason == REASON_REQUEST_GRAPH_AMBIGUOUS


def test_p5_general_request_identity_mismatch() -> None:
    gu = _request()
    general = _request()
    other = _request()
    bot = _bot(graph=(gu, general))
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=other)
    assert result.supported is False
    assert result.reason == REASON_GENERAL_REQUEST_MISMATCH


def test_p6_one_request_missing_shutdown() -> None:
    gu = _request()
    general = SimpleNamespace(_client=SimpleNamespace(is_closed=False), shutdown=None)
    bot = _bot(graph=(gu, general))
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is False
    assert result.reason == REASON_REQUEST_SHUTDOWN_UNAVAILABLE


def test_p7_leftover_diagnostic_missing() -> None:
    gu = _request(diagnostic=False)
    general = _request()
    bot = _bot(graph=(gu, general))
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is False
    assert result.reason == REASON_LEFTOVER_DIAGNOSTIC_UNAVAILABLE


def test_p8_recognized_full_graph_pass() -> None:
    gu = _request()
    general = _request()
    bot = _bot(graph=(gu, general))
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is True
    assert result.reason is None
    assert len(result.roles) == 2
    assert result.roles[0].role == "get_updates_request"
    assert result.roles[0].request is gu
    assert result.roles[1].role == "request"
    assert result.roles[1].request is general
    assert result.close_targets == (gu, general)


def test_p9_inspector_calls_zero_shutdown_methods() -> None:
    gu = _request()
    general = _request()
    bot = _bot(graph=(gu, general))
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is True
    assert bot.shutdown.calls == 0
    assert gu.shutdown.calls == 0
    assert general.shutdown.calls == 0


def test_p10_duplicate_physical_request_dedupes_close_targets() -> None:
    shared = _request()
    bot = _bot(graph=(shared, shared))
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=shared)
    assert result.supported is True
    assert len(result.roles) == 2
    assert result.roles[0].request is shared
    assert result.roles[1].request is shared
    assert result.close_targets == (shared,)


def test_p11_unknown_private_shape_fail_closed() -> None:
    general = _request()
    bot = SimpleNamespace(
        shutdown=_CallCounter(),
        _request={"general": general},
        request=general,
    )
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is False
    assert result.reason in (
        REASON_REQUEST_GRAPH_UNAVAILABLE,
        REASON_REQUEST_GRAPH_AMBIGUOUS,
    )


def test_p12_version_string_does_not_alter_pass(monkeypatch) -> None:
    gu = _request()
    general = _request()
    bot = _bot(graph=(gu, general))

    import integrations.telegram_sender_gates as gates

    monkeypatch.setattr(gates, "_telegram_version", lambda: "0.0.0-fake")
    a = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    monkeypatch.setattr(gates, "_telegram_version", lambda: "99.99.99-fake")
    b = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert a.supported is True and b.supported is True
    assert a.reason is None and b.reason is None
    assert a.close_targets == b.close_targets
    assert a.telegram_version != b.telegram_version
