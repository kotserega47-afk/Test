# Контракт initialize / start / остановки isolated Antares (TASK-23)

| Мета | Значение |
|------|----------|
| **Статус** | review пройден (только документы; runtime не менялся; pytest не запускался; сервисный lifecycle не реализован) |
| **База** | закрытие TASK-22 `8a3fd5a2fb6d4738997b5463bebd8392e8db4f90` (принятый review HEAD `d9592ff0a04480433d18455726ca7f66f76d09bf`) |
| **Application build-only** | [MODULAR_REORG_ANTARES_APPLICATION.md](MODULAR_REORG_ANTARES_APPLICATION.md) |
| **Lifecycle обзор** | [MODULAR_REORG_ANTARES_LIFECYCLE.md](MODULAR_REORG_ANTARES_LIFECYCLE.md) |
| **PTB** | **22.8** (Cursor Python 3.12.10); `requirements.txt` `>=20.7`, не pin. Railway **UNKNOWN** |
| **job-queue extra** | на Cursor 3.12.10 **нет** (`APS_AVAILABLE is False`); builder даёт `job_queue=None`. Ветка extra **не** в scope первого sandbox и **не** заявляется проверенной |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Runtime в этом PR **не** менять. Boot и диагностический `run` TASK-22 **сохраняются**. Публичный `serve` и live polling в следующий code scope **не** входят. Pytest **не** требуется.

---

## 0. Единственный порядок (везде в этом документе)

**Запуск**

1. `await app.initialize()`
2. `await app.start()`
3. `await app.updater.start_polling(...)` — **только будущий сервис**. Первый sandbox-подэтап этот шаг **не** вызывает.

Это **намеренно не** порядок `run_polling` (там polling **до** `start`). Isolated не копирует `__run`.

**Остановка**

1. Остановить поступление updates (не класть в очередь; polling не кормит getUpdates).
2. `await app.updater.stop()` — **если** updater запущен (`updater.running`).
3. `await app.stop()` — **если** Application запущен (`app.running`).
4. `await app.shutdown()` — если Application дошёл до `_initialized`; иначе staged cleanup § 6, **не** один `Bot.shutdown` на все компоненты.

Пропуск шага, который не стартовал, обязателен. Ошибка шага **не** отменяет оставшиеся доступные шаги и **не** затирает исходное исключение (§ 6.1).

---

## 1. Выбор: ручной async helper, не `run_polling`

Единственный механизм isolated Antares — helper § 2. **Не** `Application.run_polling` / `run_webhook` / `stop_running`. Mixed (`scheduler.py` L365) остаётся на `run_polling(close_loop=False)`; isolated его **не** копирует.

| | `run_polling` / `__run` | Isolated helper |
|--|-------------------------|-----------------|
| Порядок | initialize → **polling** → start | initialize → **start** → polling (polling только serve) |
| Loop | `__run` / `run_forever` | caller `asyncio.run`; helper не создаёт второй loop |
| `stop_running` | заточен под `run_forever` | **не** использовать; стоп через `stop` Event |
| Sandbox | live getUpdates или подмена `__run` | тот же helper, `enable_polling=False`, очередь без Telegram |

---

## 2. Production helper (его же исполняет sandbox)

Будущий модуль (имя файла code PR может уточнить, контракт нет): `modules.antares.application_lifecycle`.

```text
async def run_ptb_lifecycle(
    app: Application,
    *,
    stop: asyncio.Event,
    enable_polling: bool = False,
) -> None
```

| | Контракт |
|--|----------|
| Кто владеет `Application` | **Caller**: собрал token, snapshot, `build()`, `add_handler` (как TASK-22). Helper Application **не** строит и **не** вешает handlers |
| Кто владеет loop | **Caller** (`asyncio.run`). Helper только `await` на текущем loop |
| Запрос остановки | `stop.set()` с того же loop (sandbox после наблюдаемого callback; будущий serve — отдельная обвязка сигналов) |
| `enable_polling` | sandbox / первый code: **`False`**. `True` только будущий публичный serve, **не** этот code scope |
| Что helper делает | порядок § 0; ждёт `stop`; finally — остановка § 0 |
| Чего нет | argv, dotenv, assemble, snapshot, `run_polling`, sender/worker/schedules, смена mixed gate |

Sandbox **обязан** вызывать этот helper (после того же build+handlers, что production). Отдельный lifecycle только в test runner **не** засчитывается: он не подтверждает путь приложения.

Boot и `run` helper **не** вызывают.

Публичный `serve` и `enable_polling=True` — **не** следующий code.

---

## 3. Event loop и сигналы (раздельно)

| Механизм | Что это | Первый sandbox | Будущий serve |
|----------|---------|----------------|---------------|
| Stop event | `stop.set()` → helper выходит из `wait` в штатный finally | **да**, основной путь | да |
| Cancellation главной async-задачи | `CancelledError` на `stop.wait()` / mid-start; срабатывает `finally` helper | **да**, отдельный сценарий | да |
| SIGINT | обычно `KeyboardInterrupt` / отмена task в `asyncio.run` | **не** контракт первого подэтапа | определить в serve-PR |
| SIGTERM | `asyncio.run` **сам не** ставит обработчик SIGTERM | **не** в sandbox | Unix: `loop.add_signal_handler(SIGTERM, stop.set)` если loop это умеет. Windows ProactorEventLoop: `add_signal_handler` **нет** (как warning mixed). Не обещать SIGTERM на Windows без отдельной проверки |

Не писать «`asyncio.run` обрабатывает SIGTERM».

---

## 4. Initialize по стадиям (PTB 22.8)

`Application.initialize` (`_application.py` ~470–511), без persistence:

1. `await self.bot.initialize()`
2. `await self._update_processor.initialize()`
3. `await self.updater.initialize()` (снова `Bot.initialize`, уже idempotent)
4. `self._initialized = True`

`Bot.initialize` (`_bot.py` ~843–868):

1. Если ещё нет: `HTTPXRequest.initialize` на обоих request → **`_requests_initialized = True`**
2. `await self.get_me()` → `_bot_initialized = True`. InvalidToken перехватывается здесь.

`HTTPXRequest.initialize` пересоздаёт client, только если он уже `closed`; иначе почти no-op. Клиенты **созданы на `build()`**.

`Application.shutdown` no-op, пока `_initialized` ложно. Он **не** вызывается на промежуточных стадиях сам. **`Bot.shutdown` не очищает processor и Updater.**

| Стадия отказа | Флаги | Cleanup helper (доступные шаги, по порядку § 0 затем staged) |
|---------------|-------|--------------------------------------------------------------|
| 1. Ошибка инициализации **requests** (`gather` initialize упал **до** `_requests_initialized`) | Bot requests flag ложь; Application не initialized; updater нет | `updater.stop`/`app.stop`/`app.shutdown` не применимы. `Bot.shutdown` **no-op**. Idle httpx с `build()` этим **не** закрыть. Не называть `Bot.shutdown` очисткой. Диагностика: процесс exit; сервисный helper: записать, что request shutdown не через Bot API |
| 2. Ошибка **`get_me`** (сеть/InvalidToken) **после** `_requests_initialized` | requests да; `_bot_initialized` нет; Application/updater/processor initialize дальше **не** шли | `await app.bot.shutdown()` → `HTTPXRequest.shutdown` → `AsyncClient.aclose`. **Не** `Application.shutdown()` (no-op). Processor/Updater не инициализированы — их shutdown не звать «за компанию» |
| 3. `Bot.initialize` **успешен**, отказ **до** `Application._initialized` (processor или `Updater.initialize`) | Bot полностью initialized; Application flag ложь; updater/processor **частично** | `Application.shutdown` всё ещё **no-op** — **не** закроет processor. Сделать: если `updater.running` (не должно); если `updater._initialized` — `updater.shutdown` (он зовёт `Bot.shutdown`); иначе `Bot.shutdown`; если processor.initialize уже вернул успех — **отдельный** `await app._update_processor.shutdown()`, не считать это сделанным `Bot.shutdown`. Не обобщать один вызов на все компоненты |

Имена `_initialized` внутренние PTB; helper в code может опираться на публичные `running` и на факт, что `shutdown`/`stop` бросают или no-op — но контракт cleanup **стадийный**, не «всегда Bot.shutdown».

---

## 5. Очередь, callbacks, ошибки

### 5.1 Как проверять start/stop (обязательно)

Последовательность событий sandbox (тот же helper, `enable_polling=False`):

1. Caller: build + handlers (TASK-22 путь).
2. Зарегистрировать **наблюдаемый** error handler на этом `app` (до helper).
3. `await helper` доходит до `start` (fetcher читает `app.update_queue`).
4. Положить **один** синтетический `Update` в `app.update_queue` (не `process_update`).
5. **Дождаться** наблюдаемого завершения callback (harness-событие / Event), не таймаут «наверное обработалось».
6. Остановить поступление (больше не `put`).
7. `stop.set()` → helper: updater.stop пропускается → `app.stop` → `app.shutdown`.

`await app.process_update(update)` — **отдельная** проверка handlers без fetcher. Она **не** доказывает `_update_fetcher`, очередь, `task_done`, `concurrent_updates`.

### 5.2 Ошибки callback

`process_update` ловит Exception из blocking handler и зовёт `process_error` (`_application.py` ~1324–1327, 1249–1250). Успешный **возврат** `process_update` (нет raise наружу) **не** есть успех callback: ошибка могла уйти в error handler.

Контракт проверки:

- Положительный путь: событие «callback completed» у выбранного handler.
- Отрицательный путь (инъекция raise в callback): событие error handler с тем же типом; **и** явная проверка, что бизнес-успех (diag line / «handler ok») **отсутствует**.
- Не использовать «`process_update` не бросил» как pass.

### 5.3 Синтетический Update

Безопасный выбор: private `/whoami` (`cmd_whoami`) — access из sandbox xlsx, **без** `request_job`, **без** import `telegram_bot`, **без** document ingest.

`reply_text` → Telegram HTTP: на границе `HTTPXRequest.do_request` синтетический **успешный** JSON для `getMe` (initialize) и для `sendMessage` (ответ whoami). Прочие method/url — запись попытки и отказ. Это не live Telegram.

Не использовать `/run_*`, `/status`, `/registry_*`, Document.ALL как первый lifecycle-update.

Jobs, sender, worker, Dropbox, PG, Playwright, живая сеть — запрещены.

### 5.4 Уже в очереди vs запрет новых (PTB 22.8)

Исходники: `stop` кладёт `_STOP_SIGNAL` и `await update_queue.join()` (`_application.py` ~669–685). `__update_fetcher` читает FIFO, пока не увидит `_STOP_SIGNAL`, затем **return** (~1210–1217). `_update_fetcher` **finally** снимает хвост через `get_nowait` с логом `Dropping pending update` (~1234–1240). Докстринг `stop`: после вызова updates из очереди больше не fetch'атся, даже если очередь не пуста (~645–647). Concurrent path (`concurrent_updates=True` → 256): handler уходит в `create_task`; `stop` потом `gather` `__create_task_tasks`.

Контракт:

- **Новые поступления:** caller/helper прекращают `put` и не включают polling **до** `app.stop`. Это запрет **новых** входов, не свойство FIFO.
- **Уже стоявшие до `_STOP_SIGNAL`:** реализация может обработать их до сигнала или дропнуть хвост в `finally`. **Не** обещать ни потерю, ни полное завершение хвоста без отдельной проверки.
- Sandbox поэтому: один update, дождаться callback, **потом** stop — не опираться на drain-during-stop.

---

## 6. Cleanup: не глотать исходную ошибку, не пропускать шаги

### 6.1 Правило finally

```text
primary = исключение тела (initialize/start/polling/wait)
для каждого доступного шага остановки § 0 / § 4:
    try: шаг
    except: запомнить рядом с primary, продолжить
в конце: если primary — выбросить его; cleanup-ошибки в __context__ / ExceptionGroup
не подменять primary ошибкой cleanup
не skip оставшийся шаг из-за сбоя предыдущего cleanup
```

`app.stop()` при `not running` бросает RuntimeError — поэтому «если запущен». То же `updater.stop`.

### 6.2 Таблица частичных отказов (порядок § 0)

| Отказ | Уже есть | Cleanup (доступное) | Не делать |
|-------|----------|---------------------|-----------|
| assemble / snapshot / build / add_handler | TASK-22 | процесс exit; helper нет | initialize ради cleanup |
| initialize, стадия requests | Application в памяти, requests flag ложь | § 4 стадия 1 | `Bot.shutdown` как будто закроет httpx |
| initialize, стадия get_me | requests да | `Bot.shutdown`; не `app.shutdown` | `run_polling`; повтор initialize без shutdown requests |
| initialize, после успешного Bot, до Application flag | Bot да; processor/updater частично | § 4 стадия 3 | один `Bot.shutdown` «за всё» |
| `start` падает (например после JobQueue.start, если extra когда-либо будет) | `running` сброшен в except start | JobQueue.stop **только если extra и scheduler.running** (сейчас extra нет — не заявлять); `app.shutdown` если `_initialized` | `app.stop()` при not running |
| `start` успешен, `start_polling` падает | fetcher жив; updater может быть running | поступление стоп → updater.stop если running → app.stop → app.shutdown | оставить polling; **не в sandbox** (polling нет) |
| штатный stop после callback | start без polling | поступление стоп → skip updater.stop → app.stop → app.shutdown | process kill |
| callback бросил | PTB process_error | не PTB-stop jobs/sender/worker (их нет в успехе sandbox) | считать возврат process_update успехом |

---

## 7. JobQueue extra

Сейчас extra **нет**. Первый sandbox **не** проверяет и **не** обещает `JobQueue.start`/`stop`.

Если extra **войдёт в scope** отдельным решением (не этот docs PR, не молча): окружение с установленным `[job-queue]`; тот же helper; наблюдать `JobQueue.start` на `app.start` и `JobQueue.stop(wait=True)` на `app.stop`; по-прежнему не класть Antares jobs в PTB scheduler; `JOB_REGISTRY` отдельно. Пока extra нет — эта строка N/A.

`requirements.txt` не менять.

---

## 8. Первый update vs бизнес-эффекты

Нет `schedule_loop` ≠ нет действий. `/run_*`, `/status`, registry, Document.ALL могут поднять jobs, sender, worker, outbox, `get_file`. Для lifecycle-проверки выбран `/whoami` (§ 5.3). Успех sandbox **не** разрешение сервиса и не cutover.

---

## 9. argv

| argv | Поведение | Когда |
|------|-----------|--------|
| нет / `boot` | TASK-18 | сохранить |
| `run` | TASK-22 диагностика, **exit 0**, без helper/initialize | сохранить; не превращать в сервис |
| будущий `serve` | caller + helper `enable_polling=True` | **не** следующий code |
| иное | отказ | сохранить |

---

## 10. Минимальный следующий code (не этот PR)

1. Добавить helper § 2. Не дублировать lifecycle в pytest runner.
2. Сохранить `boot` / `run` без helper.
3. Не `run_polling`, не `start_polling`, не публичный serve.
4. Sandbox subprocess: тот же build+handlers; `do_request` синтетический getMe (+ sendMessage для whoami); helper `enable_polling=False`; очередь `/whoami`; ждать callback; `stop.set()`.
5. Отдельные сценарии: отказ initialize по стадиям § 4; cancellation; callback raise → error handler; прямой `process_update` **не** вместо очереди.
6. Запрет jobs/sender/worker/SDK/сети кроме разрешённого синтетического do_request.
7. JobQueue extra не проверять, пока extra нет.

---

## 11. Graceful shutdown сервиса — позже

Нет `JOB_ACCEPT`, stop sender, join worker, production executor shutdown. PTB stop это не закрывает. Допуск новой работы — [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) (TASK-25). Remote/stale, token, locks, cutover — отдельные решения.

---

## 12. Вне скоупа

Live Telegram; рабочий token; смена `run`; `run_polling`; mixed gate; scheduler; profiles; requirements; Railway; STATE_DIR; merge; исходное Test; реализация helper в этом docs PR.

---

## 13. Оставшиеся вопросы

1. Точное имя модуля/функции helper — code PR, контракт § 2.
2. Имя argv serve — не сейчас.
3. Как именно Windows-serve увидит SIGTERM — serve-PR.
4. Railway PTB / extra — UNKNOWN.
5. Поведение хвоста очереди при stop — не обещать без отдельного теста.
