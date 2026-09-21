# Контракт initialize / start / остановки isolated Antares (TASK-23)

| Мета | Значение |
|------|----------|
| **Статус** | PROPOSED (только документы; runtime не менялся; к review, не «review пройден») |
| **База** | закрытие TASK-22 `8a3fd5a2fb6d4738997b5463bebd8392e8db4f90` (принятый review HEAD `d9592ff0a04480433d18455726ca7f66f76d09bf`) |
| **Application build-only** | [MODULAR_REORG_ANTARES_APPLICATION.md](MODULAR_REORG_ANTARES_APPLICATION.md) |
| **Lifecycle обзор** | [MODULAR_REORG_ANTARES_LIFECYCLE.md](MODULAR_REORG_ANTARES_LIFECYCLE.md) |
| **PTB** | **22.8** (Cursor Python 3.12.10); `requirements.txt` `>=20.7`, не pin. Railway **UNKNOWN** |
| **job-queue extra** | на Cursor 3.12.10 **нет** (`APS_AVAILABLE is False`); builder даёт `job_queue=None` |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Runtime, mixed `scheduler.py`, профили, `requirements.txt`, Railway, workbook/routes и `STATE_DIR` в этом PR **не** менять. Boot и диагностический `run` TASK-22 **сохраняются**. Pytest **не** требуется.

---

## 1. Выбор lifecycle: ручной async, не `run_polling`

**Единственный механизм isolated Antares:** явные `await app.initialize()`, `await app.start()`, при живом сервисе отдельно `await app.updater.start_polling(…)`, остановка в обратном порядке `updater.stop` → `app.stop` → `app.shutdown`. Event loop принадлежит isolated-коду (`asyncio.run` / созданный loop), не `Application.__run`.

**Не** вызывать `Application.run_polling` / `run_webhook` / `stop_running` в isolated процессе. Два механизма не смешивать.

Обоснование по исходникам PTB 22.8 (`telegram/ext/_application.py`):

| | `run_polling` / `__run` | Ручной async |
|--|-------------------------|--------------|
| Сеть getUpdates | всегда `Updater.start_polling` до `start()` | polling — отдельный шаг; sandbox может обойтись без него |
| Loop | `__run` берёт/создаёт loop, `run_forever`, опционально `close_loop` | владелец — наш `asyncio.run` |
| `stop_running` | заточен под `run_forever` (докстринг: custom lifecycle «not guaranteed») | не использовать |
| Частичный `initialize` | `finally` зовёт `Application.shutdown()`, который **no-op**, если `_initialized` ещё ложно | явный `try/finally` с `Bot.shutdown`, см. § 4 |
| Windows | `add_signal_handler` на ProactorEventLoop **нет** (mixed это уже обходит warning'ом) | SIGINT → `KeyboardInterrupt` на `asyncio.run`; не обещать POSIX signal handlers |
| Тесты | live polling или полная подмена `__run` | `process_update` / очередь без getUpdates |

Mixed (`scheduler.py` L365) остаётся на `run_polling(close_loop=False)`. Isolated **не** копирует mixed `main()`.

---

## 2. Кто владеет event loop

- Diagnostic `boot` / `run`: loop **не** нужен (синхронный `build`/`add_handler`).
- Будущий сервис и sandbox lifecycle: **один** loop, созданный isolated-кодом. PTB callbacks (`process_update`, `start`, `stop`) исполняются на нём.
- `ThreadPoolExecutor` jobs, sender thread, WE worker — **другие** потоки; PTB `stop` их **не** останавливает.
- `close_loop`: в ручном пути закрывать loop только если мы его создали и больше не используем (`asyncio.run` делает это сам). Не звать `Application.run_polling(..., close_loop=…)`.

---

## 3. Порядок start и обратной остановки (PTB 22.8)

### 3.1 Start (ручной)

1. Уже есть: boot-prefix + local snapshot + `Application.build()` + `assembled.handlers` (TASK-22).
2. `await app.initialize()`: `Bot.initialize` (`HTTPXRequest.initialize` + **`get_me`**) → `update_processor.initialize` → `Updater.initialize` (повторный `Bot.initialize`, idempotent) → `_initialized = True`. Persistence в isolated **нет**.
3. Живой сервис **только**: `await app.updater.start_polling(...)` — getUpdates в очередь. **Не** в первом code и **не** в диагностическом `run`.
4. `await app.start()`: если `job_queue` задан — `JobQueue.start()`; иначе skip; затем task `_update_fetcher` из `update_queue` → `process_update`. **Не** открывает Telegram HTTP сам по себе.

`get_me` = первая авторизация token. Успех `build()` этим не является.

### 3.2 Stop (ручной, обратный)

Если polling был запущен: `await app.updater.stop()` **до** `app.stop()` (как в `__run` finally: updater.stop, затем Application.stop, затем shutdown).

Затем:

1. `await app.stop()` — если `running`: в очередь `_STOP_SIGNAL`, `join` текущих, `JobQueue.stop(wait=True)` при наличии, `gather` задач `create_task`. Докстринг: после вызова **новые** элементы из очереди не забираются, даже если очередь не пуста (кладётся stop-сигнал).
2. `await app.shutdown()` — только если `_initialized`: `Bot.shutdown` (`HTTPXRequest.shutdown` → `AsyncClient.aclose()`), processor, `Updater.shutdown` (ещё раз `Bot.shutdown`, уже no-op). Если `_initialized` ложно — **return без aclose**.
3. Isolated **не** обещает stop sender / worker / executor / `schedule_loop` (API нет).

SIGINT/SIGTERM: в ручном `asyncio.run` типично `KeyboardInterrupt` / отмена task. На Windows `loop.add_signal_handler` **не** контракт. После прерывания — тот же finally, что и штатный stop. `SystemExit` из PTB `_raise_system_exit` — только если кто-то поставил signal handlers как `__run`.

Ошибки Telegram на getUpdates: у `run_polling` есть `error_callback` → `process_error(update=None)`. В ручном polling тот же callback нужно повесить самим, когда (позже) включат `start_polling`. InvalidToken / сеть на `get_me` — отказ initialize, не polling.

Отмена (`CancelledError`): не глотать без stop/shutdown; после частичного start — § 4.

---

## 4. Частичные отказы и cleanup

`Application.shutdown()` **не** закрывает httpx, пока `_initialized` ложно. `Bot.initialize` ставит `_requests_initialized` **до** `get_me`. Если `get_me` падает, `Application._initialized` остаётся False, но клиенты уже созданы/«initialized».

| Этап отказа | Уже есть | Cleanup | Не делать |
|-------------|---------|---------|-----------|
| gate / assemble / snapshot / build / add_handler | как TASK-22 | процесс exit (диагностика) | initialize ради shutdown |
| `initialize`: ошибка **до** `_requests_initialized` | Application в памяти | `Application.shutdown` no-op; процесс/finally | `get_me` retry как success |
| `initialize`: `get_me` / InvalidToken / сеть **после** `_requests_initialized` | Bot requests «открыты», `Application._initialized` False | **`await app.bot.shutdown()`** (это вызывает `HTTPXRequest.shutdown` → `AsyncClient.aclose`). Не полагаться на `Application.shutdown()` | `run_polling`; initialize повторно без shutdown |
| `initialize` успешен, `start` падает после `JobQueue.start` | scheduler может быть running, `Application.running` сброшен в `except` | `JobQueue.stop` если extra есть и scheduler.running; затем `Application.shutdown` | `stop()` — бросит «not running» |
| `start` успешен, `start_polling` падает | fetcher жив, updater может быть running | `updater.stop` если `updater.running`; `app.stop`; `app.shutdown` | оставить polling |
| polling/start живы, ошибка handler | update в обработке | PTB `process_error`; jobs/worker/sender — **свои** потоки, PTB stop их не снимает | считать PTB stop полным stop сервиса |
| штатный выход сервиса | всё PTB running | updater.stop → app.stop → app.shutdown | process kill как «graceful» |

Нет метода `HTTPXRequest.aclose()`. Есть `HTTPXRequest.shutdown()` → внутренний `client.aclose()`.

---

## 5. Очередь updates и активные callbacks

- `start()` читает `update_queue` и зовёт `process_update` (в т.ч. concurrent_updates=256, как mixed).
- Sandbox может класть синтетический `Update` в очередь **или** звать `await app.process_update(update)` без fetcher. Для проверки handlers достаточно `process_update` после `initialize` (initialize нужен, если handler ходит в `context.bot`; часть handlers делает `reply_text` / `get_file` — это снова сеть, в тестах границу закрыть).
- `stop()`: stop-сигнал; in-flight `create_task` gather'ятся; необработанный хвост очереди **не** контрактовать как «все updates дойдут».
- PTB JobQueue jobs (если extra) ждут `JobQueue.stop(wait=True)`. Это **не** Antares `JOB_REGISTRY`.

---

## 6. JobQueue extra vs без extra

Antares jobs живут в `core.job_runner.JOB_REGISTRY`, не в PTB JobQueue.

| | extra нет (Cursor 3.12.10) | extra установлен |
|--|----------------------------|------------------|
| `build()` | `job_queue is None` | `JobQueue()` + `set_application` |
| `start()` | skip scheduler | `AsyncIOScheduler.start()` |
| `stop()` | skip | `JobQueue.stop(wait=True)` |
| Isolated jobs | `request_job` / executor | то же; PTB scheduler **не** заменяет JOB_REGISTRY |

Первый code **не** требует `python-telegram-bot[job-queue]`. Не планировать Antares расписания через PTB JobQueue в этом срезе. Если extra появится — start/stop PTB JobQueue идут вместе с Application.start/stop; не стартовать PTB jobs самим.

`requirements.txt` **не** менять в TASK-23/первом startstop code.

---

## 7. Первый update — не «только Telegram»

Отсутствие `schedule_loop` **не** значит отсутствие бизнес-действий. Реальные Antares handlers (CONFIRMED):

| Вход | Что может начаться |
|------|-------------------|
| `/run_wallet` `/run_hourly` `/run_download` `/run_rate` `/operator_wallets_ready` `/wallet_editor_refresh` | `run_job_async` → `dispatch_job_async` → `request_job` → lock + executor + job (Dropbox/Playwright/PG/TG routes). Routes **lazy**-импортируют `telegram_bot` → **sender loop** |
| `/status` | lazy `get_telegram_sender_health_snapshot` → import `telegram_bot` |
| `/registry_replay` `/registry_export` `/auto_enable_*` | registry/outbox/PG; export шлёт в Telegram |
| `Document.ALL` | `handle_wallet_editor_document`: `bot.get_file` (сеть), `add_task` → **WE worker** (lazy `ensure_worker` на `add_task`) |
| `/reload_rules` | snapshot force_sync |
| `reply_text` / `get_file` | Telegram HTTP, если Bot уже initialize |

Поэтому следующий code проверяет lifecycle **в sandbox**: синтетические updates, границы сети/SDK/sender/worker/jobs, **без** live getUpdates и рабочих credentials. Успех такого прогона **не** разрешение запускать сервис и **не** cutover.

---

## 8. Как запускать сервис vs диагностика

Не добавлять live-запуск в существующий `run` молча.

| argv | Поведение | Когда |
|------|-----------|--------|
| нет / `boot` | TASK-18: сборка, exit 0, нет Application | сохранить |
| `run` | TASK-22: snapshot + `build` + handlers, одна диагностика, **exit 0**, нет initialize/polling | сохранить; **не** превращать в сервис |
| будущий `serve` (имя можно уточнить в code PR, не `run`) | ручной async: initialize → start → (позже) polling; процесс **живёт** до stop | **не** первый code TASK-23; отдельный code после sandbox lifecycle |
| иное | отказ, как сейчас | сохранить |

Idle без polling **не** вводить как замену `serve`.

---

## 9. Минимальный следующий code (не этот PR)

Только проверка ручного PTB lifecycle в subprocess/sandbox на базе TASK-22 Application:

1. Сохранить `boot` и диагностический `run` без initialize.
2. Не звать `run_polling` / `start_polling` / live getUpdates.
3. Синтетический token + граница HTTP: `get_me` **не** идёт в Telegram (stub/перехват `do_request` / Bot.get_me на границе, с записью попытки). Успех initialize в тесте ≠ авторизация боевого token.
4. `initialize` → `start` → `process_update` (синтетика) → `stop` → `shutdown`; ненулевой exit и нет success, если шаг падает.
5. Частичный `get_me` fail: cleanup через `Bot.shutdown`, не через no-op `Application.shutdown`.
6. Границы: сеть, `telegram_bot`, mixed/raccoon, Dropbox, PG, Playwright, `request_job` / `add_task` — отказ на вызове; первый update **не** должен стартовать sender/worker/job, пока тест это не разрешит явно (по умолчанию запрет).
7. Не стартовать `schedule_loop`. Не менять mixed gate, scheduler, profiles, requirements, Railway, STATE_DIR, remote/stale policy.

Не считать этот code «сервис запущен». `serve` + getUpdates — **следующий** code PR после принятия этого контракта и зелёного sandbox.

---

## 10. Полный безопасный shutdown сервиса — зависимости (не первый code)

PTB stop/shutdown **не** останавливает Antares-потоки. Пока нет API — не писать success «graceful shutdown сервиса».

Порядок реализации **после** sandbox lifecycle и **до** заявления production serve:

1. **Допуск заданий** (`JOB_ACCEPT` или эквивалент) — сейчас **нет**; иначе первый update/`request_job` исполняет работу.
2. Stop **sender** (сейчас import = daemon без join) — **нет**.
3. Stop **WE worker** (sentinel/join) — **нет**.
4. Shutdown **job executor** вне test helper — **нет**.
5. `schedule_loop` stop event + фильтр семи keys — если loop вообще вводить; **не** в первом startstop code.
6. Remote/stale workbook, общий token, `STATE_DIR` locks, `/tmp` auth-state, cutover — **отдельные** решения до реального запуска.

Пока пункты 1–4 отсутствуют: даже корректный PTB stop оставляет бизнес-потоки. Isolated `serve` в prod **не** готов.

---

## 11. Вне скоупа TASK-23 и первого startstop code

Live Telegram; рабочий token; смена `run` в сервис; `run_polling`; mixed gate/`scheduler.py`; production profiles; `requirements.txt`; Railway; workbook/routes; `STATE_DIR`; remote/stale; cutover; merge/retarget/deploy; исходное Test.

---

## 12. Оставшиеся вопросы

1. Точное имя argv сервиса (`serve` vs иное) — code PR, не `run`.
2. Stub `get_me`: уровень `Bot.get_me` vs `HTTPXRequest.do_request` — выбрать в code, оба допустимы, если попытка сети записана и отклонена.
3. Нужен ли `process_update` без `start()` (без fetcher) — допустимо для unit handlers; контракт сервиса всё же включает `start`/`stop`, чтобы проверить очередь и JobQueue extra-ветку.
4. PTB/Railway version — **UNKNOWN**; контракт 22.8.
5. `ResourceWarning` httpx в diagnostic `run` — по-прежнему не доказан родительским `-W default`.
