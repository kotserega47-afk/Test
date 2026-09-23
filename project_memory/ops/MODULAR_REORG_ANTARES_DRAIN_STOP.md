# Контракт учёта принятой работы, drain и остановки isolated Antares (TASK-39)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **уточнён, на review**; runtime **не** менялся |
| **База** | закрытие TASK-38 `9221f052f8b9bacda10a3041757fa72f7687202c` (принятый review HEAD `f12811035433bce0306ef9ef9d328db43652795a`, Draft PR #41) |
| **Обследованный SHA** | runtime `f128110…`; этот docs `ffa2702…` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **AE continuation** | [MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md](MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md) |
| **PTB helper** | [MODULAR_REORG_ANTARES_STARTSTOP.md](MODULAR_REORG_ANTARES_STARTSTOP.md) / TASK-24 `run_ptb_lifecycle` |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Цель: полный учёт isolated Accepted work, разделение **work drain** и **resource shutdown**, один порядок со стыком PTB. Это **не** mixed-stop, **не** serve/polling, **не** `JOB_ACCEPT`. Runtime этого PR **нет**.

Тестовый `_HarnessQueue.end_loop` TASK-38 **не** production stop и **не** доказательство остановки WE worker.

---

## 0. Жёсткие правила

1. **`seal()`** запрещает новые внешние принятия. Уже Accepted/Queued seal не отменяет.
2. Реестр всех isolated Accepted **executor** Futures принадлежит **тому же** `WorkAdmission`. Регистрация — в том же `_lock`, что OPEN-check и успешный `executor.submit`, **до** выхода из lock. После успешного seal не существует Accepted Future, ещё невидимого drain.
3. Accepted AE orchestrator держит continuation до `wrapper.finally`. Worker **не** останавливать, пока continuation жива или оркестраторский Future не terminal.
4. Пустая Queue ≠ drain. Пустая sender queue + завершённый job ≠ нет запланированного `put`.
5. **Work drain** ≠ **resource shutdown**. Бизнес-ошибка задания допустима при успешном drain и видна отдельно.
6. Полный graceful shutdown isolated **обязан** остановить sender. Его code можно вынести в отдельный срез; без него полный graceful **не** заявлять.
7. Запрос остановки: **`seal()` → `stop.set()` на owner loop**. `stop.set()` — координация, **не** завершение.
8. Loop PTB **не** блокировать `Future.result()` / `Thread.join()`.
9. Timeout / dead worker → **явный failure**, без restart/handoff/retry; бизнес-исход остаётся неизвестным. Ограниченное время выхода процесса **не** обещать.
10. Mixed-stop — отдельная зависимость до cutover (**O10**).

---

## 1. Обследованные цепочки (SHA `f128110…`)

### 1.1 Job executor

`get_job_executor()` — `ThreadPoolExecutor`, prefix `job-worker`, default `max_workers=2`. Isolated Accepted = успешный `submit` под OPEN. Terminal = done Future тела (`request_job`, AE wrapper, export/plan/replay/reload).

Дочерние: `_RUNNING` + lock-файл; AE → WE `result_future`; Playwright.

`_reset_job_executor_for_tests`: `shutdown(wait=False, cancel_futures=True)` — **запрещено** на Accepted.

### 1.2 Auto-Enable

Accepted = Future wrapper, не `Queue.put`. Continuation на том же admission. Batch wait `result_future` на job thread, вне `_lock`.

### 1.3 Ingest

Isolated: `ensure_profile_queue` **до** `put_nowait_if_open`. Handler может **скачать файл и создать lazy worker до seal**, затем получить Rejected. Такой worker входит в финальный список ресурсов.

Active item: между `get` и `task_done`. `delayed_cleanup` daemon (sleep) **не** критерий work drain.

### 1.4 Schedules

Isolated `tick` не wired. После seal новые submit Rejected. Mixed `schedule_loop` — не этот контракт.

### 1.5 PTB callbacks

`watch_admitted_future` + `await wait()`. Cancel callback **не** cancel Future и **не** снимает Future с реестра admission.

Сейчас helper после `stop.wait()` сразу `seal` (идемпотентно) и PTB cleanup — **без** drain. Будущее изменение — § 5.

### 1.6 Sender (обязателен для полного graceful)

Файл: `integrations/telegram_bot.py`.

| Вызов | Путь |
|-------|------|
| `send_message_sync` / `send_file_sync` (`transport.telegram_transport.send_text` / `send_document`) | `loop.call_soon_threadsafe(queue.put_nowait, item)` затем `_record_enqueue`; **не** ждёт доставки |
| `send_photo_sync` | **прямой** `requests.post(sendPhoto)` на потоке caller; **не** через sender `asyncio.Queue`. При ошибке fallback `send_message_sync` (уже очередь) |
| `send_message_direct` | прямой HTTP; **только тесты** |

Три состояния очереди (для `send_message_sync` / `send_file_sync`):

| # | Состояние | Сейчас видно как |
|---|-----------|------------------|
| S1 | передача **запланирована**: `call_soon_threadsafe` принял callback, `put_nowait` ещё не исполнен на sender loop | `_record_enqueue` уже +1, `queue` ещё может быть пуста |
| S2 | элемент **в** `asyncio.Queue` | `qsize` / `empty()` лгут относительно S1 |
| S3 | отправка **выполняется** | `_worker` между `get` и `task_done` (`await bot.send_*`) |

Пустая очередь и done job **не** доказывают отсутствие S1.

Worker `send_text`/`send_document` возвращаются сразу после планирования S1; `task_done` WE может наступить, пока S1/S2/S3 ещё живы.

### 1.7 Registry / outbox

`prepare_registry_outbox_and_schedule` (`wallet_editor_registry_async.py`):

| Шаг | Когда заканчивается | Кто учитывает |
|-----|---------------------|---------------|
| `persist_durable_result_copy` | синхронно в теле WE task | work drain этого item |
| `create_outbox_record` | синхронно; durable pending на `{STATE_DIR}` | work drain этого item |
| `schedule_registry_append` | **сразу** стартует daemon `we-registry-*`; `append_run_to_dropbox_registry` идёт отдельно | **не** WorkAdmission; **не** work drain; **не** первый resource shutdown |

Durable pending **не** есть доставка Telegram и **не** есть завершение in-flight Dropbox I/O. Первый isolated graceful **не** ждёт Dropbox daemon и **не** обещает sync. `/registry_replay` — отдельный Accepted executor Future, если admit прошёл.

---

## 2. Реестр Accepted executor Futures

**Владелец:** конкретный bound `WorkAdmission` (не глобальный dict вне instance; не PTB Application).

Покрывает **все** isolated `executor.submit`, дающие `AdmissionAccepted`: `submit_if_open`, `submit_job_if_open`, `submit_auto_enable_run_if_open`. Queued ingest (`AdmissionQueued`) в этот реестр **не** входит — учёт через WE queue/active/`result_future`.

### 2.1 Согласование с submit/seal

Под **тем же** коротким `_lock`:

1. если не OPEN → `AdmissionRejected`, Future нет, реестр не трогать;
2. `future = executor.submit(...)` (AE: сначала continuation pending);
3. **вставить `future` в реестр**;
4. выйти из lock, вернуть `AdmissionAccepted(future)`.

`seal()` берёт тот же lock → либо submit уже зарегистрировал Future, либо submit ещё не прошёл OPEN и получит Rejected. Окна «Accepted есть, drain его не видит» нет.

Submit exception: как сейчас, continuation снимается; Future в реестр не класть.

### 2.2 Callback и already-done

`add_done_callback` ставить **только после** отпускания `_lock`.

Если Future уже `done` к моменту `add_done_callback` (callable успел до возврата `submit`): callback выполняется **синхронно** в этом потоке. Он не берёт `_lock` на ожидание; под lock только короткое удаление из реестра (или удаление через структуру, допускающую pop без реentrant acquire на том же потоке, что держит lock — проще: **не вызывать callback под lock**).

Ожидание drain: снимок незавершённых Future **под коротким lock**, `await` / `asyncio.wrap_future` / `wait` — **вне** lock. Playwright/`result()`/join — вне lock.

Идемпотентное удаление при terminal: один done-callback реестра снимает запись, если она ещё там.

### 2.3 Cancel callback handler

Cancel `AdmittedJob` / TG-handler **не** `future.cancel()`, **не** pop из реестра, **не** revoke continuation. Живая принятая работа остаётся в реестре до собственного terminal.

---

## 3. Протокол учёта sender (будущий code-срез)

Пока нет счётчика S1, полный graceful **не** заявлять.

**Выбранный протокол:** под lock sender:

1. Caller `send_message_sync` / `send_file_sync`: увеличить `pending_loop_handoffs`, затем `call_soon_threadsafe(_enqueue_item, payload)`.
2. На sender loop `_enqueue_item`: `queue.put_nowait`; уменьшить `pending_loop_handoffs` (и при ошибке put — тоже уменьшить, зафиксировать failure).
3. `_worker`: in-flight флаг/счётчик между `get` и `task_done`.

Idle sender для drain исходящих: `pending_loop_handoffs==0` **и** очередь пуста **и** in-flight==0. Наблюдение Event/barrier, не sleep.

`send_photo_sync`: часть тела caller (Accepted job/WE item), не S1–S3. Work drain caller включает блокирующий HTTP. Fallback `send_message_sync` после ошибки фото — уже S1–S3.

---

## 4. Work drain vs resource shutdown

| | Work drain | Resource shutdown |
|--|------------|-------------------|
| Смысл | вся принятая работа получила **terminal outcome** | остановлены и **joined** ресурсы этого запуска |
| Успех при бизнес-ошибке | **да** (Future exception / AE `set_exception` видны отдельно) | только если потоки/loop/HTTP закрыты |
| Executor Futures | все из реестра `done()` | затем `shutdown`; `cancel_futures` запрещён |
| Continuation | map пуст | — |
| WE items | нет queued, `unfinished_tasks==0`, AE `result_future` done | production stop loop + **join** thread |
| Sender S1–S3 | idle (§ 3) | cancel task, stop loop, join thread, закрыть HTTP sender Bot |
| PTB | in-flight callbacks, которые ещё ensure/put, завершились как producers | `app.stop` / `shutdown` |
| Dropbox daemon / `delayed_cleanup` | не входят | не входят в первый isolated (daemon; durable outbox уже есть) |

Drain успешен, а WE/executor/sender thread ещё жив → **resource shutdown не успешен**.

### 4.1 Production stop WE worker (после drain)

**Выбор:** предмет в production `worker_loop` — выделенный sentinel type (не test exception из `Queue.get`). После drain (нет queued/active/continuation, которые ещё put) положить sentinel в каждую profile queue этого процесса, loop выходит, **join** thread.

`_HarnessQueue.end_loop` / `_EndWorkerLoop` **не** цитировать как production. Имя sentinel — code PR.

Пока drain не завершён, sentinel **не** класть (оркестратор ещё может put).

---

## 5. Один порядок и стык с PTB

Сохранить **`request_antares_stop`: `seal()` затем `stop.set()` на owner loop**.

`stop.set()` будит `run_ptb_lifecycle` (`await stop.wait()`). Это **запрос** начать фазу остановки, не idle.

### Необходимое будущее изменение helper

Сейчас: `stop.wait()` → `seal()` → сразу PTB `_await_cleanup`. Нужно:

```text
[уже] initialize → start → admission.open → await stop.wait()
      # request_antares_stop уже сделал seal до set
1. Прекратить входящие updates (сейчас polling нет; когда serve — updater.stop
   здесь, не после drain jobs). Isolated tick после seal не звать.
2. Дождаться завершения in-flight PTB callbacks как producers
   (download/ensure/put_nowait). Их CancelledError не cancel Accepted Future.
3. Work drain: реестр Futures, continuation, WE queued/active/result_future,
   sender S1–S3.
   Только await / wrap_future / to_thread — не Future.result/join на PTB loop.
4. Resource shutdown: WE sentinel+join; executor shutdown после drain
   (не wait=True после deadline); sender task/loop/thread + HTTP;
   затем PTB app.stop / shutdown (helper TASK-24).
```

Финальный список WE workers снимать **после** шага 2: handler, начавший download до seal, может `ensure_profile_queue` **после** seal и получить Rejected — поток всё равно создан.

Admission lock не держать через шаги 2–4.

---

## 6. Dead worker / deadline (первый вариант)

**Выбор:** явный **failure** shutdown. Нет automatic restart, durable handoff «считай выполненным», retry бизнес-операции. Неизвестный исход сохранить в отчёте.

Отчёт остатков (минимум): SEALED?; незавершённые Future из реестра (queued vs running, если известно); живые continuation; profile → qsize / unfinished / dead thread; AE `result_future`; sender `pending_loop_handoffs` / queue / in-flight.

Ограничения:

- незавершённый TPE job **нельзя безопасно убить**;
- после истечения deadline **`shutdown(wait=True)` может зависнуть** — не звать;
- `shutdown(wait=False, cancel_futures=False)` не останавливает running thread;
- **не** обещать ограниченное время `exit` процесса.

---

## 7. Матрица будущих Event/barrier-тестов

Реальные `WorkAdmission`, `executor.submit`, `Queue`, `Future`. Без sleep-as-proof. Harness только для теста loop, не как proof production stop.

| # | Сценарий | Ожидание |
|---|----------|----------|
| D1 | seal, нет Accepted | drain успех; новый submit Rejected |
| D2 | `/run_wallet` running, seal | drain ждёт реестр Future; не cancel |
| D3 | AE queued в TPE, seal, старт wrapper | continuation жива; worker не стопать; put разрешён |
| D4 | AE `result()` wait, seal | worker жив до terminal batch + revoke |
| D5 | N батчей после seal | все batch Future done; один внешний Accepted в реестре |
| D6 | `qsize==0`, get без `task_done` | drain не успех |
| D7 | ingest Queued, seal | item дожимается; новый ingest Rejected |
| D8 | cancel TG после AE Accepted | реестр и continuation живы |
| D9 | пустая Queue + живая continuation | стоп worker запрещён |
| D10 | пустая Queue + Future ещё в очереди TPE | drain не успех |
| D11 | deadline при живом execute | failure + отчёт; нет retry |
| D12 | worker мёртв до get | failure; `result_future` не done; нет retry |
| D13 | исключение AE после get | drain work успех; бизнес-ошибка видна отдельно |
| D14 | job done, sender S1 ещё не put | work drain исходящих не успех |
| D15 | `stop.set` без seal | запрещённый порядок request API |
| D16 | qsize/log throw после put | batch принят; drain ждёт Future |
| D17 | submit выиграл гонку с seal | Future **в реестре** до возврата submit; drain его видит |
| D18 | Future done до `add_done_callback` | callback sync вне submit-lock; реестр корректно пуст; нет deadlock |
| D19 | job завершён, sender put ждёт loop (S1) | пустая queue ≠ idle sender |
| D20 | очередь пуста, `_worker` в send (S3) | drain исходящих не успех |
| D21 | pre-seal ingest: ensure worker после seal, put Rejected | worker в финальном списке; join обязателен |
| D22 | work drain done, WE/executor thread жив | resource shutdown **не** успешен |
| D23 | dead worker / deadline | failure с остатками; нет retry/handoff/restart |

---

## 8. Решения (бывшие O1–O9) и срезы code

| Было | Решение | Срез (не этот PR) |
|------|---------|-------------------|
| O1 API | Реестр на `WorkAdmission`; WE stop = production sentinel + join; drain/shutdown — отдельные исходы | admission registry; worker sentinel; helper orchestration |
| O2 `stop.set` vs drain | Request: seal→set как сейчас. Drain **после** `stop.wait()`, **до** PTB cleanup | изменение `run_ptb_lifecycle` |
| O3 sender | В полном graceful **обязателен**; отдельный code-срез учёта S1–S3 | sender handoff + stop loop/thread/HTTP |
| O4 `delayed_cleanup` | Не work drain, не первый resource shutdown | — |
| O5 dead worker | Failure + отчёт; без restart/handoff/retry | remainder report |
| O6 tick | После seal не звать; не wired | serve/schedules отдельно |
| O7 профили | Join **все** `_profile_workers`, созданные процессом до конца producers (включая Rejected после ensure) | resource shutdown |
| O8 `app.stop` vs wait() | Сначала producers+work drain, потом PTB stop; cancel wait ≠ cancel Future | helper |
| O9 shutdown TPE | Ждать **реестр** Future, не «все неизвестные». После deadline не `wait=True` | drain + shutdown slice |
| O10 mixed-stop | **Открыто** до cutover | не isolated TASK-39 |

Имена функций code PR может уточнить; семантика этого docs обязательна.

---

## 9. Вне скоупа

Runtime этого PR; реализация срезов; serve / live polling; mixed-stop; mixed gate; merge/retarget/deploy; исходное Test; надзор worker; harness как stop API.

---

## 10. Связь

- TASK-25: seal → `stop.set()`; слой D = Accepted Future.
- TASK-38: continuation; Future-once AE; harness ≠ stop.
- TASK-24: PTB cleanup остаётся последним HTTP PTB; jobs/WE/sender вставляются **перед** ним.
