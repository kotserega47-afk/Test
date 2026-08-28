from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.job_runner import Actor, exclusive_job
from integrations.wallet_editor_auto_enable import (
    AUTO_ENABLE_JOB_TYPE,
    VERDICT_BUSY,
    run_auto_enable_exclusive,
)


def test_exclusive_job_second_caller_gets_none(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    actor = Actor(kind="tg", chat_id=1, user_id=2)
    with exclusive_job(AUTO_ENABLE_JOB_TYPE, actor) as first:
        assert first
        with exclusive_job(AUTO_ENABLE_JOB_TYPE, actor) as second:
            assert second is None


def test_run_auto_enable_exclusive_busy_does_not_enqueue(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    actor = Actor(kind="tg", chat_id=1, user_id=2)
    with patch(
        "integrations.wallet_editor_auto_enable.exclusive_job"
    ) as lock:
        lock.return_value.__enter__.return_value = None
        lock.return_value.__exit__.return_value = False
        with patch(
            "integrations.wallet_editor_auto_enable.run_auto_enable"
        ) as run_fn:
            busy = run_auto_enable_exclusive(actor, manual=True)
    assert busy.skipped_reason == "busy"
    assert busy.verdict == VERDICT_BUSY
    assert busy.correlation_id
    run_fn.assert_not_called()


def test_auto_enable_job_is_registered():
    from core.job_runner import JOB_REGISTRY
    from core.lock_status import KNOWN_JOB_TYPES

    assert AUTO_ENABLE_JOB_TYPE in JOB_REGISTRY
    assert callable(JOB_REGISTRY[AUTO_ENABLE_JOB_TYPE])
    assert AUTO_ENABLE_JOB_TYPE in KNOWN_JOB_TYPES
