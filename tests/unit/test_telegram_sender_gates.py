"""TASK-46: read-only PTB sender compatibility inspector (P1–P12 + blockers)."""

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
    if not shutdown:
        payload = SimpleNamespace(shutdown=None)
    else:
        payload = SimpleNamespace(shutdown=_CallCounter())
    if diagnostic:
        payload._client = SimpleNamespace(is_closed=False)
    return payload


def _bot(*, graph, shutdown: bool = True):
    bot_shutdown = _CallCounter() if shutdown else object()
    return SimpleNamespace(
        shutdown=bot_shutdown if shutdown else object(),
        _request=graph,
        request=graph[1] if isinstance(graph, tuple) and len(graph) == 2 else None,
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
    assert result.close_targets == ()


def test_p11b_tuple_length_three_fail_closed() -> None:
    """BLOCKER 2: trailing third request must not PASS on first two."""

    gu = _request()
    general = _request()
    extra = _request()
    bot = SimpleNamespace(
        shutdown=_CallCounter(),
        _request=(gu, general, extra),
        request=general,
    )
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is False
    assert result.reason == REASON_REQUEST_GRAPH_AMBIGUOUS
    assert result.close_targets == ()
    assert bot.shutdown.calls == 0
    assert gu.shutdown.calls == 0
    assert general.shutdown.calls == 0
    assert extra.shutdown.calls == 0


def test_p11c_raising_property_probes_fail_closed() -> None:
    """BLOCKER 3: raising capability properties → fail-closed, no crash."""

    gu = _request()
    general = _request()

    class _BotShutdownRaises:
        _request = (gu, general)
        request = general

        @property
        def shutdown(self):
            raise RuntimeError("bot.shutdown boom")

    r1 = inspect_sender_ptb_compatibility(
        bot=_BotShutdownRaises(), expected_general_request=general
    )
    assert r1.supported is False
    assert r1.reason == REASON_BOT_SHUTDOWN_UNAVAILABLE
    assert r1.close_targets == ()

    class _BotRequestAttrRaises:
        shutdown = _CallCounter()

        @property
        def _request(self):
            raise RuntimeError("_request boom")

        @property
        def request(self):
            raise RuntimeError("request boom")

    r2 = inspect_sender_ptb_compatibility(
        bot=_BotRequestAttrRaises(), expected_general_request=general
    )
    assert r2.supported is False
    assert r2.reason in (
        REASON_REQUEST_GRAPH_AMBIGUOUS,
        REASON_REQUEST_GRAPH_UNAVAILABLE,
    )
    assert r2.close_targets == ()

    class _ReqShutdownRaises:
        _client = SimpleNamespace(is_closed=False)

        @property
        def shutdown(self):
            raise RuntimeError("request.shutdown boom")

    bad_shutdown = _ReqShutdownRaises()
    bot3 = SimpleNamespace(
        shutdown=_CallCounter(),
        _request=(gu, bad_shutdown),
        request=bad_shutdown,
    )
    r3 = inspect_sender_ptb_compatibility(bot=bot3, expected_general_request=bad_shutdown)
    assert r3.supported is False
    assert r3.reason == REASON_REQUEST_SHUTDOWN_UNAVAILABLE
    assert r3.close_targets == ()

    class _ClientRaises:
        shutdown = _CallCounter()

        @property
        def _client(self):
            raise RuntimeError("_client boom")

    bad_diag = _ClientRaises()
    bot4 = SimpleNamespace(
        shutdown=_CallCounter(),
        _request=(gu, bad_diag),
        request=bad_diag,
    )
    r4 = inspect_sender_ptb_compatibility(bot=bot4, expected_general_request=bad_diag)
    assert r4.supported is False
    assert r4.reason == REASON_LEFTOVER_DIAGNOSTIC_UNAVAILABLE
    assert r4.close_targets == ()

    class _ClientIsClosedRaises:
        @property
        def is_closed(self):
            raise RuntimeError("is_closed boom")

    class _ReqWithBadIsClosed:
        shutdown = _CallCounter()
        _client = _ClientIsClosedRaises()

    bad_is_closed = _ReqWithBadIsClosed()
    bot5 = SimpleNamespace(
        shutdown=_CallCounter(),
        _request=(gu, bad_is_closed),
        request=bad_is_closed,
    )
    r5 = inspect_sender_ptb_compatibility(bot=bot5, expected_general_request=bad_is_closed)
    assert r5.supported is False
    assert r5.reason == REASON_LEFTOVER_DIAGNOSTIC_UNAVAILABLE
    assert r5.close_targets == ()


def test_p11d_public_request_contradicts_pair_fail_closed() -> None:
    gu = _request()
    general = _request()
    other = _request()
    bot = SimpleNamespace(
        shutdown=_CallCounter(),
        _request=(gu, general),
        request=other,
    )
    result = inspect_sender_ptb_compatibility(bot=bot, expected_general_request=general)
    assert result.supported is False
    assert result.reason == REASON_REQUEST_GRAPH_AMBIGUOUS
    assert result.close_targets == ()


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
