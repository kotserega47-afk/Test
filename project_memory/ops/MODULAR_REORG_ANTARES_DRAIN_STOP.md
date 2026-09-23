# Контракт учёта принятой работы, drain и остановки isolated Antares (TASK-39)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **подготовлен к review**; runtime **не** менялся |
| **База** | закрытие TASK-38 `9221f052f8b9bacda10a3041757fa72f7687202c` (принятый review HEAD `f12811035433bce0306ef9ef9d328db43652795a`, Draft PR #41) |
| **Обследованный SHA** | `f128110…` / close `9221f05…` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **AE continuation** | [MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md](MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md) |
| **PTB helper** | [MODULAR_REORG_ANTARES_STARTSTOP.md](MODULAR_REORG_ANTARES_STARTSTOP.md) / TASK-24 `run_ptb_lifecycle` |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Цель: зафиксировать, **что считается ещё принятой работой** после `seal()`, в каком порядке её можно дожимать и какие ресурсы останавливать. Это **не** mixed-stop, **не** serve/polling, **не** cutover `JOB_ACCEPT`. Имена production API **не** выбраны (§ 8).

Тестовый `_HarnessQueue.end_loop` TASK-38 **не** является production stop API и **не** входит в этот контракт как механизм остановки.

---

## 0. Жёсткие правила (приняты этим docs)

1. **`seal()` запрещает новые внешние принятия** (`submit_if_open` / `submit_job_if_open` / `submit_auto_enable_run_if_open` / `put_nowait_if_open`). Уже возвращённый `AdmissionAccepted` / `AdmissionQueued` seal **не** отменяет.
2. **Accepted Auto-Enable orchestrator сохраняет continuation** до `wrapper.finally`. Seal continuation **не** отзывает. Cancel TG-handler / `AdmittedJob` **не** отзывает живой run (TASK-37/38).
3. **WE worker нельзя останавливать, пока оркестратор ещё может поставить batch** — пока есть запись continuation `pending`/`active` **или** оркестраторский Future не terminal. Пустая `Queue` этого **не** отменяет.
4. **Пустая Queue не доказывает drain.** Учитывать: `unfinished_tasks` / item между `get` и `task_done`; Accepted executor Futures; живые continuation; batch `result_future` не done; `_RUNNING`; sender in-flight.
5. **Порядок остановки выводится из зависимостей** (§ 5), не из удобного списка join.
6. **Timeout ≠ «бизнес-эффекта не было»** и **не** разрешает retry той же операции с неизвестным результатом (Playwright / registry / Telegram send).
7. **Already-dead worker** и **смерть worker до `get()`** принятого batch/ingest item — **блокеры**: drain **неуспешен**, пока нет выбранного (сейчас **открытого**) способа завершить Future / handoff. Надзор/restart **не** этот контракт.

---

## 1. Обследованные цепочки (SHA `f128110…`)

### 1.1 Общий job executor

| | Факт |
|--|------|
| Ресурс | `core/job_dispatch.py` `ThreadPoolExecutor` (`get_job_executor()`, prefix `job-worker`, default `max_workers=2`) |
| Владелец постановки | isolated: `WorkAdmission.submit_*` под коротким `_lock`; mixed: `dispatch_job_background` / `loop.run_in_executor` |
| Момент Accepted | успешный `executor.submit` внутри OPEN-check → `AdmissionAccepted.future` |
| Terminal outcome | Future `set_result` / `set_exception` тела callable (`request_job`, `run_auto_enable` wrapper, export/plan/replay/reload) |
| Дочерние работы | `request_job` → `_RUNNING` + file lock + `JOB_REGISTRY` callable (Playwright/jobs); AE wrapper → `enqueue_auto_enable_batch` → WE queue + `result_future`; export/plan/replay — свои I/O |
| Ждёт | слот TPE; внутри job — lock файла `{STATE_DIR}/locks`; AE — WE worker `result()` |
| Сейчас stop | production shutdown **нет**; `_reset_job_executor_for_tests` делает `shutdown(wait=False, cancel_futures=True)` — **запрещено** копировать на Accepted work |

Queued vs running: элемент в очереди TPE ещё не начал callable; running — поток `job-worker-*` внутри callable. Оба — Accepted, если `submit` уже вернул Future.

### 1.2 Auto-Enable orchestrator → batch → result_future

| | Факт |
|--|------|
| Владелец | isolated `/auto_enable_run` → `submit_auto_enable_run_if_open` → wrapper → `run_auto_enable` → `_run_phase_b2_batches` → `enqueue_auto_enable_batch` (`integrations/wallet_editor_auto_enable.py`, `automation/worker.py`) |
| Accepted | Future wrapper на job executor, **не** `Queue.put` |
| Continuation | map на том же `WorkAdmission`; `pending` до activate; `active` на job thread; revoke в `finally` |
| Batch | после обязательной проверки continuation: credentials → `_ensure_profile_worker` → `queue.put` → wait `WalletEditorAutoEnableBatchTask.result_future` |
| Terminal batch | один `set_result` **или** `set_exception` на WE thread; `task_done` в `worker_loop.finally` |
| Дочерние | `execute_enable_batch` (Playwright); registry patch / outbox `prepare_registry_outbox_and_schedule`; TG report через sender |
| Ждёт | слот TPE (orchestrator); живой WE daemon + item processed; Playwright; `result()` на job thread |
| Не ждать admission lock | put / `result()` / Playwright / registry **вне** `_lock` |

N батчей одного run — N put/Future при одном внешнем Accepted. После seal внутренние put continuation **разрешены**.

### 1.3 Ingest → profile queue → активное задание

| | Факт |
|--|------|
| Владелец | `modules/antares/document_ingest.py` isolated: `ensure_profile_queue` + `put_nowait_if_open`; mixed `add_*_task` → `_ensure_profile_worker` + `queue.put` |
| Accepted | `AdmissionQueued` = успешный `put_nowait` под lock (isolated). Diagnostic `qsize` **не** этот объект |
| Terminal | тело `_run_disable_task` / add / edit на WE thread; `task_done`; TG `send_text`/`send_document`; `delayed_cleanup` daemon (sleep 30s — **не** критерий drain бизнес-задания) |
| Дочерние | registry outbox schedule; sender; delayed file delete |
| Ждёт | живой profile thread; очередь профиля; Playwright/engine |
| Conversion `add_task` | **не** первый isolated; этот контракт **не** обещает mixed conversion drain |

Active item: `get()` уже забран, `task_done` ещё нет. `qsize()==0` при `unfinished_tasks>0` — drain **не** завершён.

### 1.4 Schedules

| | Факт |
|--|------|
| Isolated | `modules.antares.scheduler.tick` + `submit_job_if_open`; **не** подключён к `boot`/`run`/`assemble_antares` / helper |
| Mixed | `scheduler.py` `schedule_loop` → `dispatch_job_background` — **вне** isolated drain |
| Accepted | как § 1.1 (`request_job` Future) |
| После seal | `tick` не принимает новую работу (Rejected); уже Accepted Future — слой D |
| Этот контракт | isolated tick, **если** его вызовут; mixed `schedule_loop` stop **открыт как не этот scope** |

### 1.5 PTB callbacks

| | Факт |
|--|------|
| Владелец | `run_ptb_lifecycle` (sandbox/будущий serve) + `Application` caller |
| Работа | handler после ACL: admit → `watch_admitted_future` → reply → `await AdmittedJob.wait()` |
| Accepted | Future job/work; callback **не** владеет отменой Future |
| Terminal callback | reply результата **или** `CancelledError` на wait (Future жив) |
| Ждёт | event loop; job executor; иногда WE `result()` внутри job thread, не на loop |
| PTB `app.stop`/`shutdown` | **не** drain jobs/WE/sender (TASK-23/24/25). `stop.set()` **не** seal (seal должен быть первым) |

Live polling **не** обследуется как действующий isolated путь (`enable_polling=True` отвергается).

### 1.6 Sender / outbox

| | Факт |
|--|------|
| Sender | `integrations/telegram_bot.py`: daemon thread `run_forever`, `asyncio.Queue`, `_worker` `while True`; `send_message_sync` / file → `call_soon_threadsafe(put_nowait)` |
| Владелец исходящего | callers (handlers reply — PTB Bot; jobs/worker — этот sender) |
| Accepted исходящего | успешный enqueue в sender queue (**не** WorkAdmission) |
| Terminal | `_record_delivery_success` / failure после `await bot.send_*` |
| Stop API | production **нет** (`run_forever`, daemon) |
| Registry outbox | `{STATE_DIR}/wallet_editor/` durable; `replay_pending_outbox_records` — Accepted direct work на executor; in-flight append из WE task — дочерняя работа **того** task |
| Durable outbox ≠ drain in-memory queue | записанный outbox переживает процесс; незавершённый in-memory send — нет |

---

## 2. Учёт «ещё принятого» после seal

Считать незавершённым, пока истинно хотя бы одно:

| Учёт | Наблюдение (без sleep-as-proof) |
|------|----------------------------------|
| Executor queued/running | Future Accepted не `done()` |
| AE continuation | запись в `admission._ae_continuations` (`pending` или `active`) |
| AE batch | `result_future` не `done()` **или** item в WE queue **или** между get и `task_done` |
| Ingest/WE task | то же для profile `Queue` / active item |
| `request_job` | ключ в `_RUNNING` и/или держащийся lock-файл живого pid |
| Sender | item в `telegram_bot.queue` **или** `_worker` между get и `task_done` |
| PTB in-flight callback | task handler не завершён (не путать с job Future) |

`qsize()==0` **и** `not queue.empty()` гонки не определяют idle. Нужны Event/barrier на `task_done` / Future done / revoke continuation.

---

## 3. Смерть worker

| Момент | Состояние | Следствие drain |
|--------|-----------|-----------------|
| Уже мёртв, в очереди лежат items | `result_future` / ingest terminal **нет** | **Неуспешный shutdown**, пока политика не выбрана (§ 8). Не timeout-как-доказательство, не retry |
| Смерть **до** `get()` этого item | item в queue, поток нет | то же |
| Смерть **во время** `_run_*` / execute | Playwright/registry могли частично выполниться | Future может не завершиться; **не** трактовать как «не выполнялось»; **не** retry |
| Исключение на живом worker после get | TASK-38: AE Future один раз `set_exception`; `task_done` есть | item снят; оркестратор видит ошибку — это **успех учёта**, не успех бизнеса |

Надзор/restart потока **вне** этого docs.

---

## 4. Timeout и retry

- `Future.result(timeout=…)` / `join(timeout=…)` / `wait(timeout)` истекли → **неизвестно**, жива ли работа и был ли кабинетный эффект.
- **Запрещено** по одному timeout: объявить «не выполнялось», снять continuation, повторно `submit`/`put` ту же бизнес-операцию, `cancel_futures=True` на Accepted.
- Допустимо: классифицировать shutdown как **неуспешный** (§ 6) и эскалировать.

---

## 5. Порядок, выведенный из зависимостей

Ребра (кто кого ждёт):

```text
PTB callback wait  →  Accepted executor Future
AE wrapper (executor)  →  continuation + WE result_future
WE worker  →  Playwright, registry/outbox, sender enqueue
ingest/disable/add/edit worker  →  engine, sender, delayed_cleanup (не drain-критерий)
request_job  →  JOB lock + callable
sender worker  →  Bot HTTP
run_ptb_lifecycle after stop.set  →  app.stop/shutdown (не jobs)
```

**Обязательная последовательность для isolated** (имена шагов — смысл, не API):

1. **`admission.seal()`** — нет новых внешних Accepted/Queued.
2. **Прекратить кормление новых updates** (уже sealed; `stop.set()` не заменяет шаг 1). **OPEN:** звать `stop.set()` до или после шага 3 — § 8.
3. **Дождаться Accepted executor Futures** (включая queued-after-seal AE wrapper) **и** опустошения continuation map. Пока continuation жива — **не** останавливать WE worker.
4. **Дождаться WE profile queues**: нет queued items, нет active item (`task_done` для каждого get), нет незавершённых AE `result_future`.
5. **Sender:** после шагов 3–4 (больше не ставят send из jobs/worker). Дождаться sender queue + in-flight `_worker`.
6. **Executor `shutdown`**: только когда шаги 3–4 не оставляют работы, которая ещё вызовет `submit`/wait на этом пуле. `cancel_futures` на Accepted **запрещён**.
7. **PTB `app.stop` / `shutdown`**: не считать заменой шагов 3–6. Helper TASK-24 сегодня после `stop.set()` сразу PTB stop — **стык открыт** (§ 8).
8. **Lock-файлы `_RUNNING`:** должны уйти сами из `request_job.finally`. Висящий lock живого pid после мёртвого executor — неуспех.

Admission `_lock` **не** держать через wait/join/Playwright/sender/PTB.

Worker **раньше** шага 4 останавливать нельзя: оркестратор после seal имеет право `put`.

---

## 6. Критерии исхода

### Успешный drain (все пункты)

- `admission` **SEALED**; новых внешних accept нет.
- Нет живых AE continuation.
- Все известные Accepted executor Futures `done()` (результат или исключение тела — неважно для drain).
- По каждому isolated WE profile: нет queued item, `unfinished_tasks==0`, нет незавершённых AE `result_future`.
- `_RUNNING` пуст (для job types, которые этот процесс принимал).
- Sender: очередь пуста и нет in-flight send **если** sender входит в isolated stop (иначе явно исключить — § 8).
- Ни один из этих фактов не установлен timeout-суррогатом.

### Явный неуспешный shutdown (достаточно одного)

- Worker мёртв, а в его queue есть item **или** AE `result_future` не done.
- Смерть worker во время execute без terminal Future.
- По истечении объявленного wait drain условия успеха **не** выполнены (остаток описать: чьи Future/queue/continuation).
- `executor.shutdown` с отменой Accepted **или** обрыв `result()` wait без terminal batch.
- Retry бизнес-операции после timeout.

Неуспех **логируется как shutdown failure**, не как «очередь была пуста».

---

## 7. Матрица будущих Event/barrier-тестов (code PR, не этот)

Реальные `WorkAdmission`, `executor.submit`, `Queue`, `Future`. Без sleep-as-proof. Execute/Playwright/Telegram — заглушки. Worker: production loop + тестовый способ **закончить loop после элементов** (как TASK-38 harness), **не** выдавая его за production stop. Join в `finally`. Ошибки рабочих потоков — в основной тест.

| # | Сценарий | Ожидание |
|---|----------|----------|
| D1 | seal, нет Accepted | успех drain без wait worker; новый submit Rejected |
| D2 | `/run_wallet` Accepted running, seal | drain ждёт Future; seal не cancel |
| D3 | AE queued в TPE, seal, затем старт wrapper | continuation жива; worker **не** остановлен до revoke; batch put разрешён |
| D4 | AE `result()` wait, seal | worker жив; после batch terminal — continuation revoke — тогда можно стоп worker |
| D5 | N батчей одного run после seal | все Future batch done; один внешний Accepted |
| D6 | `qsize==0`, но get без `task_done` (active item) | drain **не** успех |
| D7 | ingest Queued, seal | item дожимается; новый ingest Rejected |
| D8 | cancel TG-handler после AE Accepted | continuation и worker drain по Future, не по callback |
| D9 | пустая Queue + живая continuation | стоп worker **запрещён**; drain не успех |
| D10 | пустая Queue + Accepted Future ещё queued в TPE | drain не успех |
| D11 | timeout wait при живом execute | неуспешный shutdown; нет retry; Future не объявлять «не выполнялся» |
| D12 | worker мёртв до get, batch в queue | неуспешный shutdown; `result_future` не done |
| D13 | исключение AE на живом worker после get | Future once; `task_done`; drain этого item успешен как учёт |
| D14 | sender enqueue из worker, jobs уже done | drain не успех, пока sender in-flight (если sender в scope) |
| D15 | `stop.set` без предварительного seal | **запрещённый** порядок; тест фиксирует, что контракт требует seal первым |
| D16 | diagnostic qsize/log throw после put | не превращает batch в «не принятый»; drain ждёт Future |

---

## 8. Открытые решения (явно)

| ID | Вопрос | Почему открыто |
|----|--------|----------------|
| O1 | Имена production API (`drain_*`, `shutdown_*`, sentinel vs Event vs join) | «до выбора API»; harness TASK-38 не кандидат |
| O2 | `stop.set()` до или после drain executor/WE | helper TASK-24 сразу PTB stop после `stop.wait()`; callbacks vs jobs |
| O3 | Входит ли sender в **первый** isolated stop | daemon `run_forever`, нет stop; handlers replies идут через PTB, jobs — через sender |
| O4 | `delayed_cleanup` daemon | не бизнес-terminal; ждать или игнорировать |
| O5 | Политика already-dead: только fail shutdown vs durable handoff vs restart | restart/надзор вне scope; handoff меняет обещание |
| O6 | Isolated `tick` в drain, пока не wired | вызовы tick после seal Rejected; loop нет |
| O7 | Несколько WE profile keys | join все, что процесс создал; conversion profile в isolated процессе **OPEN** |
| O8 | Стык `app.stop` и незавершённых handler `wait()` | Future не cancel; нужен ли drain до `app.stop` |
| O9 | `executor.shutdown(wait=True)` vs явный wait всех известных Future | известные Future есть у caller admit; TPE внутреннюю очередь чужих submit не видим без учёта |
| O10 | Mixed-stop (`schedule_loop`, mixed `add_task`) | **не этот контракт** |

---

## 9. Вне скоупа

Runtime этого PR; serve / live polling; mixed-stop; ослабление mixed gate; merge/retarget/deploy; исходное Test; надзор/restart worker; выдавать test harness за stop API; `JOB_ACCEPT` cutover.

---

## 10. Связь с уже принятым

- TASK-25: seal → затем `stop.set()`; Future после submit — слой D.
- TASK-38: continuation + Future-once AE; dead worker — блокер, перенесён сюда как D12 / O5.
- TASK-24: PTB initialize/start/stop/shutdown **без** worker/sender/executor.
