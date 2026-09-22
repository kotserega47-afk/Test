# Контракт допуска Telegram document ingest (TASK-33)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **принят**; code TASK-34 **реализован** (GPT review `f76f9c96f46b2489ecb08a38c542de421b609ba6`); **не выпущен** |
| **База** | `4b1f3676507a11ea4582ab59eb9846bcac2849a6` (`feat/task-2026-09-17-32-rules-publish-generation`) |
| **Обследованный SHA** | `4b1f367…`; уточнение контракта от review HEAD `1ebc024161dcb4f6876130f7a433aa2920ed55c8` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **Owner ingest** | `modules/antares/document_ingest.py`; mixed re-export `integrations/wallet_editor_tg.py` |

Контракт **принят** (GPT docs `4682e399…`). Code TASK-34 **реализован и закрыт** на `f76f9c9…` (GPT код/diff/тесты, pytest GPT не запускал; Cursor 28/95). **Не выпущен**. TASK-33 повторно не закрывать. Schedules — TASK-35. Conversion bridge, внутренний Auto-Enable enqueue, drain, worker join, sender stop, mixed-stop — **не** ingest.

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

`CancelledError` (BaseException) текущим `except Exception` **не** ловится. Если cancel после создания `local_path` — файл может остаться. Isolated code обязан закрыть этот исход (§ 4); mixed этим этапом **не** менять.

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

Фактический результат задания после Accepted — ответственность worker, не handler.

---

## 2. Допуск (будущий isolated code)

### 2.1 Правила

- mixed / unbound: текущий `add_*_task` и тексты **без изменений**.
- isolated bound/closed и sealed: **новая** задача не ставится.
- **Обязательная** ранняя проверка после allowlist / `.xlsx` / operator и **до** «Файл получен», `get_file`/download и создания worker: если bound и `state is not OPEN` → `ADMISSION_CLOSED_REPLY`, без файла и без `ensure_profile_queue`. Это не optional.
- Ранний OPEN **не** резервирует право постановки. Атомарная проверка `state is OPEN` + `put_nowait` обязательна; seal во время download/routing/ensure всё ещё отказывает.
- Проверка OPEN и фактическая постановка — **один** `WorkAdmission._lock`.
- Этот lock **не** держать через: `await`, download, Excel parsing, Telegram reply, `Thread.start`, блокирующий `Queue.put` (в lock только `put_nowait`).

### 2.2 Минимальный API

Не оборачивать ingest в `submit_if_open(get_job_executor(), …)`: это другой executor, не WE queue.

Не менять `add_task` / `add_add_wallet_task` / `add_edit_wallet_task` для mixed.

Добавить на `WorkAdmission`:

```python
@dataclass(frozen=True)
class AdmissionQueued:
    """Successful queue admit. Not a Future. Diagnostic size is not this object."""

def put_nowait_if_open(self, queue, item) -> AdmissionQueued | AdmissionRejected:
    with self._lock:
        if self._state is not AdmissionState.OPEN:
            return AdmissionRejected(self._state)
        queue.put_nowait(item)
        return AdmissionQueued()
# lock released
# handler: if AdmissionQueued → handed_off = True  (no await)
# then diagnostic queue.qsize() / log / confirmation reply
```

Успешный `put_nowait` **есть** Accepted. `qsize` **не** внутри admission lock и **не** определяет Accepted. Ошибка `qsize` после `AdmissionQueued` не откатывает постановку. `qsize` не позиция задачи и не доказательство, что worker ещё не забрал элемент (размер может быть 0).

`AdmissionAccepted.future` **не** использовать для очереди.

Публичный lookup очереди (тонкая обёртка над `_ensure_profile_worker`), **вне** admission lock:

```python
def ensure_profile_queue(profile_key: str) -> Queue:
    return _ensure_profile_worker(profile_key).queue
```

Порядок блокировок: сначала `_registry_lock` (создание Queue + start thread), **отпустить**, затем `WorkAdmission._lock` + `put_nowait`. Не вкладывать locks.

Старт daemon thread **до** окончательного admit не есть Accepted. Если `put_nowait_if_open` отвергнут после `ensure_profile_queue` — допустимый ресурсный эффект: простой worker без этой задачи. Это **не** drain/join и **не** гарантия, что после `seal` новые потоки больше не появятся (другой путь всё ещё может вызвать ensure).

Isolated callback:

1. allowlist / `.xlsx` / operator — как сейчас;
2. **обязательный** early reject, если bound и не OPEN (файла нет, worker не создаём);
3. «Файл получен» / download / routing / построение task;
4. `queue = ensure_profile_queue(profile)` вне admission lock;
5. `outcome = admission.put_nowait_if_open(queue, task)` → при успехе `AdmissionQueued`;
6. Rejected или исключение **до** помещения элемента → handler владеет файлом, best-effort unlink, ответ отказа / ошибки постановки;
7. `AdmissionQueued` → handler **сразу** фиксирует передачу владения (без `await`); затем диагностический `qsize` / log / reply.

Unbound: шаги 2 и 5–7 не через admission; как сейчас `add_*_task`.

---

## 3. Момент Accepted и фиксация владения

**Accepted** = возврат `AdmissionQueued` из `put_nowait_if_open` после успешного `queue.put_nowait` под lock при OPEN.

Порядок после постановки (§2.2): `put_nowait` → `AdmissionQueued` → handler фиксирует передачу владения → диагностический `qsize` / log / reply. Между `AdmissionQueued` и фиксацией владения **нет** `await`.

Не Accepted:

- allowlist / не-xlsx / operator deny;
- ранний closed/sealed;
- reply «Файл получен»;
- download / routing / построение task;
- `ensure_profile_queue` / `Thread.start`;
- `AdmissionRejected`;
- исключение `put_nowait` **до** помещения элемента.

### 3.1 После Accepted (ошибка ответа ≠ ошибка постановки)

- файл **не** удалять;
- enqueue **не** повторять;
- оператору **не** сообщать, что постановка не состоялась;
- сбой подтверждения (Telegram reply) логировать отдельно (`exception` на confirmation, не ingest-failed);
- `CancelledError` на **первом await после Accepted** (обычно confirmation reply): enqueue один, файл сохранён, cancel пробрасывается; это покрывается тестами этого этапа, не UNKNOWN;
- фактический результат задания — worker.

`seal()` после Accepted не снимает элемент и не отменяет worker.

Ошибка диагностического `qsize` / logging после успешного put **не** превращает Accepted в «enqueue failed» и **не** даёт unlink.

---

## 4. Владение `local_path`

Путь: `TMP_DIR / f"wallet_editor_{uuid}{ALLOWED_EXTENSION}"` (`/tmp/wallet_editor`).

| Исход | Владелец | Isolated cleanup |
|-------|----------|------------------|
| download + parsing, до enqueue | handler | нет, пока исход не завершён |
| частичный download / ошибка download | handler | best-effort unlink |
| ошибка routing/parsing / построения task (включая AMBIGUOUS) | handler | best-effort unlink |
| ошибка `ensure_profile_queue` / `Thread.start` | handler | best-effort unlink |
| ранний closed/sealed **до** download | файла нет | — |
| отказ admission после файла, до put | handler | best-effort unlink |
| ошибка `put_nowait` **до** помещения элемента | handler | best-effort unlink |
| `CancelledError` **до** Accepted | handler | best-effort unlink, затем **проброс** cancel |
| **после Accepted** (в т.ч. cancel/ошибка confirmation) | **worker / task.file_path** | handler **не** удаляет |

Best-effort unlink: `unlink(missing_ok=True)` + `except OSError`. Ошибка unlink **не** затирает исходную ошибку и **не** подменяет `CancelledError` (сначала cleanup, затем re-raise исходного / cancel).

Worker `delayed_cleanup` — best-effort `os.remove`, warning при ошибке; delay 30s; daemon. **Не** обещать удаление при `OSError` или kill процесса.

Существующий пробел worker (не этот code): add/edit exception и disable gate-fail / disable exception не вызывают `delayed_cleanup`. После Accepted это не обязанность handler.

Mixed этим этапом **не** менять (включая сегодняшнее отсутствие ловли `CancelledError`).

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

Гонки: Event/barrier, **без** sleep. Реальные `WorkAdmission` и реальный `Queue.put_nowait` (sandbox `TMP_DIR`; stub `get_file` / download / routing / operator). Без браузера, сети, живого Telegram и **без бизнес-`worker_loop`**. Для ошибки put инъекция падает **до** помещения элемента в очередь.

| # | Сценарий | Ожидание |
|---|----------|----------|
| I1–I3 | три маршрута isolated OPEN | один `put_nowait`; тип task как сейчас; admission lock не вокруг download |
| I4 | bound/closed и sealed после operator, до «Файл получен» | нет download, нет worker ensure, нет enqueue; `ADMISSION_CLOSED_REPLY` |
| I5 | allowlist / operator deny | прежние тексты; нет admit/enqueue |
| I6 | seal во время download (barrier до `put_nowait_if_open`) | Rejected; unlink handler; нет элемента |
| I7 | seal после routing, до enqueue | то же |
| I8 | enqueue раньше seal (barrier: put внутри lock, затем seal) | **одна** принятая задача |
| I9 | инъекция ошибки put **до** помещения элемента | не Accepted; unlink handler; нет повторного put |
| I10 | cancel до Accepted (после появления local_path) | нет enqueue; unlink; `CancelledError` проброшен |
| I11 | отмена на **первом await после Accepted** | файл сохранён; enqueue ровно один; handler не unlink |
| I12 | ошибка confirmation reply после Accepted | те же гарантии, что I11; лог отдельно; оператору не «постановка не состоялась» |
| I13 | частичный download | нет enqueue; файл удалён best-effort |
| I14 | parsing / routing / построение task failure | нет enqueue; файл удалён best-effort |
| I15 | ошибка `ensure_profile_queue` / `Thread.start` | нет enqueue; файл удалён best-effort |
| I16 | consumer уже забрал задачу | `qsize` может быть 0; Accepted сохраняется; файл не unlink |
| I17 | ошибка unlink на исходе до Accepted | исходная ошибка / `CancelledError` не скрыты |
| I18 | ошибка `qsize` после успешного put | Accepted; нет unlink; не «enqueue failed» |
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

Lazy worker **может** быть создан до окончательного отказа admit. Это допустимый ресурсный эффект данного среза. Это **не** доказанный drain/join и **не** гарантия отсутствия новых потоков после `seal`.

### 7.1 Минимальный следующий code

1. `AdmissionQueued` + `WorkAdmission.put_nowait_if_open` (Accepted = успех `put_nowait`; `qsize` **после** lock, диагностика).
2. `ensure_profile_queue` без смены mixed `add_*_task`.
3. Isolated ветка в `handle_wallet_editor_document`: обязательный early reject; фиксация владения сразу после put, без await; cleanup всех исходов до Accepted.
4. Тесты § 6. Без live Telegram.

Успех этого code **не** глобальный запрет новой работы в процессе.

### 7.2 UNKNOWN / не обещать

- удаление файла при `OSError` unlink, kill процесса, crash worker после Accepted;
- наблюдаемая разница `put` vs `put_nowait` на unbounded queue (для admit только `put_nowait` в lock);
- Windows-путь `/tmp/wallet_editor` vs `%TEMP%` (сейчас константа);
- отдельный public API vs вызов `_ensure_profile_worker` — code может экспортировать тонкую функцию, не меняя mixed helpers.

Отмена на первом await **после** Accepted — не UNKNOWN: I11.
