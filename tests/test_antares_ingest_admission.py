"""TASK-34 isolated Telegram document ingest admission (I1–I18, M1)."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from queue import Queue
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation.edit_wallet_contract import ExcelRouting
from automation.runtime import (
    MSG_OPERATOR_UNMAPPED,
    WalletEditorAddWalletTask,
    WalletEditorEditWalletTask,
    WalletEditorTask,
)
from modules.antares import document_ingest as ingest
from modules.antares.work_admission import (
    ADMISSION_CLOSED_REPLY,
    AdmissionQueued,
    AdmissionRejected,
    AdmissionState,
    WorkAdmission,
    bind_antares_admission,
    reset_antares_admission_for_tests,
)

DEFAULT_USER_ID = 123456789
DEFAULT_PROFILE = "DENIS"
CHAT_ID = -5102627011


@pytest.fixture(autouse=True)
def _sandbox_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "TMP_DIR", tmp_path / "wallet_editor")


@pytest.fixture(autouse=True)
def _reset_admission():
    reset_antares_admission_for_tests()
    yield
    reset_antares_admission_for_tests()


@pytest.fixture
def no_business_worker(monkeypatch):
    """Stub Thread only on automation.worker; restore the original registry dict."""
    import threading as std_threading
    import automation.worker as worker_mod

    starts: list = []

    class _Thread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None, **_k):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = daemon
            self.name = name

        def start(self) -> None:
            starts.append(self)
            assert self.target is worker_mod.worker_loop

    class _ThreadingNS:
        Thread = _Thread

        def __getattr__(self, name):
            return getattr(std_threading, name)

    orig_workers = worker_mod._profile_workers
    with worker_mod._registry_lock:
        worker_mod._profile_workers = {}
    monkeypatch.setattr(worker_mod, "threading", _ThreadingNS())
    yield starts
    with worker_mod._registry_lock:
        worker_mod._profile_workers = orig_workers


def _operator_env() -> dict[str, str]:
    return {
        "WALLET_EDITOR_OPERATOR_MAP": f"{DEFAULT_USER_ID}:{DEFAULT_PROFILE}",
        f"WALLET_EDITOR_OPERATOR_{DEFAULT_PROFILE}_LOGIN": "denis-login",
        f"WALLET_EDITOR_OPERATOR_{DEFAULT_PROFILE}_PASSWORD": "denis-pass",
        "WALLET_EDITOR_ALLOWED_CHAT_IDS": str(CHAT_ID),
    }


def _make_update(*, file_name: str = "batch.xlsx") -> MagicMock:
    update = MagicMock()
    texts: list[str] = []

    async def _reply(text: str, **_k):
        texts.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)
    update._texts = texts
    update.effective_chat.id = CHAT_ID
    update.effective_user = MagicMock()
    update.effective_user.id = DEFAULT_USER_ID
    document = MagicMock(spec=["file_name", "file_id"])
    document.file_name = file_name
    document.file_id = "file-123"
    update.message.document = document
    update.message.from_user = MagicMock()
    update.message.from_user.id = DEFAULT_USER_ID
    return update


def _download_writes(tg_file: AsyncMock, body: bytes = b"xlsx") -> None:
    async def _dl(*, custom_path=None, **_k):
        Path(custom_path).parent.mkdir(parents=True, exist_ok=True)
        Path(custom_path).write_bytes(body)

    tg_file.download_to_drive = AsyncMock(side_effect=_dl)


def _context_ok(body: bytes = b"xlsx") -> MagicMock:
    context = MagicMock()
    tg_file = AsyncMock()
    context.bot.get_file = AsyncMock(return_value=tg_file)
    _download_writes(tg_file, body)
    return context


def _open_bound() -> WorkAdmission:
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    return admission


def _run(update, context):
    asyncio.run(ingest.handle_wallet_editor_document(update, context))


async def _await_other_tasks() -> None:
    current = asyncio.current_task()
    others = [task for task in asyncio.all_tasks() if task is not current]
    if others:
        await asyncio.wait(others)


class _ObservingLock:
    """Wrap WorkAdmission._lock; waiter_blocked is set only when the waiter sees it held."""

    def __init__(self, inner, *, holder, waiter, holder_acquired, waiter_blocked):
        self._inner = inner
        self._holder = holder
        self._waiter = waiter
        self.holder_acquired = holder_acquired
        self.waiter_blocked = waiter_blocked

    def acquire(self, blocking=True, timeout=-1):
        me = threading.current_thread()
        if me is self._waiter and self._inner.locked():
            self.waiter_blocked.set()
        if timeout is None or timeout < 0:
            acquired = self._inner.acquire(blocking)
        else:
            acquired = self._inner.acquire(blocking, timeout)
        if acquired and me is self._holder:
            self.holder_acquired.set()
        return acquired

    def release(self):
        if threading.current_thread() is self._holder:
            assert self.waiter_blocked.wait(timeout=5)
        self._inner.release()

    def locked(self):
        return self._inner.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def _patch_tmp_unlink_oserror(monkeypatch) -> list[Path]:
    hits: list[Path] = []
    orig = Path.unlink

    def _unlink(self, *args, **kwargs):
        tmp = ingest.TMP_DIR
        try:
            under_tmp = self.resolve().is_relative_to(tmp.resolve())
        except (OSError, ValueError):
            under_tmp = False
        if under_tmp:
            hits.append(self)
            raise OSError("resource busy")
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _unlink)
    return hits


def test_put_nowait_if_open_admits_under_lock() -> None:
    admission = WorkAdmission()
    admission._bind_instance()
    admission.open()
    q: Queue = Queue()
    outcome = admission.put_nowait_if_open(q, "task")
    assert isinstance(outcome, AdmissionQueued)
    assert q.get_nowait() == "task"


def test_put_nowait_if_open_rejects_when_sealed() -> None:
    admission = WorkAdmission()
    admission._bind_instance()
    admission.open()
    admission.seal()
    q: Queue = Queue()
    outcome = admission.put_nowait_if_open(q, "task")
    assert isinstance(outcome, AdmissionRejected)
    assert outcome.state is AdmissionState.SEALED
    assert q.empty()


def test_put_wins_race_seal_waits_on_lock() -> None:
    """I8 race: put holds admission lock during real put_nowait; seal waits on that lock."""
    admission = WorkAdmission()
    admission._bind_instance()
    admission.open()
    holder_acquired = threading.Event()
    waiter_blocked = threading.Event()
    in_put = threading.Event()
    seal_finished = threading.Event()
    outcome: dict[str, object] = {}
    observed = _ObservingLock(
        admission._lock,
        holder=None,
        waiter=None,
        holder_acquired=holder_acquired,
        waiter_blocked=waiter_blocked,
    )
    admission._lock = observed

    class _Q(Queue):
        def put_nowait(self, item):
            assert observed.locked()
            in_put.set()
            assert waiter_blocked.wait(timeout=5)
            super().put_nowait(item)

    q: Queue = _Q()

    def _put() -> None:
        outcome["v"] = admission.put_nowait_if_open(q, "task")

    def _seal() -> None:
        assert in_put.wait(timeout=5)
        admission.seal()
        seal_finished.set()

    put_thread = threading.Thread(target=_put, name="admit-put")
    seal_thread = threading.Thread(target=_seal, name="admit-seal")
    observed._holder = put_thread
    observed._waiter = seal_thread

    put_thread.start()
    seal_thread.start()
    put_thread.join(timeout=5)
    seal_thread.join(timeout=5)
    assert not put_thread.is_alive()
    assert not seal_thread.is_alive()
    assert holder_acquired.is_set()
    assert waiter_blocked.is_set()
    assert in_put.is_set()
    assert seal_finished.is_set()
    assert isinstance(outcome["v"], AdmissionQueued)
    assert q.get_nowait() == "task"
    assert q.empty()
    assert admission.state is AdmissionState.SEALED


def test_seal_wins_race_put_not_called() -> None:
    """Seal holds the lock; put is observed waiting; queue.put_nowait never runs."""
    admission = WorkAdmission()
    admission._bind_instance()
    admission.open()
    holder_acquired = threading.Event()
    waiter_blocked = threading.Event()
    outcome: dict[str, object] = {}
    put_calls: list = []
    observed = _ObservingLock(
        admission._lock,
        holder=None,
        waiter=None,
        holder_acquired=holder_acquired,
        waiter_blocked=waiter_blocked,
    )
    admission._lock = observed

    class _Q(Queue):
        def put_nowait(self, item):
            put_calls.append(item)
            raise AssertionError("seal won; put_nowait must not run")

    q: Queue = _Q()

    def _seal() -> None:
        admission.seal()

    def _put() -> None:
        assert holder_acquired.wait(timeout=5)
        outcome["v"] = admission.put_nowait_if_open(q, "task")

    put_thread = threading.Thread(target=_put, name="admit-put")
    seal_thread = threading.Thread(target=_seal, name="admit-seal")
    observed._holder = seal_thread
    observed._waiter = put_thread

    seal_thread.start()
    put_thread.start()
    seal_thread.join(timeout=5)
    put_thread.join(timeout=5)
    assert not seal_thread.is_alive()
    assert not put_thread.is_alive()
    assert holder_acquired.is_set()
    assert waiter_blocked.is_set()
    assert put_calls == []
    assert isinstance(outcome["v"], AdmissionRejected)
    assert outcome["v"].state is AdmissionState.SEALED
    assert q.empty()
    assert admission.state is AdmissionState.SEALED


def test_i1_i2_i3_three_routes_open(no_business_worker, monkeypatch) -> None:
    cases = (
        (ExcelRouting.DISABLE, WalletEditorTask, "disable"),
        (ExcelRouting.ADD_WALLET, WalletEditorAddWalletTask, "add"),
        (ExcelRouting.EDIT_WALLET, WalletEditorEditWalletTask, "edit"),
    )
    import automation.worker as worker_mod

    for routing, cls, kind in cases:
        reset_antares_admission_for_tests()
        with worker_mod._registry_lock:
            worker_mod._profile_workers.clear()
        _open_bound()
        monkeypatch.setattr(
            "automation.edit_wallet_contract.detect_excel_routing",
            lambda *a, **k: (routing, None),
        )
        monkeypatch.setenv("WALLET_EDITOR_ADD_WALLET_DRY_RUN", "1")
        update = _make_update()
        context = _context_ok()
        with patch.dict("os.environ", _operator_env(), clear=False):
            for key, value in _operator_env().items():
                monkeypatch.setenv(key, value)
            _run(update, context)
        worker = worker_mod._profile_workers[DEFAULT_PROFILE]
        task = worker.queue.get_nowait()
        assert isinstance(task, cls)
        assert task.operator_profile == DEFAULT_PROFILE
        assert Path(task.file_path).is_file()
        assert any(t.startswith("📌") for t in update._texts)
        if kind == "add":
            assert task.dry_run is True
            assert task.user_id == DEFAULT_USER_ID
        if kind == "disable":
            assert task.telegram_user_id == DEFAULT_USER_ID
        assert worker.queue.empty()


def test_i4_closed_and_sealed_skip_download(no_business_worker) -> None:
    import automation.worker as worker_mod

    closed = WorkAdmission()
    bind_antares_admission(closed)
    update = _make_update()
    context = _context_ok()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, context)
    context.bot.get_file.assert_not_called()
    assert worker_mod._profile_workers == {}
    assert update._texts == [ADMISSION_CLOSED_REPLY]

    reset_antares_admission_for_tests()
    sealed = WorkAdmission()
    bind_antares_admission(sealed)
    sealed.open()
    sealed.seal()
    update2 = _make_update()
    context2 = _context_ok()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update2, context2)
    context2.bot.get_file.assert_not_called()
    assert update2._texts == [ADMISSION_CLOSED_REPLY]


def test_i5_allowlist_and_operator_deny(no_business_worker) -> None:
    _open_bound()
    update = _make_update()
    context = _context_ok()
    with patch.dict(
        "os.environ",
        {**_operator_env(), "WALLET_EDITOR_ALLOWED_CHAT_IDS": "1"},
        clear=True,
    ):
        _run(update, context)
    context.bot.get_file.assert_not_called()
    assert update._texts == ["⛔ Чат не разрешён для WalletEditor."]

    update2 = _make_update()
    update2.effective_user.id = 999
    update2.message.from_user.id = 999
    context2 = _context_ok()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update2, context2)
    context2.bot.get_file.assert_not_called()
    assert update2._texts == [MSG_OPERATOR_UNMAPPED]


def test_i6_seal_during_download(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    admission = _open_bound()
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    context = MagicMock()
    tg_file = AsyncMock()

    async def _dl(*, custom_path=None, **_k):
        Path(custom_path).write_bytes(b"xlsx")
        admission.seal()

    tg_file.download_to_drive = AsyncMock(side_effect=_dl)
    context.bot.get_file = AsyncMock(return_value=tg_file)
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, context)
    assert ADMISSION_CLOSED_REPLY in update._texts
    assert worker_mod._profile_workers[DEFAULT_PROFILE].queue.empty()
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []


def test_i7_seal_after_routing_before_enqueue(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    admission = _open_bound()

    def _route(*_a, **_k):
        admission.seal()
        return ExcelRouting.DISABLE, None

    monkeypatch.setattr("automation.edit_wallet_contract.detect_excel_routing", _route)
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    assert ADMISSION_CLOSED_REPLY in update._texts
    assert worker_mod._profile_workers[DEFAULT_PROFILE].queue.empty()
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []


def test_i8_enqueue_then_seal_keeps_one(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    admission = _open_bound()
    orig = admission.put_nowait_if_open

    def _wrap(queue, item):
        out = orig(queue, item)
        if isinstance(out, AdmissionQueued):
            admission.seal()
        return out

    monkeypatch.setattr(admission, "put_nowait_if_open", _wrap)
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    q = worker_mod._profile_workers[DEFAULT_PROFILE].queue
    assert q.qsize() == 1
    assert any(t.startswith("📌") for t in update._texts)


def test_i9_put_fails_before_item(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()

    class FailQ(Queue):
        def put_nowait(self, item):
            raise RuntimeError("put denied")

    fail = FailQ()
    monkeypatch.setattr(worker_mod, "ensure_profile_queue", lambda *_a, **_k: fail)
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    assert fail.empty()
    assert any("Ошибка при приёме файла" in t for t in update._texts)
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []


def test_i10_cancel_before_accept_unlinks(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    entered = asyncio.Event()
    context = MagicMock()
    tg_file = AsyncMock()

    async def _dl(*, custom_path=None, **_k):
        Path(custom_path).write_bytes(b"partial")
        entered.set()
        await asyncio.Event().wait()

    tg_file.download_to_drive = AsyncMock(side_effect=_dl)
    context.bot.get_file = AsyncMock(return_value=tg_file)
    update = _make_update()

    async def _main() -> None:
        with patch.dict("os.environ", _operator_env(), clear=True):
            handler = asyncio.create_task(
                ingest.handle_wallet_editor_document(update, context)
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            handler.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handler
            await _await_other_tasks()

    asyncio.run(_main())
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []
    assert worker_mod._profile_workers == {}


def test_i11_cancel_first_await_after_accept(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    update = _make_update()
    entered = asyncio.Event()

    async def _reply(text: str, **_k):
        update._texts.append(text)
        if text.startswith("📌"):
            entered.set()
            await asyncio.Event().wait()

    update.message.reply_text = AsyncMock(side_effect=_reply)

    async def _main() -> None:
        with patch.dict("os.environ", _operator_env(), clear=True):
            handler = asyncio.create_task(
                ingest.handle_wallet_editor_document(update, _context_ok())
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            handler.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handler
            await _await_other_tasks()

    asyncio.run(_main())
    q = worker_mod._profile_workers[DEFAULT_PROFILE].queue
    task = q.get_nowait()
    assert Path(task.file_path).is_file()
    assert q.empty()


def test_i12_confirmation_error_keeps_enqueue(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    update = _make_update()

    async def _reply(text: str, **_k):
        update._texts.append(text)
        if text.startswith("📌"):
            raise RuntimeError("telegram down")

    update.message.reply_text = AsyncMock(side_effect=_reply)
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    q = worker_mod._profile_workers[DEFAULT_PROFILE].queue
    task = q.get_nowait()
    assert Path(task.file_path).is_file()
    assert all("постановка не состоялась" not in t.lower() for t in update._texts)
    assert all("Ошибка при приёме файла" not in t for t in update._texts)


def test_i13_partial_download(no_business_worker) -> None:
    _open_bound()
    context = MagicMock()
    tg_file = AsyncMock()

    async def _dl(*, custom_path=None, **_k):
        Path(custom_path).write_bytes(b"partial")
        raise RuntimeError("download cut")

    tg_file.download_to_drive = AsyncMock(side_effect=_dl)
    context.bot.get_file = AsyncMock(return_value=tg_file)
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, context)
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []
    assert any("Ошибка при приёме файла" in t for t in update._texts)


def test_i14_ambiguous_routing(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.AMBIGUOUS, "bad columns"),
    )
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []
    assert worker_mod._profile_workers == {}
    assert any("Не удалось определить тип Excel" in t for t in update._texts)


def test_i14_detect_excel_routing_raises(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("corrupt workbook")),
    )
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []
    assert worker_mod._profile_workers == {}
    assert any("Ошибка при приёме файла: corrupt workbook" in t for t in update._texts)


def test_i14_task_construction_raises(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )

    def _boom(*_a, **_k):
        raise TypeError("task fields invalid")

    monkeypatch.setattr(ingest, "_route_task", _boom)
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []
    assert worker_mod._profile_workers == {}
    assert any("Ошибка при приёме файла: task fields invalid" in t for t in update._texts)


def test_i15_ensure_failure(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    monkeypatch.setattr(
        worker_mod,
        "ensure_profile_queue",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("thread start failed")),
    )
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    leftover = list(ingest.TMP_DIR.glob("*.xlsx")) if ingest.TMP_DIR.exists() else []
    assert leftover == []
    assert any("Ошибка при приёме файла" in t for t in update._texts)


def test_i16_consumer_took_task_qsize_may_be_zero(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    orig = WorkAdmission.put_nowait_if_open

    def _wrap(self, queue, item):
        out = orig(self, queue, item)
        if isinstance(out, AdmissionQueued):
            taken = queue.get_nowait()
            queue._held = taken
        return out

    monkeypatch.setattr(WorkAdmission, "put_nowait_if_open", _wrap)
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    q = worker_mod._profile_workers[DEFAULT_PROFILE].queue
    assert q.empty()
    assert Path(q._held.file_path).is_file()
    assert any(t.startswith("📌") for t in update._texts)


def test_i17_unlink_oserror_keeps_download_error(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    hits = _patch_tmp_unlink_oserror(monkeypatch)
    context = MagicMock()
    tg_file = AsyncMock()

    async def _dl(*, custom_path=None, **_k):
        Path(custom_path).write_bytes(b"partial")
        raise RuntimeError("download cut")

    tg_file.download_to_drive = AsyncMock(side_effect=_dl)
    context.bot.get_file = AsyncMock(return_value=tg_file)
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, context)
    assert hits
    assert worker_mod._profile_workers == {}
    assert any("Ошибка при приёме файла: download cut" in t for t in update._texts)
    assert all("resource busy" not in t for t in update._texts)


def test_i17_unlink_oserror_keeps_cancellederror(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()
    hits = _patch_tmp_unlink_oserror(monkeypatch)
    entered = asyncio.Event()
    context = MagicMock()
    tg_file = AsyncMock()

    async def _dl(*, custom_path=None, **_k):
        Path(custom_path).write_bytes(b"partial")
        entered.set()
        await asyncio.Event().wait()

    tg_file.download_to_drive = AsyncMock(side_effect=_dl)
    context.bot.get_file = AsyncMock(return_value=tg_file)
    update = _make_update()

    async def _main() -> None:
        with patch.dict("os.environ", _operator_env(), clear=True):
            handler = asyncio.create_task(
                ingest.handle_wallet_editor_document(update, context)
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            handler.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handler
            await _await_other_tasks()

    asyncio.run(_main())
    assert hits
    assert worker_mod._profile_workers == {}


def test_i17_unlink_helper_error_does_not_hide_cancel(no_business_worker, monkeypatch) -> None:
    _open_bound()

    def _boom(path):
        raise RuntimeError("unlink boom")

    monkeypatch.setattr(ingest, "_best_effort_unlink", _boom)
    entered = asyncio.Event()
    context = MagicMock()
    tg_file = AsyncMock()

    async def _dl(*, custom_path=None, **_k):
        Path(custom_path).write_bytes(b"partial")
        entered.set()
        await asyncio.Event().wait()

    tg_file.download_to_drive = AsyncMock(side_effect=_dl)
    context.bot.get_file = AsyncMock(return_value=tg_file)
    update = _make_update()

    async def _main() -> None:
        with patch.dict("os.environ", _operator_env(), clear=True):
            handler = asyncio.create_task(
                ingest.handle_wallet_editor_document(update, context)
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            handler.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handler
            await _await_other_tasks()

    asyncio.run(_main())


def test_i18_qsize_error_after_put_still_accepted(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    _open_bound()

    class Q(Queue):
        def qsize(self):
            raise RuntimeError("qsize boom")

    q = Q()
    monkeypatch.setattr(worker_mod, "ensure_profile_queue", lambda *_a, **_k: q)
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        _run(update, _context_ok())
    task = q.get_nowait()
    assert Path(task.file_path).is_file()
    assert any(t.startswith("📌") for t in update._texts)
    assert all("Ошибка при приёме файла" not in t for t in update._texts)


def test_m1_mixed_disable_uses_add_task(monkeypatch) -> None:
    """DISABLE only. ADD/EDIT mixed: document_ingest ADD/EDIT field tests + tg_integration ADD."""
    monkeypatch.setattr(
        "automation.edit_wallet_contract.detect_excel_routing",
        lambda *a, **k: (ExcelRouting.DISABLE, None),
    )
    update = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        with patch("automation.worker.add_task", return_value=3) as add_task:
            with patch("automation.worker.add_add_wallet_task") as add_add:
                with patch("automation.worker.add_edit_wallet_task") as add_edit:
                    _run(update, _context_ok())
    add_task.assert_called_once()
    add_add.assert_not_called()
    add_edit.assert_not_called()
    assert isinstance(add_task.call_args.args[0], WalletEditorTask)
    closed = WorkAdmission()
    bind_antares_admission(closed)
    reset_antares_admission_for_tests()
    update2 = _make_update()
    with patch.dict("os.environ", _operator_env(), clear=True):
        with patch("automation.worker.add_task", return_value=1) as add_task2:
            _run(update2, _context_ok())
    add_task2.assert_called_once()


def test_worker_fixture_does_not_patch_stdlib_threading(no_business_worker) -> None:
    import threading as std_threading
    import automation.worker as worker_mod

    assert worker_mod.threading.Thread is not std_threading.Thread


def test_ensure_profile_queue_starts_thread_without_worker_loop(no_business_worker) -> None:
    import automation.worker as worker_mod

    q = worker_mod.ensure_profile_queue("DENIS")
    assert no_business_worker[0].target is worker_mod.worker_loop
    assert no_business_worker[0].name == "wallet-editor-worker-DENIS"
    q.put_nowait("x")
    assert q.get_nowait() == "x"
    assert worker_mod.ensure_profile_queue("DENIS") is q
    assert len(no_business_worker) == 1


def test_ensure_profile_queue_thread_start_error_does_not_register(no_business_worker, monkeypatch) -> None:
    import automation.worker as worker_mod

    class _FailThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None, **_k):
            self.target = target

        def start(self) -> None:
            raise RuntimeError("cannot start thread")

    monkeypatch.setattr(worker_mod.threading, "Thread", _FailThread)
    with pytest.raises(RuntimeError, match="cannot start thread"):
        worker_mod.ensure_profile_queue("DENIS")
    assert worker_mod._profile_workers == {}
