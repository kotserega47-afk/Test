# Контракт допуска новой работы isolated Antares (TASK-25)

| Мета | Значение |
|------|----------|
| **Статус** | review пройден (только документы; runtime не менялся; pytest не запускался; реализация — TASK-26; cutover-план **не** к исполнению) |
| **База** | закрытие TASK-24 `364976424b1818e37a76bc0b1d10cb254e4479de` (принятый review HEAD `34ef7af33cc112d369e0c9ee860d66954268ce8c`, Draft PR #27) |
| **Start/stop** | [MODULAR_REORG_ANTARES_STARTSTOP.md](MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **Lifecycle** | [MODULAR_REORG_ANTARES_LIFECYCLE.md](MODULAR_REORG_ANTARES_LIFECYCLE.md) |
| **Migration / JOB_ACCEPT** | [MODULAR_REORG_MIGRATION.md](MODULAR_REORG_MIGRATION.md) — рычаг cutover **не** этот PR |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Следующая зависимость **перед сервисом**: управляемый допуск новой работы и его закрытие при остановке. PTB `stop`/`shutdown` это **не** закрывают.

Runtime в TASK-25 **не** менять. Live polling, sender stop, worker join, executor shutdown **не** входят в этот PR и **не** входят в первый будущий code scope.

`JOB_ACCEPT` как production env-флаг **не** добавлять без отдельного cutover-контракта.

---

## 0. Что считается работой

Допуск **не** сводится к `JOB_REGISTRY` / `request_job`. Карта путей — § 1. Первый code scope защищает **один** путь (§ 7); остальные — обходы, пока не подключены.

| Класс | Примеры | Сейчас | Первый code |
|-------|---------|--------|-------------|
| Постановка JOB_REGISTRY | `/run_*`, `/operator_wallets_ready`, `/wallet_editor_refresh` | `run_job_async` → `dispatch_job_async` → `request_job` | только `/run_wallet` |
| Прямая registry/Auto-Enable | `/registry_replay`, `/registry_export`, `/auto_enable_plan`, `/auto_enable_run` | callback → executor / sync API | **не** защищены |
| Document ingest | `Document.ALL` | `queue.put` в `automation/worker.py` | **не** защищён |
| Будущие schedules | isolated loop (нет) | mixed: `dispatch_job_background` | **не** защищены |
| Внутренний re-enqueue | `enqueue_auto_enable_batch`; job → ещё dispatch/`add_task` | **новая** работа | **не** защищён (§ 7.2) |
| Read-only | `/whoami`, `/help`, `/start`, `/status`, `/registry_health`, `/rules_validate` | ACL + ответ | допуск не режет |
| Outbound sender | `telegram_bot.send_message_sync` | исходящая очередь | не точка приёма |

`/reload_rules` — побочный эффект snapshot; **не** первый code.

Conversion bridge → `add_task` — mixed/Raccoon; isolated **не** меняет.

---

## 1. Карта вызовов (исходники на закрытии TASK-24)

Поступление update (будущий serve/polling; sandbox — `update_queue`):

```text
PTB Updater / update_queue → Application._update_fetcher → handler callback
```

Handlers: `modules/antares/handlers.py` `get_antares_handlers()`; ingest: `modules/antares/document_ingest.py`.

### 1.1 JOB_REGISTRY (Telegram)

| Шаг | Файл | Символ |
|-----|------|--------|
| CommandHandler | `handlers.py` | `cmd_run_wallet` и др. |
| ACL | `handlers.py` | `_run_antares_command` → `guard_or_deny` |
| Reply «Запускаю» | `core/tg_command_dispatch.py` | `run_job_async` L35 **до** dispatch |
| Async dispatch | `core/job_dispatch.py` | `dispatch_job_async` L111–128 |
| Фактический submit | CPython `loop.run_in_executor` | **синхронный** `executor.submit` **до** `await` Future |
| Тело | `core/job_runner.py` | `request_job` L178 |

`dispatch_job_async` при `JOB_DISPATCH_VIA_EXECUTOR` включён (default): `await loop.run_in_executor(get_job_executor(), lambda: request_job(...))`. `run_in_executor` — **не** coroutine: он сразу делает `executor.submit`, затем возвращает awaitable. Обёртка `async def admit(): await dispatch_job_async(...)` **не** держит lock на момент submit: lock уже отпущен, либо (если lock вокруг `await dispatch_job_async`) lock живёт на **всё** выполнение `request_job` — запрещено § 4.

Ветка `JOB_DISPATCH_VIA_EXECUTOR=0`: `run_in_executor(None, ...)` — submit в default executor loop. Mixed не менять. Первый isolated путь **не** использует эту обёртку как gate.

`request_job` допуска не знает. `job_rejected_busy` / unknown type ≠ sealed.

### 1.2 Прямые операции (без `request_job`)

| Команда | После ACL | Единица постановки (будущая) |
|---------|-----------|------------------------------|
| `/registry_health` | `build_registry_health_report` | нет (read-only) |
| `/registry_replay` | `replay_pending_outbox_records` (sync в callback) | вход в replay **после** короткого admit; **не** держать lock на весь replay |
| `/registry_export` | `loop.run_in_executor(None, build_registry_export_from_postgres)` | **submit** default/явного executor, не «весь export» |
| `/auto_enable_plan` | `run_in_executor(..., run_auto_enable_plan)` | **submit** executor |
| `/auto_enable_run` | `run_in_executor(..., run_auto_enable)` → позже `enqueue_auto_enable_batch` | submit plan/run **и отдельно** worker enqueue (§ 7.2) |
| `/reload_rules` | invalidate + force snapshot | вход в invalidate |

Сейчас replies «Replaying…» / «Building export…» идут **до** постановки и **без** допуска.

### 1.3 Document ingest

```text
handle_wallet_editor_document
  allowlist / operator / routing
  reply «Файл получен»          ← await, до очереди
  get_file + download_to_drive  ← await
  add_task / add_add_wallet_task / add_edit_wallet_task
    _ensure_profile_worker
    worker.queue.put            ← фактическая постановка (blocking put; очередь unbounded)
```

Будущая единица: `queue.put_nowait` (или `put` на unbounded — тот же non-wait шаг) **под коротким lock** вместе с admit. Не держать lock на reply/`get_file`.

### 1.4 Mixed schedules

`scheduler.py` `schedule_loop` → `dispatch_job_background` → `get_job_executor().submit(request_job, ...)` (прямой submit, не `run_in_executor`). Isolated loop **нет**.

### 1.5 Уже принятая работа

| Поток | Где | Stop API |
|-------|-----|----------|
| JOB_REGISTRY | `_RUNNING` + file lock | нет cancel |
| Job executor | `_JOB_EXECUTOR` | только test reset |
| WE worker | daemon + `Queue` | нет |
| Sender | `telegram_bot` queue | нет |
| PTB callbacks | `concurrent_updates` tasks | `app.stop`; не jobs |

---

## 2. Владелец и состояния (не выводить из «нет bind»)

Владелец объекта: **isolated lifecycle caller** (будущий serve / обвязка вокруг `run_ptb_lifecycle`). Создаёт экземпляр `modules.antares.work_admission.WorkAdmission` и вызывает `bind` **до** `initialize`/`start`.

Режим **не** вычисляется как «если объект не привязан — значит mixed». Четыре явных состояния:

| Состояние | Кто | Новая работа (подключённые пути) |
|-----------|-----|----------------------------------|
| **unbound** | mixed; тесты без isolated bind; `boot`/`run` без bind | допуск **не** участвует; поведение как сейчас |
| **bound/closed** | isolated после `bind`, до успешного `open` | **отказ**; не unbound |
| **open** | isolated после успешного `app.start` + `open()` | приём по подключённым путям |
| **sealed** | isolated после `seal()` | отказ навсегда для этого запуска |

Смысл **bound/closed**: ошибка initialize/start **не** оставляет процесс в unbound. Callbacks/handlers, если как-то вызваны, на подключённом пути получают отказ, а не «как mixed».

### 2.1 Переходы

```text
unbound --bind(obj)--> bound/closed --open()--> open --seal()--> sealed
bound/closed --seal()--> sealed
open --seal()--> sealed
sealed --seal()--> sealed          (идемпотентно)
```

| Вызов | Разрешён | Иначе |
|-------|----------|--------|
| `bind` | только из **unbound** | повторный bind — ошибка; не «тихо unbound» |
| `open` | только **bound/closed** | из unbound / open / sealed — ошибка; **не** открывать sealed |
| `seal` | bound/closed, open, sealed | из unbound — ошибка (mixed не seal'ит) |

`boot` / диагностический `run` **не** bind'ят.

### 2.2 Кто владеет экземпляром

- Один экземпляр на isolated-процесс, поле caller'а + `bind` в модуль (чтобы handlers видели тот же объект).
- Mixed **никогда** не вызывает `bind`.
- Снимать bind в том же процессе после isolated — отдельный test helper; production isolated не «отвязывается» обратно в unbound.
- `core.job_runner` / `core.job_dispatch` **не** владельцы.

---

## 3. Четыре слоя

| Слой | Что | Допуск |
|------|-----|--------|
| A. Новые updates | polling / `put` в очередь PTB | STARTSTOP.md; **не** этот примитив |
| B. Начало callback | PTB отдал Update | не cancel handler |
| C. Постановка | submit / `put_nowait` / вход в прямую sync-операцию | § 4 |
| D. Уже принятое | Future после submit, `_RUNNING`, элемент в WE queue | не cancel в этом PR |

Подключённый путь закрывает **C**. A отдельно. Незаявленные пути остаются обходами.

---

## 4. Единица принятия: атомарный submit под коротким lock

**Выбранный механизм (первый code и дальнейшие постановки в очередь/executor):** не reservation-токен.

```text
with admission.lock:          # threading.Lock, только этот блок
    if state != open:
        return Rejected
    future = executor.submit(request_job, ...)   # синхронно
    return Accepted(future)
# lock отпущен
await asyncio.wrap_future(future)   # без lock; это уже D
```

**Принято** = `executor.submit` (или для очереди — `queue.put_nowait`) **вернулся внутри lock при state==open** без исключения.

Последствия Accepted:

- Ошибка последующего Telegram `reply` («Запускаю» / «Принято») **не** отменяет submit. Future — слой D; job может идти без ответа оператору. Логировать сбой reply отдельно, не `future.cancel()`.
- Исключение самого `submit` (отказ executor, TypeError callable) → **не** Accepted. Состояние допуска из‑за неудачной постановки не становится «принято»; caller видит ошибку постановки.
- `request_job` внутри Future после успешного submit — исполнение D, не повторное принятие.

Запрещено держать этот lock:

- через `await`;
- во время Telegram `reply_*` / `get_file` / download;
- на время всего `request_job`, registry export/replay, Auto-Enable.

### 4.1 Почему не обёртка вокруг `dispatch_job_async`

`await dispatch_job_async(...)` доходит до `loop.run_in_executor(...)`, где submit уже произошёл, и затем **ждёт весь** `request_job`. Lock вокруг этого `await` = lock на job. Lock только до вызова async-функции = окно до submit. Поэтому isolated первый путь **не** gate'ит `dispatch_job_async`; нужен синхронный `submit_job_if_open(...)` рядом с `get_job_executor()`.

`dispatch_job_background` уже делает голый `submit` — тот же примитив, когда schedules подключат.

### 4.2 Reservation (не первый code)

Токен «можно enqueue позже» **не** входит в первый этап.

Если появится (ingest: admit до `get_file`, put после): enqueue после `seal` тогда **явно** «завершение уже принятой работы», а **не** «enqueue после seal невозможен». Первый этап этого **не** обещает: после успешного `seal` новый `submit`/`put_nowait` на подключённом пути невозможен.

### 4.3 «Запускаю» (явная смена порядка на подключённом пути)

Сейчас (`run_job_async`): ACL → reply «Запускаю» → `dispatch_job_async` (submit + весь job) → «Принято».

На **первом isolated пути** (`/run_wallet`) порядок **меняется явно**:

1. ACL как сейчас (`guard_or_deny`). Deny — те же тексты; admit нет.
2. Короткий `submit_job_if_open`. Rejected → reply допуска («не принимаем новую работу»), **без** «Запускаю».
3. Accepted → reply «Запускаю» (смысл: submit уже случился; job может уже идти).
4. `await` Future **без** lock → «Принято» / ошибка выполнения как сейчас.

Mixed и остальные команды **сохраняют** старый `run_job_async` (Запускаю до dispatch).

Нельзя оставить «Запускаю» до submit на подключённом пути: seal может выиграть после сообщения.

### 4.4 Единицы по путям (справочник; защищён только § 7)

| Путь | Синхронная единица |
|------|-------------------|
| Isolated `/run_wallet` | `get_job_executor().submit(request_job, ...)` под lock |
| Другие `/run_*` / dispatch cmds | та же единица, **когда** подключат; сейчас обход через `run_job_async` |
| `dispatch_job_background` | уже `submit` в `job_dispatch.py` L84 |
| Ingest | `queue.put_nowait` после worker lookup; lookup/start thread **вне** admission lock или отдельно кратко; **не** первый code |
| Export / auto_enable_plan | `executor.submit` callable; не весь callable под lock |
| `/registry_replay` | admit+вход в `replay_pending_outbox_records` без lock на тело |
| `enqueue_auto_enable_batch` | внутренняя постановка в worker queue — § 7.2 |

---

## 5. Ответы при отказе допуска

Не менять ACL и не подменять `job_rejected_busy` / unknown / ingest allowlist.

На подключённом пути: ACL → admit. Отказ bound/closed или sealed — одно семейство «не принимаем» (текст code PR), лог `admission_closed` / `admission_sealed`, не `access_denied`.

Read-only не режется.

Нет обещания отмены running jobs.

---

## 6. Mixed и env

Unbound: `run_polling` + `schedule_loop` без изменений.

Не вводить `JOB_ACCEPT` env в первом code. `JOB_DISPATCH_VIA_EXECUTOR` не есть допуск.

---

## 7. Первый code scope (не этот PR)

**Примитив + один путь**, не все commands/ingest/прямые операции сразу.

1. `WorkAdmission`: unbound / bound/closed / open / sealed; `bind` / `open` / `seal` / `submit_job_if_open` под коротким lock.
2. Isolated bind **до** `initialize`. `open()` сразу после успешного `app.start`, до кормления updates. `boot`/`run` без bind.
3. Подключить **только** `cmd_run_wallet` (не весь `_run_antares_command`).
4. Не менять `request_job`, mixed `scheduler.py`, `telegram_bot`, `automation.worker` API, остальные handlers, ingest.
5. Не live polling, sender stop, worker join, executor shutdown.

Успех первого этапа **не** есть глобальный запрет новой работы в процессе.

### 7.1 Ещё не защищены (обходы)

- `/run_hourly`, `/run_download`, `/run_rate`, `/operator_wallets_ready`, `/wallet_editor_refresh` — `run_job_async`
- `/registry_replay`, `/registry_export`, `/auto_enable_plan`, `/auto_enable_run`, `/reload_rules`
- document ingest → `queue.put`
- mixed `schedule_loop` / `dispatch_job_background`
- conversion bridge `add_task`
- внутренний re-enqueue § 7.2
- прямой вызов `dispatch_job_async` / `request_job` / `get_job_executor().submit` в обход `submit_job_if_open`

### 7.2 Внутренние постановки — следующий этап

`run_auto_enable` → `enqueue_auto_enable_batch` (`integrations/wallet_editor_auto_enable.py` ≈ L512, `automation/worker.py`) ставит карточки в WE queue **после** того, как Telegram-вход (даже будущий submit `run_auto_enable`) уже принят.

Первый этап **не** перекрывает этот re-enqueue. Не писать «sealed ⇒ никакой новой работы в процессе».

Следующий этап (не сейчас): либо `put_nowait` под тем же admission в worker helpers (меняет shared worker — смешает mixed, нужна осторожность), либо isolated-обёртка только для Antares auto-enable enqueue.

---

## 8. Начало остановки vs `stop.set()`

`asyncio.Event.set()` **сам не** закрывает допуск. Другая задача может `submit_job_if_open` между `set()` и поздним `seal()`.

**Единый запрос остановки** (имя code PR; контракт обязателен):

Порядок: **сначала `seal()`, потом `stop.set()`**. `Event.set()` вызывать **в потоке event loop**, которому принадлежит `stop` (тот же loop, что `run_ptb_lifecycle` / `stop.wait()`).

Из потока loop (sandbox после callback; signal handler, если loop его принимает):

```text
admission.seal()    # любой поток: короткий lock
stop.set()          # только loop-thread
```

Из **другого** потока (worker, signal на Windows без `add_signal_handler`):

```text
admission.seal()    # sync в этом потоке — допуск закрыт сразу
loop.call_soon_threadsafe(stop.set)   # не stop.set() напрямую
```

`seal()` до `call_soon_threadsafe`: окно до пробуждения helper уже **не** принимает новую работу на подключённых путях. Не `set()` без предшествующего `seal`. Не считать `call_soon_threadsafe` заменой seal.

`run_ptb_lifecycle` **не** считает `stop.wait()` возврат заменой seal. Если helper догоняет stop без предшествующего seal — это нарушение caller'а; helper на всякий случай `seal()` **синхронно в начале except/finally до первого await cleanup** (идемпотентно).

### 8.1 Cancellation и ошибка

| Момент | Состояние | Уже принято |
|--------|-----------|-------------|
| `bind` выполнен, `initialize`/`start` падает | **bound/closed**, затем `seal()` в cleanup path (→ sealed) | submit не должно было быть (`open` не было) |
| `open` был, `CancelledError` на `stop.wait()` / mid-lifecycle | `seal()` **до** `await` cleanup | Future, чей `submit` уже вернул Accepted |
| `open` был, штатный `request_antares_stop` | sealed до `set()` | то же |
| `open` не вызывался (bind есть) | не unbound; closed затем sealed | нет |

После seal подключённый `submit_job_if_open` — Rejected. Уже возвращённый `Future` — слой D, выполняется. Нет cancel Future/job.

---

## 9. Тестовый контракт (будущий code; не этот PR)

Детерминированные `threading.Event` / barrier. Проверять **реальный** `executor.submit` (mock/wrapper на `get_job_executor()`, не «вызван async dispatch»).

| Сценарий | Ожидание |
|----------|----------|
| **Seal победил** | barrier: `seal` входит в lock первым; `submit` не вызван; Rejected; нет `request_job` |
| **Принятие победило** | `submit` вернул Future внутри lock до seal; seal после; Future имеет право выполнить `request_job` **после** seal (это D, не обход) |
| bound/closed, без `open` | Rejected; submit нет |
| bind + ошибка start | состояние не unbound; submit нет |
| mixed / unbound | `dispatch_job_async` / `run_job_async` как сейчас; admission no-op |
| `/run_wallet` open | submit + (после lock) «Запускаю»; нет «Запускаю» при Rejected |
| submit бросил | не Accepted; Future нет; sealed/open как до вызова |
| Accepted, затем reply упал | Future жив; job не отменяют |
| `/run_hourly` (первый этап) | **без** gate — документированный обход, тест что путь ещё не через `submit_job_if_open` |

Не sleep-as-sync. Не обещать, что hourly/ingest/auto-enable batch закрыты.

---

## 10. Зависимости отдельно

| Тема | Почему отдельно |
|------|-----------------|
| Остальные TG-команды, ingest, прямые ops | не первый code |
| Внутренний Auto-Enable enqueue | § 7.2 |
| Live polling / serve | STARTSTOP.md |
| Sender stop / worker join / executor shutdown | нет production API |
| Reservation + download-then-put | меняет обещание «нет enqueue после seal» |
| `JOB_ACCEPT` env cutover | другой процесс |

---

## 11. Вне скоупа TASK-25

Реализация runtime; pytest этого docs PR; live Telegram; mixed gate; Railway; merge; исходное дерево Test; закрытие TASK-25.
