# Контракт внутреннего Auto-Enable enqueue (TASK-37)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **подготовлен к review**; runtime **не** менялся |
| **База** | закрытие TASK-36 `c9c75336cd12dc5182608790159055aa5c73482a` (принятый review HEAD `3f6d3e75dd4874efb9020374bf82360771092f2e`, Draft PR #39) |
| **Обследованный SHA** | runtime `3f6d3e7…` / close `c9c7533…` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) § 7.2 |
| **Mixed owner** | `automation/worker.py` `enqueue_auto_enable_batch` / `add_*_task` — **поведение не менять** этим docs-этапом |

Цель: отделить **новую** работу от **продолжения уже Accepted** операции `/auto_enable_run`. Нельзя механически запрещать каждый внутренний `queue.put` после `seal()`, если без него принятый `run_auto_enable` не может завершить Future. Durable cursor, exactly-once и «sealed ⇒ нет любой новой работы в процессе» **не** объявлять. Успех будущего code **не** drain/join и **не** serve.

---

## 1. Фактические callers `enqueue_auto_enable_batch`

Единственный production-вызов (не тест):

| # | Путь | Файл |
|---|------|------|
| P1 | `_run_phase_b2_batches` → `enqueue_auto_enable_batch(batch, settings)` | `integrations/wallet_editor_auto_enable.py` ≈ L513 |
| P1a | `run_auto_enable` → `_run_phase_b2_batches` (dry_run=0, manual TG или `manual=False` scheduled API) | тот же модуль ≈ L632–729 |
| P1b | isolated TG `/auto_enable_run` → `_admit_direct_work` → `submit_if_open(get_job_executor(), run_auto_enable)` | `modules/antares/handlers.py` ≈ L427–500, L135–171 |
| P1c | mixed/unbound TG `/auto_enable_run` → `loop.run_in_executor(None, run_auto_enable)` | handlers ≈ L456–463; **нет** `WorkAdmission` |

`run_auto_enable_plan` **не** вызывает enqueue.

Прямые вызовы `enqueue_auto_enable_batch` есть только в unit-тестах (`test_wallet_editor_concurrency_guard.py` и моки orchestrator). Это **не** production-вход.

### Conversion и legacy — не callers enqueue

| Путь | Что ставит | Достижимость из isolated Antares |
|------|------------|----------------------------------|
| `integrations/conversion_wallet_editor_bridge.py` ≈ L233 `add_task` | disable `WalletEditorTask` на профиль `CONVERSION_AUTO` | **нет**: bridge не импортируется assemble/boot/run; conversion — mixed/Raccoon. **Не** включать в первый isolated Antares автоматически |
| `automation/tg_receiver.py` ≈ L160 `add_task` | legacy TG file → disable queue | **нет** в `get_antares_handlers()` / isolated ingest |
| mixed `add_task` / `add_add_wallet_task` / `add_edit_wallet_task` | ingest mixed | isolated ingest уже `put_nowait_if_open`; mixed API **не** менять |
| isolated document ingest | `put_nowait_if_open` | TASK-34; **не** Auto-Enable batch |
| mixed `schedule_loop` | `dispatch_job_background` семи report/registry keys | `run_auto_enable` **не** JOB_REGISTRY key; scheduled AE в loop **не** подключён (WE-AE-SCHEDULER-C open) |

Общая очередь `CONVERSION_AUTO`: conversion disable и Auto-Enable batch **сериализуются** на одном worker. Это факт runtime, не доказательство, что conversion — вход isolated Antares.

---

## 2. Цепочки: Accepted, владение, put, wait, locks

### A. Isolated `/auto_enable_run` (состав выпуска, не выпущен)

```text
event loop: cmd_auto_enable_run
  ACL guard_or_deny
  submit_if_open(get_job_executor(), run_auto_enable, actor, manual=True)
      # короткий admission lock: OPEN → executor.submit → AdmissionAccepted
  watch_admitted_future  # observer на Future оркестратора
  reply «Запускаю…»
  await admitted.wait()  # loop, БЕЗ admission lock

job executor thread (тот же Future):
  run_auto_enable(...)          # план, gate, возможно несколько батчей
    for batch:
      enqueue_auto_enable_batch
        _ensure_profile_worker  # _registry_lock только lookup/Thread.start
        Queue.put(WalletEditorAutoEnableBatchTask)  # unbounded put
        result_future.result()  # БЛОКИРУЕТ этот слот job executor
      patch_enable_results_in_dropbox_registry  # после wait, тот же поток
    return AutoEnableRunResult → set_result оркестраторского Future
```

| Вопрос | Ответ |
|--------|--------|
| Где работа **впервые** Accepted | `submit_if_open` оркестратора `run_auto_enable` — **не** `Queue.put` батча |
| Кто владеет завершением | Accepted Future оркестратора; `AdmittedJob.wait()` на loop; batch Future — внутренний шаг |
| Где worker и `queue.put` | `enqueue_auto_enable_batch`: `_ensure_profile_worker` + `queue.put`; daemon `worker_loop` |
| Кто ждёт `result_future` | **поток job executor**, тот что выполняет `run_auto_enable`; **не** event loop и **не** admission lock |
| Locks/executor во время wait | admission lock **свободен**; `_registry_lock` **не** держат; слот `get_job_executor()` **занят** до возврата `result()`; WE worker обрабатывает очередь |

Вызов из job executor **не** есть доказательство Accepted: mixed использует **другой** executor (`run_in_executor(None, …)`).

### B. Mixed / unbound `/auto_enable_run`

Те же `run_auto_enable` → enqueue → `result_future.result()`. Accepted **нет**: нет `WorkAdmission`. Wait — поток default executor. Этот docs **не** меняет mixed.

### C. Прямой `enqueue_auto_enable_batch` (тесты / будущий caller)

Нет внешнего Accepted. `queue.put` + неограниченный `result()`. Isolated code **не** должен давать этому неограниченный обход.

### D. Завершение батча на worker

`worker_loop` → `_run_auto_enable_batch_task`: Playwright `execute_enable_batch` в **потоке WE worker**; `result_future.set_result` / `set_exception`. Job executor **не** используется внутри execute. Admission lock нет.

---

## 3. Выбранный протокол: continuation принятой операции

**Имя:** continuation token принятого `/auto_enable_run`.

Правила (однозначные):

1. **Новый внешний вход после seal запрещён.** Isolated `/auto_enable_run` / `/auto_enable_plan` по-прежнему только через `submit_if_open`. Seal → Rejected, оркестратор не стартует, enqueue нет.
2. **Разрешённое продолжение** — только `enqueue_auto_enable_batch` (и его isolated-обёртка), вызванное **из ещё не завершённого** Accepted Future с `fn is run_auto_enable` (тот же объект, что передали в `submit_if_open`). Каждый батч цикла `_run_phase_b2_batches` — тот же Accepted run, не новая внешняя работа.
3. **Seal после Accepted, до внутреннего enqueue** — continuation **разрешено**: иначе Accepted Future не завершится. Это **не** новый `/auto_enable_run`.
4. **Прямой caller без continuation** (тест, CLI, чужой модуль, `queue.put` batch вручную) — isolated **отказ**; mixed `enqueue_auto_enable_batch` без токена **не** менять этим этапом.
5. **«Вызов с потока executor» не достаточен.** Нужна связь с конкретным Accepted Future / токеном, выданным в момент `AdmissionAccepted`.
6. **Не** использовать `put_nowait_if_open` как единственный затвор батча: после seal он запретил бы продолжение принятого run.
7. Admission lock **не** держать на `result_future.result()`, на весь batch loop, на Playwright, на registry patch.
8. Ошибки: любой провал **до** успешного `put` (credentials, `Thread.start`, `put`) должен **завершить** batch Future (`set_exception` / raise в оркестратор) — **не** вечный `result()`. После `put` worker уже делает `set_exception` на сбое execute. Пустой `candidates` — `[]` без wait (как сейчас). Потерянный worker без `set_*` — известный hang mixed; isolated обёртка обязана закрыть Future при ошибке start; **timeout как единственное доказательство** в тестах не использовать (Event/barrier).

Токен (будущий code, не этот PR): выдать при Accepted `auto_enable_run`; проверить в isolated enqueue; снять когда оркестраторский Future done. Точная форма (nonce vs Future id vs contextvars) — **открытое решение** § 8.

---

## 4. Lock-order и взаимные ожидания

Порядок, который **уже** есть и который isolated **сохраняет**:

```text
admission._lock          # только submit/seal/state; никогда через Future.wait / batch
_registry_lock           # только ensure worker; отпустить до put и до result()
Queue.mutex              # put / get
result_future            # wait на job-executor thread; set на WE worker thread
registry/Dropbox/PG      # patch после result(); не admission
```

Запрещённые циклы для будущего code:

| Нельзя | Почему |
|--------|--------|
| admission lock → `result_future.result()` | seal не сможет взять lock; stop зависнет |
| admission lock → весь `_run_phase_b2_batches` | то же + Playwright |
| `_registry_lock` → `result()` | worker start заблокирует другие профили |
| WE worker → `get_job_executor().submit` с ожиданием, пока job slot ждёт этот же batch | потенциальный deadlock (сейчас execute **не** ходит в job executor) |

Не deadlock, но ёмкость: `JOB_EXECUTOR_MAX_WORKERS` (по умолчанию 2) слот занят на всё время всех батчей. Conversion disable на той же `CONVERSION_AUTO` очереди удлиняет wait — не круговое ожидание.

---

## 5. Матрица будущих проверок (code PR)

Fake clock не нужен. Event/barrier; без sleep как доказательства; без live кабинетов, Telegram, сети. Playwright/execute — заглушка worker, **реальные** `WorkAdmission`, `Queue`, `Future`, `executor.submit`.

| # | Сценарий | Ожидание |
|---|----------|----------|
| E1 | seal **до** нового `/auto_enable_run` | Rejected; `run_auto_enable` нет; enqueue/`put` нет |
| E2 | OPEN → Accepted оркестратора → seal **до** первого enqueue | continuation `put` разрешён; оркестраторский Future доходит; новый внешний submit — Rejected |
| E3 | Accepted → N батчей put+wait → patch | все `result_future` завершены; один внешний Accepted |
| E4 | ошибка **до** put (start/put exception) | batch Future не висит; оркестраторский Future ошибка; нет вечного wait |
| E5 | put успешен, worker `set_exception` | `result()` бросает; оркестратор обрабатывает как сейчас; Future не висит |
| E6 | прямой `enqueue_auto_enable_batch` без токена (isolated) | отказ; `put` нет **или** не wait навсегда |
| E7 | lock barrier: seal во время `result()` wait | seal не ждёт Future; admission lock свободен (Event: waiter на lock не держит Future) |
| E8 | mixed `enqueue` / `add_task` baseline | без изменения поведения |

Не копировать mixed hang «worker умер без set_*» как успех isolated.

---

## 6. Оставшиеся входы состава первого выпуска

Уже с isolated admit (Draft, **не выпущено**): TG jobs `submit_job_if_open`; direct ops export/plan/run `submit_if_open`; replay; reload_rules; ingest `put_nowait_if_open`; isolated `tick` (не подключён к boot/run).

**Ещё обход состава:** внутренний Auto-Enable `queue.put` (этот контракт).

**Не состав isolated Antares (не подключать автоматически):** conversion `add_task`; `tg_receiver`; mixed `schedule_loop` / `dispatch_job_background`; mixed `add_*_task`; Raccoon/WR.

**Ещё не код перехода:** serve; drain/join worker; executor shutdown; sender stop; mixed-stop.

---

## 7. Границы этого docs

Runtime нет. Mixed `enqueue_auto_enable_batch` / `add_task` не менять. Не serve, не live polling, не merge. `/auto_enable_plan` без enqueue. Не обещать, что seal отменяет уже принятый run.

---

## 8. Открытые решения (не блокируют контракт выбора протокола)

1. Форма continuation token (явный nonce на `WorkAdmission` vs identity Accepted Future vs contextvars на job thread).
2. Isolated-обёртка рядом с handlers **против** крючка внутри shared `enqueue_auto_enable_batch` (второе смешает mixed).
3. Нужен ли отдельный fail, если WE thread умер без `set_*`, кроме ошибки start — без sleep-timeout как доказательства в тестах.
4. Нужен ли scheduled `run_auto_enable` через isolated `tick` (сейчас **нет** в семи keys) — отдельный этап, не этот контракт.
