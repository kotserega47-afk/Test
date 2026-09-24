# Контракт внутреннего Auto-Enable enqueue (TASK-37)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **принят** (`741f5cc…`); code TASK-38 принят GPT на `f12811035433bce0306ef9ef9d328db43652795a`, **не выпущен**; drain/stop контракт TASK-39 принят (`69ae53c…`); code TASK-40+ |
| **База** | закрытие TASK-36 `c9c75336cd12dc5182608790159055aa5c73482a` (принятый review HEAD `3f6d3e75dd4874efb9020374bf82360771092f2e`, Draft PR #39) |
| **Обследованный SHA** | runtime `3f6d3e7…` / close `c9c7533…` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) § 7.2 |
| **Mixed owner** | `automation/worker.py` `enqueue_auto_enable_batch` / `add_*_task` — put/ensure mixed **не** менять; узкая правка AE Future-once допустима (§ 5) |

Цель: отделить **новую** работу от **продолжения уже Accepted** `/auto_enable_run`. Нельзя механически запрещать каждый внутренний `queue.put` после `seal()`, если без него принятый оркестратор не завершит Future. Durable cursor, exactly-once и «sealed ⇒ нет любой новой работы в процессе» **не** объявлять. Успех будущего code **не** drain/join и **не** serve.

**Выбранный механизм (единственный для code):** запись continuation на **том же** `WorkAdmission`, opaque token, **wrapper вокруг** `run_auto_enable`, переданный в `executor.submit`. Thread-local выставляется **внутри wrapper на job thread**. Contextvars, «имя функции», «имя потока» и «мы на executor» **не** являются доказательством Accepted.

---

## 1. Фактические callers `enqueue_auto_enable_batch`

Единственный production-вызов (не тест):

| # | Путь | Файл |
|---|------|------|
| P1 | `_run_phase_b2_batches` → `enqueue_auto_enable_batch(batch, settings)` | `integrations/wallet_editor_auto_enable.py` ≈ L513 |
| P1a | `run_auto_enable` → `_run_phase_b2_batches` | тот же модуль ≈ L632–729 |
| P1b | isolated TG `/auto_enable_run` → `_admit_direct_work` → `submit_if_open(get_job_executor(), run_auto_enable)` | `modules/antares/handlers.py` ≈ L427–500, L135–171 |
| P1c | mixed/unbound TG `/auto_enable_run` → `loop.run_in_executor(None, run_auto_enable)` | handlers ≈ L456–463; **нет** `WorkAdmission` |

`run_auto_enable_plan` **не** вызывает enqueue. Прямые вызовы `enqueue_auto_enable_batch` — unit-тесты, не production.

### Conversion и legacy — не callers enqueue

| Путь | Что ставит | Isolated Antares |
|------|------------|------------------|
| `conversion_wallet_editor_bridge.py` ≈ L233 `add_task` | disable на `CONVERSION_AUTO` | **не** первый isolated |
| `automation/tg_receiver.py` ≈ L160 `add_task` | legacy file | **нет** в isolated handlers |
| mixed `add_*_task` | ingest mixed | не менять |
| isolated ingest | `put_nowait_if_open` | не AE batch |
| mixed `schedule_loop` | семь JOB keys | `run_auto_enable` не key; scheduled AE не подключён |

---

## 2. Цепочки: Accepted, владение, put, wait, locks

### A. Isolated `/auto_enable_run`

```text
event loop: cmd_auto_enable_run
  ACL
  submit_auto_enable_run_if_open(executor, run_auto_enable, ...)
      # под admission lock: OPEN → register continuation → submit(wrapper) → Accepted
  watch_admitted_future; start reply; await wait()  # без admission lock

job executor (wrapper):
  thread-local = continuation; owner_thread = current
  try: run_auto_enable → enqueue_auto_enable_batch (проверка § 3.3) → put → result()
  finally: revoke continuation; clear thread-local
```

| Вопрос | Ответ |
|--------|--------|
| Где впервые Accepted | `submit_auto_enable_run_if_open` / атомарный `executor.submit(wrapper)` — **не** batch `Queue.put` |
| Владение завершением оркестратора | Accepted Future + `AdmittedJob.wait()` на loop |
| Worker / put | после обязательной проверки continuation: `_ensure_profile_worker` + `queue.put` |
| Кто ждёт batch `result_future` | job-executor thread внутри `enqueue_auto_enable_batch` |
| Locks на wait | admission **свободен**; `_registry_lock` не держат; слот job executor занят |

### B. Mixed / unbound

`bound_admission() is None` → `enqueue_auto_enable_batch` как сейчас, без continuation. Docs **не** меняет этот путь.

`bound_admission() is None` **не** выводить из «нет токена». Isolated bound + нет/чужой/отозванный token → отказ, **не** mixed fallback.

### C. Прямой `enqueue_auto_enable_batch` при bound admission

Без действительного continuation — отказ **до** `_ensure_profile_worker` и **до** `put`.

### D. Worker batch

`_run_auto_enable_batch_task`: Playwright на WE thread; `set_result` / `set_exception`. Job executor внутри execute **нет**.

---

## 3. Механизм continuation (lifecycle)

### 3.1 Объект

На **конкретном** `WorkAdmission` (не глобальный dict вне instance):

- opaque `token` (`secrets.token_urlsafe`);
- ссылка на этот admission (`id(admission)` / identity instance);
- `kind="auto_enable_run"`;
- `state`: `pending` → `active` → `revoked`;
- `owner_thread`: `None` пока wrapper не стартовал, затем `threading.current_thread()`;
- `orchestrator_future`: Future из `executor.submit`, заполняется сразу после submit.

Несколько Accepted AE run на одном admission — несколько записей. Чужой instance не видит чужие token.

### 3.2 Создание до старта callable

`executor.submit` может начать `wrapper` **до возврата** `submit`. Поэтому:

Под **тем же** коротким `admission._lock`, что OPEN-check:

1. если не OPEN → `AdmissionRejected`, continuation **нет**;
2. создать запись `pending`, положить в `admission._ae_continuations[token]`;
3. `future = executor.submit(wrapper, …)` где `wrapper` замыкает эту запись;
4. сохранить `future` на записи;
5. вернуть `AdmissionAccepted(future)`.

Если `executor.submit` бросает: **снять** запись в `except` **до** выхода из lock (или в `finally` если future не создан). Активного continuation нет.

`wrapper` **не** contextvars.copy_context: TPE их не переносит. Перенос = сам `wrapper` в `submit`.

### 3.3 Старт wrapper на job thread

В начале `wrapper` (ещё до `run_auto_enable`): короткий lock → `state=active`, `owner_thread=current`; thread-local **этого модуля** = запись. Затем тело оркестратора.

Queued Accepted: запись уже `pending` с момента submit; seal **не** удаляет её. Когда слот executor освобождается после seal, wrapper стартует, становится `active`, enqueue разрешён.

### 3.4 Отзыв

`wrapper` `finally` (нормальный return **и** исключение оркестратора): короткий lock → `state=revoked`, pop из map; clear thread-local.

Cancel Telegram-handler / `AdmittedJob` **не** cancel Future и **не** вызывает этот `finally`. Continuation живого run **не** отзывать из callback TG.

Seal **не** отзывает уже зарегистрированные continuation.

### 3.5 Что не является Accepted

Имя `run_auto_enable`, `thread.name`, «текущий поток — job-worker», наличие `get_job_executor()`. Mixed default executor проходит без token.

---

## 4. Точка проверки и API будущего code

**Обязательная проверка — первый шаг isolated-ветки внутри** `automation.worker.enqueue_auto_enable_batch` **до** credentials-I/O, **до** `_ensure_profile_worker`, **до** `Queue.put`.

Логика:

```text
if bound_admission() is None:
    # mixed/unbound: текущее тело без изменения порядка put/wait
else:
    require_valid_auto_enable_continuation(bound_admission())  # отказ → exception, put нет
    # далее ensure / put / result как сейчас
```

«Нет token» при **bound** admission = isolated отказ, **не** mixed.

| Файл | Изменение code PR |
|------|-------------------|
| `modules/antares/work_admission.py` | map continuation; `submit_auto_enable_run_if_open`; `require_valid_auto_enable_continuation`; register/revoke под `_lock` |
| `modules/antares/handlers.py` | isolated `/auto_enable_run` зовёт `submit_auto_enable_run_if_open`, не голый `submit_if_open` для этого work |
| `modules/antares/auto_enable_continuation.py` (новый, имя code) | thread-local + wrapper factory; **не** contextvars |
| `automation/worker.py` `enqueue_auto_enable_batch` | ветка `bound_admission() is not None` → require **до** ensure/put; unbound тело прежнее |
| `integrations/wallet_editor_auto_enable.py` | может остаться вызов `enqueue_auto_enable_batch`; gate внутри worker |

`/auto_enable_plan` **не** регистрирует AE continuation.

### Охваченные входы (этот контракт)

- isolated `/auto_enable_run` → `run_auto_enable` → `enqueue_auto_enable_batch`;
- любой прямой вызов **`enqueue_auto_enable_batch`**, пока `bound_admission()` не None.

### Не охвачены (не обещать)

- произвольный `Queue.put` / `put_nowait` `WalletEditorAutoEnableBatchTask`;
- `ensure_profile_queue` + put;
- `add_task` / `add_add_wallet_task` / `add_edit_wallet_task`;
- mixed `enqueue_auto_enable_batch` при `bound_admission() is None`;
- `tg_receiver`, conversion bridge.

---

## 5. Срок действия и окно revoke vs enqueue

**Живёт:** от register (шаг 2 § 3.2) до `wrapper.finally` (§ 3.4).

**Запрещено enqueue если** (всё проверяется под `admission._lock` в `require_valid_…`):

- записи нет или `state is revoked`;
- `admission` is not тот instance, что в записи / не `bound_admission()`;
- `owner_thread is not threading.current_thread()` (для `pending` owner ещё None → **отказ** enqueue: put только после старта wrapper);
- thread-local не указывает на **эту** запись.

Независимый caller не ставит thread-local wrapper → отказ до ensure/put.

**Окно после отзыва:** revoke выполняется в `finally` **того же** job thread **после** выхода из `run_auto_enable`. Enqueue вызывается **только** из этого тела, синхронно. Пока идёт `enqueue` (включая `result()`), `finally` не бежит. Другой поток с чужим/украденным token не проходит `owner_thread`. После `finally` thread-local пуст; повторное использование token на том же executor thread невозможно.

Put / ensure / `result()` / Playwright / registry patch — **вне** admission lock. Под lock только register, переход pending→active, require, revoke.

---

## 6. Ошибки до и после batch put

`WalletEditorAutoEnableBatchTask.result_future` создаётся до put.

| Когда | Правило |
|-------|---------|
| До успешного `queue.put` (credentials, `Thread.start`, сам put) | exception в caller оркестратора; **нет** принятого batch; **не** вызывать `result()` на этом Future; если Future уже создан и не put — не ждать его; оркестраторский Future получает ошибку из `run_auto_enable` |
| После `put` | batch принят очередью. Сбой `qsize()` / `log.info` queued **не** делает batch «не поставленным», **не** даёт второй put, **не** отменяет wait. Caller **обязан** дойти до `result()` (или эквивалент завершения) |
| Завершение batch Future | **ровно один** `set_result` **или** один `set_exception` |
| Лог после `set_result` | отдельный try; **не** `set_exception` на already-done Future |

Пустой `candidates`: как сейчас `[]` без put — **только** если isolated continuation уже валиден (иначе отказ § 4, без silent success).

---

## 7. Граница ошибок WE worker

Обследовано `worker_loop` / `_run_auto_enable_batch_task` (`automation/worker.py` ≈ L384–494):

```text
get() → _log_queue_wait   # сейчас ВНЕ try; throw → нет task_done, нет set_* на AE Future, поток может умереть
try:
  _run_auto_enable_batch_task:
    log started
    import execute / RunConfig / execute_enable_batch
    set_result(outcomes)
    log finished          # throw здесь → except set_exception → InvalidStateError
  except: set_exception
finally: task_done
```

**Выбранная защитная граница (обычные исключения на обслуживании уже put batch):** после `get()`, если item — AE batch, любой exception до успешного terminal `set_*` должен **один раз** завершить `result_future`. Сюда входят: `_log_queue_wait`, import execute, start-log, `execute_enable_batch`, ошибки до `set_result`. Реализация: обёртка AE-ветки в `worker_loop` и/или `_run_auto_enable_batch_task` с «если not done: set_exception»; log finished вне того except, что зовёт `set_exception`.

Это **не** меняет mixed ensure/put/add_task. Узкая правка AE Future-once на shared `_run_auto_enable_batch_task` допустима.

**Отложенный блокер (не этот code PR, не timeout-как-доказательство):**

1. Worker **уже мёртв**, в очереди лежат batch — `result_future` не set. Isolated **не** трактует timeout как «execute не было» и **не** retry бизнес-операцию с неизвестным результатом.
2. Смерть worker **до** `get()` этого batch — то же.
3. Надзор/restart/drain worker — TASK lifecycle, не enqueue-контракт.

Тесты: Event/barrier на живом потоке worker; не sleep-timeout как суррогат «не выполнялось».

---

## 8. Lock-order

```text
admission._lock     # register / pending→active / require / revoke; никогда result()/Playwright/patch/ensure
_registry_lock      # только ensure; отпустить до put и до result()
Queue.mutex         # put / get
batch result_future # wait на job thread; set на WE thread
registry I/O        # после result(); не admission
```

---

## 9. Матрица будущих проверок (code PR)

Реальные `WorkAdmission`, `Queue`, `Future`, `executor.submit`. Execute/Playwright — заглушка. Event/barrier. Без live кабинетов, Telegram, сети. Без sleep как доказательства.

| # | Сценарий | Ожидание |
|---|----------|----------|
| E1 | seal **до** нового `/auto_enable_run` | Rejected; continuation нет; `run_auto_enable` нет; ensure/put нет |
| E2 | Accepted → seal **до** enqueue | continuation жива; put разрешён; новый внешний submit Rejected |
| E3 | N батчей одного run | один внешний Accepted; N put+завершённые batch Future |
| E3b | несколько батчей **после** seal | все continuation-put разрешены; новый submit нет |
| E4 | ошибка **до** put | нет принятого batch; нет `result()` wait; оркестраторский Future ошибка; continuation отозвана в `finally` |
| E5 | put ок, execute/`set_exception` | `result()` бросает; Future один раз; не висит |
| E6 | isolated прямой `enqueue_auto_enable_batch` без continuation | отказ **до** ensure/put; mixed не имитировать |
| E6b | нет / чужой instance / отозванный token | ensure/put **отсутствуют** |
| E7 | seal во время `result()` wait | seal не ждёт Future; admission lock свободен |
| E8 | mixed `enqueue` / `add_task` при unbound | прежнее поведение |
| E9 | callable стартует **до** возврата `submit` | continuation уже pending; после wrapper — active; enqueue с owner_thread ок |
| E10 | Accepted в очереди executor, seal, затем старт wrapper | enqueue разрешён; внешний submit нет |
| E11 | `executor.submit` exception | continuation не остаётся; map пуст |
| E12 | cancel TG-handler после Accepted | continuation и run **не** отзывать; Future оркестратора жив |
| E13 | `qsize`/queued-log failure **после** put | batch остаётся принятым; один wait `result()`; второго put нет |
| E14 | исключение на worker **до** execute (в т.ч. wait-log / import) | batch Future завершён один раз exception |
| E15 | `set_result` уже был, затем ошибка диагностики/лога | нет `set_exception`; Future остаётся successful done |

---

## 10. Оставшиеся входы состава выпуска

Admit (Draft, не выпущено): TG jobs; export/plan/run submit; replay; reload; ingest put; tick не wired.

**Обход состава, этот контракт:** isolated `enqueue_auto_enable_batch` без continuation.

**Не первый isolated:** conversion `add_task`; `tg_receiver`; mixed loop / `add_*_task`.

**Не этот этап:** serve; drain/join; already-dead worker (§ 7); mixed-stop.

---

## 11. Границы этого docs

Контракт **принят**. Runtime **не** менялся. Реализация — TASK-38. Не serve, не live polling, не merge. `/auto_enable_plan` без AE continuation. Seal не отменяет принятый run. Произвольный `Queue.put` не закрыт. Timeout ≠ «execute не было». Already-dead worker с очередью — отложенный блокер (§ 7).
