# Контракт учёта принятой работы, drain и остановки isolated Antares (TASK-39)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **принят** (`69ae53c65b66a9ad918c294d3f6f1e53192c47a0`); runtime **не** менялся; code — TASK-40+ |
| **База** | закрытие TASK-38 `9221f052f8b9bacda10a3041757fa72f7687202c` (принятый review HEAD `f12811035433bce0306ef9ef9d328db43652795a`, Draft PR #41) |
| **Обследованный SHA** | runtime `f128110…`; принятый docs `69ae53c…` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **AE continuation** | [MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md](MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md) |
| **PTB helper** | [MODULAR_REORG_ANTARES_STARTSTOP.md](MODULAR_REORG_ANTARES_STARTSTOP.md) / TASK-24 `run_ptb_lifecycle` |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Цель: полный учёт isolated Accepted work, разделение **work drain** и **resource shutdown**, один порядок со стыком PTB. Это **не** mixed-stop, **не** serve/polling, **не** `JOB_ACCEPT`. Runtime этого PR **нет**. Первый code-срез — TASK-40 (`wait_accepted_executor_work`); успешное ожидание executor Futures **не** есть полный drain.

Тестовый `_HarnessQueue.end_loop` TASK-38 **не** production stop и **не** доказательство остановки WE worker.

---

## 0. Жёсткие правила

1. **`seal()`** запрещает новые внешние принятия. Уже Accepted/Queued seal не отменяет.
2. Реестр всех isolated Accepted **executor** Futures принадлежит **тому же** `WorkAdmission`. Регистрация — в том же `_lock`, что OPEN-check и успешный `executor.submit`, **до** выхода из lock. После успешного seal не существует Accepted Future, ещё невидимого drain.
3. Accepted AE orchestrator держит continuation до `wrapper.finally`. Worker **не** останавливать, пока continuation жива или оркестраторский Future не terminal.
4. Пустая Queue ≠ drain. Пустая sender queue + завершённый job ≠ нет запланированного `put`.
5. **Work drain** ≠ **resource shutdown**. Бизнес-ошибка задания допустима при успешном drain и видна отдельно.
6. Полный graceful shutdown isolated **обязан** остановить sender **и** дождаться уже запущенных `we-registry-*` (join). Их code можно вынести в отдельные срезы; без них полный graceful **не** заявлять.
7. Запрос остановки: **`seal()` → `stop.set()` на owner loop**. `stop.set()` — координация, **не** завершение.
8. Loop PTB **не** блокировать `Future.result()` / `Thread.join()`.
9. Timeout / dead worker / активный registry daemon после deadline → **явный failure**, без restart/handoff/retry; бизнес-исход остаётся неизвестным. Ограниченное время выхода процесса **не** обещать.
10. Cancel/timeout **ожидающего drain** не отменяет Accepted concurrent Futures, не отзывает continuation, не снимает незавершённую работу с реестра.
11. Mixed-stop — отдельная зависимость до cutover (**O10**).

---

## 1. Обследованные цепочки (SHA `f128110…`)

### 1.1 Job executor

`get_job_executor()` — `ThreadPoolExecutor`, prefix `job-worker`, default `max_workers=2`. Isolated Accepted = успешный `submit` под OPEN. Terminal = done Future тела (`request_job`, AE wrapper, export/plan/replay/reload).

Дочерние: `_RUNNING` + lock-файл; AE → WE `result_future`; Playwright.

`_reset_job_executor_for_tests`: `shutdown(wait=False, cancel_futures=True)` — **запрещено** на Accepted.

### 1.2 Auto-Enable

Accepted = Future wrapper, не `Queue.put`. Continuation на том же admission. Batch wait `result_future` на job thread, вне `_lock`.

### 1.3 Ingest

Isolated: `ensure_profile_queue` **до** `put_nowait_if_open`. Handler может **скачать файл и создать lazy worker до seal**, затем получить Rejected. Такой worker входит в инвентарь, но **не** единственный поздний источник: Pending Accepted AE после старта wrapper вызывает `_ensure_profile_worker` в `enqueue_auto_enable_batch`.

Active item: между `get` и `task_done`. `delayed_cleanup` — отдельное исключение (§ 1.7), не registry I/O.

### 1.4 Schedules

Isolated `tick` не wired. После seal новые submit Rejected. Mixed `schedule_loop` — не этот контракт.

### 1.5 PTB callbacks

`watch_admitted_future` + `await wait()`. Cancel callback **не** cancel Future и **не** снимает Future с реестра admission. Конец PTB callbacks закрывает **только этот** источник создания workers.

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
| `schedule_registry_append` | сразу стартует daemon `we-registry-*`; `append_run_to_dropbox_registry` на этом потоке | **не** WorkAdmission; **не** work drain WE item; для **полного** graceful — отдельный учёт + **join** (срез code) |

Возврат WE task **не** означает конец этого I/O. Durable pending запись **может** остаться для `/registry_replay`; это **не** разрешение игнорировать активный daemon.

`append_run_to_dropbox_registry` **может** порождать sender enqueue: `_send_slow_append_warning` / `_send_timeout_warning` / `_send_rev_conflict_warning` / `_send_missing_otlezka_warnings` → `send_message_sync`. Поэтому join уже запущенных registry-потоков **предшествует** закрытию sender.

`delayed_cleanup` (`automation.worker`: sleep + `os.remove` input/result) **не** Dropbox и **не** sender. Явно ограниченное исключение первого isolated: не ждать, не join, не смешивать с registry I/O. Потеря незавершённого unlink после deadline **не** делает shutdown failure.

`/registry_replay` — отдельный Accepted executor Future, если admit прошёл.

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

Ожидание drain: снимок незавершённых Future **под коротким lock**; ждать **вне** lock. Playwright/`result()`/join — вне lock. **Не** `await asyncio.wrap_future(concurrent_future)` как единственный механизм: cancel этого await **может** `concurrent.Future.cancel()`.

### 2.3 Cancel callback handler

Cancel `AdmittedJob` / TG-handler **не** `future.cancel()`, **не** pop из реестра, **не** revoke continuation. Живая принятая работа остаётся в реестре до собственного terminal.

### 2.4 Отмена / timeout ожидающего drain

Cancel или timeout **задачи drain** (helper, `asyncio.wait_for`, CancelledError):

- **не** `cancel()` Accepted concurrent Futures;
- **не** revoke continuation;
- **не** pop незавершённой работы из реестра.

**Выбранное ожидание:** terminal-сигнал, принадлежащий `WorkAdmission` (Event / отдельный asyncio.Future), выставляется **только** done-callback реестра. Drain ждёт эти сигналы. `asyncio.shield(wrap_future(...))` допустим как эквивалент, если cancel drain **гарантированно** не доходит до concurrent Future; голый `wrap_future` **запрещён**.

Владелец cleanup — **один** drain-session объекта на `WorkAdmission` (или helper): регистрация callback реестра уже сделана при submit (§ 2.2). Повторный cancel/timeout **не** вешает новый `add_done_callback` и **не** создаёт второй cleanup. Повторный drain после отмены снова читает актуальный реестр и ждёт те же сигналы.

Идемпотентное удаление при **собственном** terminal работы: один done-callback реестра снимает запись, если она ещё там.

---

## 3. Протокол учёта sender (будущий code-срез)

Пока нет счётчика S1, полный graceful **не** заявлять.

**Выбранный протокол:** под lock sender; учёт **не** зависит от успеха логирования (`logger.info` после enqueue сегодня может бросить — счётчик всё равно должен быть консистентен).

1. Caller `send_message_sync` / `send_file_sync`: под lock увеличить `pending_loop_handoffs`, отпустить lock, вызвать `call_soon_threadsafe(_enqueue_item, payload)`.
2. Если `call_soon_threadsafe` бросил: под lock уменьшить `pending_loop_handoffs` **ровно один раз**, записать terminal failure этой передачи. Не оставлять +1. Не откатывать дважды (в except и в `_enqueue_item`).
3. На sender loop `_enqueue_item`: `queue.put_nowait`; уменьшить `pending_loop_handoffs`. Ошибка put (в т.ч. closed queue) — тоже уменьшить **один раз** и terminal failure; счётчик не зависает.
4. `_worker`: in-flight между `get` и `task_done` (учёт in-flight тоже не через log).

**Закрытие приёма и idle:** все producers sender на этом этапе уже завершены (Accepted jobs/WE items, PTB producers, **joined** registry daemons). Затем под **тем же** lock: запретить новый handoff (intake sealed) **и** прочитать idle (`pending_loop_handoffs==0` ∧ очередь пуста ∧ in-flight==0). Между проверкой idle и stop новый handoff **не может** появиться незаметно: `call_soon` после seal intake — failure, счётчик не растёт. Наблюдение Event/barrier, не sleep.

`send_photo_sync`: часть тела caller, не S1–S3. Fallback `send_message_sync` — уже S1–S3 и подчиняется intake seal.

---

## 4. Work drain vs resource shutdown

| | Work drain | Resource shutdown |
|--|------------|-------------------|
| Смысл | вся принятая работа получила **terminal outcome** | остановлены и **joined** ресурсы этого запуска |
| Успех при бизнес-ошибке | **да** (Future exception / AE `set_exception` видны отдельно) | только если потоки/loop/HTTP закрыты |
| Executor Futures | все из реестра `done()` | затем `shutdown`; `cancel_futures` запрещён |
| Continuation | map пуст | — |
| WE items | нет queued, `unfinished_tasks==0`, AE `result_future` done | production stop loop + **join** thread |
| Sender S1–S3 | idle при sealed intake (§ 3) | cancel task, stop loop, join thread, закрыть HTTP sender Bot |
| PTB | callbacks-producers завершились | `app.stop` / `shutdown` |
| `we-registry-*` уже стартовавшие | join (полный graceful); durable pending может остаться | join до закрытия sender |
| `delayed_cleanup` | не входит | явное исключение: не ждать |

Drain успешен, а WE/executor/sender/registry thread ещё жив → **resource shutdown / полный graceful не успешен**.

Deadline при живом `we-registry-*`: shutdown **failure** с остатком в отчёте; без retry; полный graceful **не** заявлять. Durable outbox для replay это не компенсирует.

### 4.1 WE workers: наблюдение vs финальный список

Во время drain (ожидание executor/continuation/WE items) наблюдается **актуальный** `_profile_workers` / admission registry. Потоки **продолжают работать**; sentinel **не** класть — AE ещё queued в TPE может стартовать и вызвать `_ensure_profile_worker`.

**Окончательный** список для sentinel/join снимается **после** завершения:

1. PTB producers (download/ensure/put);
2. всех Accepted executor Futures из реестра;
3. всех continuation (map пуст).

К этому моменту нет разрешённого пути создать новый worker: seal запретил внешний ingest; PTB callbacks кончились; Accepted AE/jobs кончились и больше не вызовут `_ensure_profile_worker`; continuation нет. Затем sentinel в каждую очередь из этого снимка и **join**.

`_HarnessQueue.end_loop` / `_EndWorkerLoop` **не** production. Имя sentinel — code PR.

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
2. Дождаться in-flight PTB callbacks как producers
   (download/ensure/put_nowait). Их CancelledError не cancel Accepted Future
   и не drain (§ 2.4). Это закрывает только PTB-источник workers.
3. Work drain Accepted: актуальный реестр Futures + continuation + WE items.
   Ждать terminal-сигналы admission, не голый wrap_future.
   WE workers работают. Новые profile workers от AE ensure ещё возможны.
4. Join уже запущенных we-registry-* (полный graceful; срез code).
5. Снять окончательный список WE workers (producers + executor + continuation
   кончились; нового worker нет). Sentinel + join.
6. Sender: intake seal + idle S1–S3 (все producers, включая registry, уже
   не живы), затем task/loop/thread + HTTP.
7. Executor shutdown после drain (не wait=True после deadline).
8. PTB app.stop / shutdown (helper TASK-24).
   На PTB loop: не Future.result / Thread.join.
```

Admission lock не держать через шаги 2–8. Cancel шага 3 — § 2.4.

---

## 6. Dead worker / deadline (первый вариант)

**Выбор:** явный **failure** shutdown. Нет automatic restart, durable handoff «считай выполненным», retry бизнес-операции. Неизвестный исход сохранить в отчёте.

Отчёт остатков (минимум): SEALED?; незавершённые Future из реестра (queued vs running, если известно); живые continuation; profile → qsize / unfinished / dead thread; AE `result_future`; живые `we-registry-*`; sender `pending_loop_handoffs` / queue / in-flight / intake sealed?.

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
| D24 | PTB callbacks кончились, AE ещё queued; после seal AE стартует, `_ensure_profile_worker` для нового профиля, batch done | новый worker **обязан** попасть в финальный список, получить sentinel и join |
| D25 | drain отменён, Accepted Future ещё queued в TPE | Future **не** cancelled, остаётся в реестре, затем выполняется **один** раз |
| D26 | повторный cancel drain | нет второго cleanup/callback; реестр и continuation без изменений |
| D27 | `call_soon_threadsafe` бросил после +handoff | handoff −1 ровно раз; failure записан; idle возможен |
| D28 | loop-side `put_nowait` бросил | handoff −1; terminal failure; счётчик не завис |
| D29 | registry daemon жив, durable pending есть | полный graceful ждёт join; deadline = failure с остатком; replay later ≠ ignore I/O |
| D30 | registry warning `send_message_sync` после WE `task_done` | sender не idle, пока daemon/handoff жив; join registry **до** sender stop |

---

## 8. Решения (бывшие O1–O9) и срезы code

| Было | Решение | Срез (не этот PR) |
|------|---------|-------------------|
| O1 API | Реестр на `WorkAdmission`; ожидание = `wait_accepted_executor_work` (**не** полный drain); WE stop = production sentinel + join | TASK-40 registry; later worker sentinel; helper |
| O2 `stop.set` vs drain | Request: seal→set как сейчас. Drain **после** `stop.wait()`, **до** PTB cleanup | изменение `run_ptb_lifecycle` |
| O3 sender | В полном graceful **обязателен**; rollback handoff при failure `call_soon`/put; intake seal вместе с idle | sender handoff + stop loop/thread/HTTP |
| O4 `delayed_cleanup` | Явное исключение: не ждать, не смешивать с registry I/O | — |
| O4b registry daemon | Полный graceful: учёт + join уже запущенных `we-registry-*` **до** sender stop; deadline при живом daemon = failure | отдельный code-срез |
| O5 dead worker | Failure + отчёт; без restart/handoff/retry | remainder report |
| O6 tick | После seal не звать; не wired | serve/schedules отдельно |
| O7 профили | Финальный список **после** PTB producers **и** Accepted executor **и** continuation; во время drain — живой registry | resource shutdown |
| O8 `app.stop` vs wait() | Сначала producers+work drain (terminal-сигнал, не голый wrap_future), потом registry join, WE sentinel, sender, PTB stop; cancel drain ≠ cancel Future | helper |
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
- TASK-24: PTB cleanup остаётся последним HTTP PTB; jobs/WE/registry join/sender вставляются **перед** ним.
