# Контракт допуска Telegram document ingest (TASK-33)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт **подготовлен к review**; runtime **не** менялся |
| **База** | `4b1f3676507a11ea4582ab59eb9846bcac2849a6` (`feat/task-2026-09-17-32-rules-publish-generation`) |
| **Обследованный SHA** | тот же `4b1f367…` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **Owner ingest** | `modules/antares/document_ingest.py`; mixed re-export `integrations/wallet_editor_tg.py` |

Этот PR — только обследование и контракт. Реализацию `put_nowait_if_open` / isolated ingest **не** делать здесь. TASK-30 и TASK-32 повторно не закрывать. Conversion bridge, внутренний Auto-Enable enqueue, schedules, drain, worker join, sender stop, mixed-stop — **не** этот этап.

---

## 1. Реальная цепочка (исходники `4b1f367…`)

Регистрация isolated: `modules/antares/handlers.py` `get_antares_handlers()` → `MessageHandler(filters.Document.ALL, handle_wallet_editor_document)`.

Mixed: `integrations/tg_commands.py` `get_handlers()` → тот же function object через `wallet_editor_tg` re-export.

Доступ ingest: `WALLET_EDITOR_ALLOWED_CHAT_IDS` + `resolve_operator_for_user`. **Не** `guard_or_deny` / command ACL.

`handle_wallet_editor_document` сегодня **не** смотрит `bound_admission()`. Isolated closed/sealed пропускают документ так же, как mixed.

### 1.1 Порядок callback (сейчас)

```text
нет message/document → return
chat allowlist deny → reply, return          (файла нет)
не .xlsx → reply, return
нет user_id / operator unmapped → reply, return
reply «📥 Файл получен»                     ← await, не Accepted
mkdir TMP_DIR; local_path = /tmp/wallet_editor/wallet_editor_<uuid>.xlsx
get_file + download_to_drive                ← await
detect_excel_routing(local_path)            ← pandas/Excel
  AMBIGUOUS → unlink best-effort → reply → return
  ADD_WALLET → add_add_wallet_task(...)
  EDIT_WALLET → add_edit_wallet_task(...)
  иначе (DISABLE) → add_task(...)
log; reply «📌 … очередь …»
except Exception → log; reply «❌ Ошибка при приёме файла»
```

«Файл получен» и download **до** постановки. Это **не** атомарный admit.

`CancelledError` (BaseException) текущим `except Exception` **не** ловится. Если cancel после создания `local_path` — файл может остаться.

### 1.2 Три маршрута: фактическая точка enqueue

Все три helper в `automation/worker.py` **одинаковы по механике**. `add_*_task` **не** одна атомарная операция.

| Маршрут | Routing | Task | Helper | Точка постановки |
|---------|---------|------|--------|------------------|
| disable | не ADD/EDIT (после non-ambiguous) | `WalletEditorTask` | `add_task` | `worker.queue.put(task)` |
| add_wallet | `ExcelRouting.ADD_WALLET` | `WalletEditorAddWalletTask` (`dry_run` из env) | `add_add_wallet_task` | `worker.queue.put(task)` |
| edit_wallet | `ExcelRouting.EDIT_WALLET` | `WalletEditorEditWalletTask` | `add_edit_wallet_task` | `worker.queue.put(task)` |

Общий helper:

```text
_ensure_profile_worker(task.operator_profile)
  with _registry_lock:
    hit → return existing
    иначе: Queue() (unbounded, maxsize=0)
           Thread(target=worker_loop, daemon=True).start()
           _profile_workers[profile] = worker
worker.queue.put(task)     ← фактическая постановка
return worker.queue.qsize()
```

Очередь создаётся в `_ProfileWorker.queue` (`field(default_factory=Queue)`). Старт worker — lazy на **первом** add данного профиля; `ensure_worker_started()` в scheduler только debug-лог.

`queue.Queue()` без maxsize: `put` **не** ждёт свободного слота. Это всё равно **отдельный шаг** после создания потока и после отпуска `_registry_lock`. Между start thread и `put` worker уже крутит `queue.get()`.

`put_nowait` в ingest **сейчас нет**. `queue.put_nowait` в `integrations/telegram_bot.py` — очередь **sender**, не WE worker.

`enqueue_auto_enable_batch` — тот же `worker.queue.put`, **не** Telegram document. Вне этого контракта.

`automation/tg_receiver.py` — legacy download/`add_task`; не путь Antares PTB Document.ALL. Не подключать в TASK-33 code.

### 1.3 После enqueue (слой D, не admit)

`worker_loop`: `get` → dispatch `_run_disable_task` / `_run_add_wallet_task` / `_run_edit_wallet_task` → `task_done`.

Cleanup входа: `delayed_cleanup(result_path, input_path)` в **daemon** thread через 30s, `os.remove` в `try/except`. Запускается **только** на успешном конце run (disable — после send/outbox; add/edit — после result send). Gate `manual_sync` None и exception в add/edit `return` **без** cleanup. Disable exception в loop шлёт текст, cleanup **нет**.

---

## 2. Допуск (будущий isolated code)

### 2.1 Правила

- mixed / unbound: текущий `add_*_task` и тексты **без изменений**.
- isolated bound/closed и sealed: **новая** задача не ставится.
- Ранняя проверка `bound_admission()` до download: может избежать `get_file`/Excel. **Не** заменяет атомарную проверку в момент enqueue (seal во время download всё ещё должен отказать).
- Проверка `state is OPEN` и фактическая постановка — **один** `WorkAdmission._lock`.
- Этот lock **не** держать через: `await`, download, Excel parsing, Telegram reply, `Thread.start`, блокирующий `Queue.put` (даже если unbounded put обычно мгновенный — в lock только `put_nowait`).

### 2.2 Минимальный API

Не оборачивать ingest в `submit_if_open(get_job_executor(), …)`: это другой executor, не WE queue.

Не менять `add_task` / `add_add_wallet_task` / `add_edit_wallet_task` для mixed.

Добавить на `WorkAdmission`:

```python
@dataclass(frozen=True)
class AdmissionQueued:
    queue_size: int

def put_nowait_if_open(self, queue, item) -> AdmissionQueued | AdmissionRejected:
    with self._lock:
        if self._state is not AdmissionState.OPEN:
            return AdmissionRejected(self._state)
        queue.put_nowait(item)
        return AdmissionQueued(queue_size=queue.qsize())
```

`AdmissionAccepted.future` **не** использовать для очереди (нет Future от `put`).

Публичный lookup очереди (тонкая обёртка над существующим `_ensure_profile_worker`), **вне** admission lock:

```python
def ensure_profile_queue(profile_key: str) -> Queue:
    return _ensure_profile_worker(profile_key).queue
```

Порядок блокировок: сначала `_registry_lock` (создание Queue + start thread), **отпустить**, затем `WorkAdmission._lock` + `put_nowait`. Не вкладывать admission lock в registry lock и наоборот.

Старт daemon thread **до** admit не есть Accepted. Если затем `put_nowait_if_open` отвергнут — допустим простой worker без этой задачи (как уже существующий lazy worker).

Isolated callback после routing:

1. optional early reject, если bound и не OPEN (без файла / с файлом — § 3);
2. `queue = ensure_profile_queue(profile)` вне admission lock;
3. `outcome = admission.put_nowait_if_open(queue, task)`;
4. Rejected → handler владеет файлом, best-effort unlink, `ADMISSION_CLOSED_REPLY`;
5. `AdmissionQueued` → Accepted; reply «📌 …» **после** lock; сбой reply не откатывает put.

Unbound: шаг 2–5 не через admission; как сейчас `add_*_task`.

---

## 3. Момент Accepted

**Accepted** = `queue.put_nowait(item)` **вернулся внутри** `put_nowait_if_open` при `state is OPEN` без исключения.

Не Accepted:

- allowlist / не-xlsx / operator deny;
- reply «Файл получен»;
- успешный download / routing;
- `ensure_profile_queue` / start thread;
- `AdmissionRejected`;
- исключение `put_nowait` → не Accepted (как сбой `executor.submit`).

Следствия:

- `seal()` после Accepted **не** снимает элемент с очереди и не отменяет worker.
- Ошибка Telegram-ответа после Accepted **не** отказ enqueue и **не** повод повторно `put`.
- Cancellation handler после Accepted **не** unlink файла, уже переданного в task.

---

## 4. Владение `local_path`

Путь: `TMP_DIR / f"wallet_editor_{uuid}{ALLOWED_EXTENSION}"` (`/tmp/wallet_editor`).

| Исход | Владелец | Удаление |
|-------|----------|----------|
| download + parsing, до enqueue | handler | нет, пока исход не завершён |
| AMBIGUOUS | handler | сейчас: `unlink(missing_ok=True)` + `except OSError: pass` |
| early closed/sealed **до** download | файла нет | — |
| closed/sealed **после** download, до Accepted | handler | isolated: тот же best-effort unlink, что AMBIGUOUS |
| ошибка enqueue (`put_nowait` исключение) | handler | isolated: best-effort unlink |
| cancellation **до** Accepted, `local_path` создан | handler | isolated: best-effort unlink (сейчас mixed не ловит `CancelledError`) |
| **после Accepted**, включая ошибку/cancel reply | **worker / task.file_path** | handler **не** удаляет |

Worker cleanup: `delayed_cleanup` — best-effort `os.remove`, warning при ошибке; delay 30s; daemon. **Не** обещать удаление при `OSError` или kill процесса. После Accepted handler **не** имеет права unlink «на всякий случай»: worker может уже читать файл.

Существующий пробел (не чинить в этом docs PR, зафиксировать): add/edit exception и disable gate-fail / disable exception **не** вызывают `delayed_cleanup` — файл может остаться после Accepted. Это поведение worker, не handler.

---

## 5. Совместимость

Сохранить:

- fail-closed allowlist и operator mapping;
- три Excel-маршрута `detect_excel_routing` (ADD / EDIT / DISABLE + AMBIGUOUS);
- поля task dataclasses и `dry_run` add-wallet;
- mixed identity re-export `wallet_editor_tg`;
- `MessageHandler(filters.Document.ALL, …)` в isolated и mixed.

Не менять бизнес-логику Wallet Editor (engine, Excel contracts, credentials, registry outbox).

---

## 6. Матрица будущих тестов (code PR)

Гонки: Event/barrier, **без** sleep. Реальные `WorkAdmission` и реальный `Queue.put_nowait` (можно sandbox `TMP_DIR`, stub `get_file` / download / `detect_excel_routing` / `resolve_operator_for_user`). Не запускать браузер, живой Telegram, живой `worker_loop` как сервис.

| # | Сценарий | Ожидание |
|---|----------|----------|
| I1–I3 | три маршрута isolated OPEN | один `put_nowait` на очередь профиля; тип task как сейчас; reset/admission lock не вокруг download |
| I4 | bound/closed и sealed до download | нет enqueue; нет download; `ADMISSION_CLOSED_REPLY` |
| I5 | allowlist / operator deny | прежние тексты; нет admit/enqueue |
| I6 | seal во время download (barrier до `put_nowait_if_open`) | Rejected; файл unlink handler; нет элемента в queue |
| I7 | seal после routing, до enqueue | то же |
| I8 | enqueue раньше seal (barrier: put внутри lock, затем seal) | **одна** принятая задача остаётся в queue |
| I9 | `put_nowait` бросает | не Accepted; unlink handler; нет повторного put из reply |
| I10 | cancel до Accepted (после появления local_path) | нет enqueue; unlink handler |
| I11 | cancel после Accepted | элемент в queue; handler не unlink |
| I12 | reply после Accepted бросает | enqueue сохраняется; нет второго put |
| I13 | ownership по I4–I12 | assert exists/unlinked согласно § 4 |
| M1 | mixed/unbound baseline | три маршрута через `add_*_task`; closed admission не участвует |

Не считать `test_repro_*` / harness dump доказательством admit.

---

## 7. Границы этапа

Этот контракт закрывает **только** Telegram document ingest isolated Antares.

Не закрыты и не входят в следующий code:

- conversion bridge `add_task`;
- `enqueue_auto_enable_batch`;
- schedules / `dispatch_job_background`;
- drain очереди, worker join, sender stop, mixed-stop;
- `automation/tg_receiver.py`;
- смена C4 / rules publish;
- merge/deploy/live polling.

### 7.1 Минимальный следующий code

1. `AdmissionQueued` + `WorkAdmission.put_nowait_if_open`.
2. `ensure_profile_queue` без смены семантики mixed `add_*_task`.
3. Isolated ветка в `handle_wallet_editor_document` (early peek + атомарный put); unbound без изменений.
4. Тесты § 6. Без live Telegram.

Успех этого code **не** глобальный запрет новой работы в процессе.

### 7.2 UNKNOWN / не обещать

- удаление файла при `OSError`, kill, crash worker после Accepted;
- наблюдаемая разница `put` vs `put_nowait` на unbounded queue (для admit всё равно только `put_nowait` в lock);
- Windows-путь `/tmp/wallet_editor` vs `%TEMP%` (сейчас константа);
- отмена PTB `concurrent_updates` точно в окне между put и return callback;
- нужен ли отдельный public API вместо вызова `_ensure_profile_worker` — code может экспортировать тонкую функцию, не меняя mixed helpers.
