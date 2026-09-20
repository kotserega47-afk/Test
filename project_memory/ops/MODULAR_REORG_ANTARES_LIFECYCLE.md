# Контракт lifecycle isolated Antares (TASK-19)

| Мета | Значение |
|------|----------|
| **Статус** | PROPOSED (только документы; runtime не менялся) |
| **База** | закрытие TASK-18 `d1d11e308c1abc9b0a5ee4531c3249559b16c5a0` (review HEAD `2c6eeac34940f70413be70da35ddbc81e720ec83`; тесты `0603eb9ac42c3c04b282df6b38ed804b62db7307`) |
| **План entry** | [MODULAR_REORG_ANTARES_ENTRYPOINT.md](MODULAR_REORG_ANTARES_ENTRYPOINT.md) § 3–4 |
| **Сборка** | [MODULAR_REORG_ANTARES_ASSEMBLY.md](MODULAR_REORG_ANTARES_ASSEMBLY.md) |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Это обследование **исходников** на SHA закрытия TASK-18. Живой workbook, Railway env и production-настройки **не** читались. Неизвестное — **UNKNOWN**. Isolated `python -m apps.antares` сейчас только boot: сборка и **exit 0**. Polling, worker, schedules и sender **не** стартуют. Этот документ — контракт будущего **run** и остановки; runtime в TASK-19 **не** менять.

---

## 1. Цель

Зафиксировать:

- как оставить нынешний boot с exit 0 и отдельно включать run;
- порядок start/stop и отказ на каждом этапе;
- владение ресурсами без обещания graceful shutdown там, где API нет;
- обязательную изоляцию до будущего polling;
- минимальный следующий **code** PR (не этот).

Не объединять Application + polling + worker + schedules + shutdown в один code PR.

---

## 2. Boot остаётся; run — отдельный argv

**CONFIRMED** `apps/antares.py`: нет разбора argv. `main()` всегда: gate → dotenv → token → `AccessRules`/`logger` → `assemble_antares` → печать `commands=… document=… jobs=…` → возврат → процесс завершается (exit 0). Нет `Application`, `run_polling`, worker, `schedule_loop`.

Контракт:

| Команда | Поведение | Когда |
|---------|-----------|--------|
| `python -m apps.antares` (без аргументов) или явный `boot` | **как TASK-18**: сборка, диагностика, **exit 0**; workbook **не** читается; Application/sender/worker/schedules/polling **нет** | сохранить сразу, не ломать |
| `python -m apps.antares run` | тот же prefix, затем шаги run; **запрет** после неуспешной сборки | первый и следующие code PR |
| иной argv | отказ, exit ≠ 0, без run | первый code PR |

Idle без polling **не** вводить. Успешный boot по-прежнему **завершает** процесс.

Поздние шаги run (ENTRYPOINT § 4), **не** один PR:

1. fail-fast snapshot  
2. Application + Antares handlers  
3. worker (lazy как сейчас допустимо)  
4. schedule thread **с фильтром семи keys до dispatch**  
5. polling  

---

## 3. Смешанный `scheduler.py` vs isolated boot (CONFIRMED)

Mixed `main()` (`scheduler.py` ~349–365):

1. отказ без `BOT_TOKEN` (`TELEGRAM_BOT_TOKEN`);
2. `Application.builder().token(…).concurrent_updates(True).build()`;
3. `get_handlers()` — mixed `tg_commands` (raccoon cmds + `register_all_script_jobs` + import `raccoon_jobs`);
4. `RULES.get_snapshot(force_sync=True)` — **после** Application;
5. `ensure_worker_started()` — no-op (lazy per profile на `add_task`);
6. daemon `Thread(target=schedule_loop)`;
7. `app.run_polling(close_loop=False)`.

Isolated boot TASK-18 **не** копирует этот `main()`. Snapshot в mixed — после создания Application: при битом workbook процесс падает, Application **не** останавливается явным API.

Isolated run **инвертирует** порядок: snapshot **до** Application. Сбой snapshot → exit ≠ 0, Application не создавать.

---

## 4. Когда проверяется workbook

| Момент | Что происходит | Источник |
|--------|----------------|----------|
| `AccessRules.__init__` | **не** читает xlsx; `_snap = None` | `core/access_rules.py` |
| Isolated **boot** | workbook **не** читается | `apps/antares.py` |
| Mixed start | `RULES.get_snapshot(force_sync=True)` → `get_snapshot_v2` | `scheduler.py` ~358–359 |
| `/reload_rules` | `invalidate()` + `get_snapshot(force_sync=True)`; ответ об ошибке; процесс **не** гасится | `modules/antares/handlers.py` `cmd_reload_rules` |
| Каждый тик schedules | `load_schedules(force_sync=False)`; сбой → log + sleep 10, цикл жив | `scheduler.py` `schedule_loop` |
| `request_job` | `get_rules_snapshot(force_sync=…)` до lock/exec | `core/job_runner.py` |

`get_rules_snapshot`: локальный путь, иначе Dropbox download; при неуспехе — stale local / last snapshot / `RuntimeError` в зависимости от policy.

`get_snapshot_v2`: `evaluate_snapshot_publish`; `ContractPublishRejected` если publish не разрешён. LEGACY может вернуть **прошлый** in-memory snapshot при build/load error.

Для isolated **первого** `run`: `rules.get_snapshot(force_sync=True)` после успешного assemble. Исключение → **не** стартовать Application/polling. Не менять семантику `get_snapshot_v2` в первом code PR — только вызвать и отказать процессу.

Содержимое живого production workbook — **UNKNOWN** (не читалось).

---

## 5. Когда создаются Application и sender

### Application (PTB)

Mixed: `Application.builder().token(BOT_TOKEN)` в `scheduler.main`, затем `add_handler`. Isolated boot Application **не** создаёт.

PTB `Application` имеет `initialize` / `start` / `stop` / `shutdown`; mixed их **не** вызывает — блокирующий `run_polling`. Isolated не обещать полный lifecycle PTB, пока code PR не вызовет stop/shutdown явно.

Создание Application **не** стартует `integrations.telegram_bot`. Это другой Bot + daemon loop.

### Sender (`integrations.telegram_bot`)

На **import** модуля (CONFIRMED):

- `load_dotenv()` без фиксированного пути (отличается от isolated `{repo_root}/.env`);
- `Bot(...)` если token задан;
- `asyncio.new_event_loop()` + daemon `Thread` → `loop.run_forever()`;
- `_worker` на той же петле, `while True` по `queue`.

**Нет** `loop.stop()`, join thread, drain queue, закрытия Bot/HTTPX. Daemon умирает с процессом — это не graceful shutdown.

После TASK-18 assemble **не** импортирует этот модуль (lazy в send). Первый `import telegram_bot` (send, `/status` sender health, job send, `log_telegram_health_if_due` если подтянуть mixed scheduler) **необратимо** запускает loop.

Минимальное изменение для stop: **новый** API (событие/sentinel + `loop.call_soon_threadsafe(loop.stop)` + join). Сейчас **отсутствует**. Не обещать graceful sender stop в ближайших PR, пока API нет. Не стартовать sender в первом code PR после TASK-19.

---

## 6. Worker и schedules

### Wallet Editor worker (`automation/worker.py`)

- `ensure_worker_started()` — **no-op** (debug log).
- Потоки: daemon на `add_task` / add_wallet / auto-enable (`_ensure_profile_worker`).
- Очереди: in-memory `Queue` на профиль.
- **Нет** stop/join/sentinel. Процесс exit убивает daemon.

`JOB_ACCEPT` в коде **нет**. Drain «не принимать новые / дождаться очереди» — **отсутствует**.

Первый isolated run **не** обязан вызывать `ensure_worker_started`. Ingest всё равно поднимет worker при `add_task`.

### Job executor (`core/job_dispatch.py`)

- Lazy `ThreadPoolExecutor` в `get_job_executor()` при первом `dispatch_job_background` / sync dispatch.
- `shutdown(wait=False, cancel_futures=True)` только в `_reset_job_executor_for_tests`.
- Production stop API **нет**.

`schedule_loop` вызывает `dispatch_job_background` без фильтра keys.

### `schedule_loop`

- `while True` + `sleep(5)`; **нет** stop event.
- `load_schedules` — все enabled rows из snapshot, **без** фильтра Antares keys.
- Unknown `job_type` всё равно `dispatch_job_background` → `request_job` → `job_failed` / `unknown_job_type` (событие есть, работа не исполняется).
- Hourly gate только для `jt == "hourly"`.

Isolated: **не** копировать mixed loop как есть. Фильтр семи keys — **до** `dispatch_job_background`. Иначе raccoon/`hello_world`/чужие строки workbook попадут в dispatch (event log, locks/snapshot side effects).

Семь keys (CONFIRMED сборка): `download`, `hourly`, `rate`, `wallet`, `wallet_editor_registry_refresh`, `wallet_editor_registry_replay`, `script_job:operator_wallets_ready`.

---

## 7. Неизвестная строка workbook и reload

| Случай | Mixed сейчас | Isolated контракт |
|--------|----------------|-------------------|
| schedule `job_key` не в `JOB_REGISTRY` | dispatch → `unknown_job_type` | **не** dispatch; log skip |
| `job_key` в registry, но не в семи Antares keys | dispatch (raccoon и др., если mixed их зарегистрировал) | **не** dispatch, даже если кто-то зарегистрировал key |
| битый/непубликуемый snapshot на старте | exception после Application | fail-fast **до** Application; run не начинать |
| битый snapshot на тике loop | warning, loop жив | loop жив; **не** считать это стартом; не порождать jobs из пустого/чужого набора без фильтра |
| `/reload_rules` | invalidate + force snapshot; ошибка в reply; clocks reset | то же для handlers; **не** пересоздавать Application; schedules подхватят следующий `load_schedules` |
| LEGACY reuse stale snapshot | возможно | для **первого** fail-fast run: отказ процесса, если `get_snapshot(force_sync=True)` бросает; silent stale **не** считать успешным стартом, если исключение не брошено — **UNKNOWN** до явной политики в code PR snapshot (не раздувать первый PR) |

Не исследовать боевой xlsx. Неизвестные prod-строки — **UNKNOWN**.

---

## 8. Остановка и частичный startup

### Штатный выход mixed

`run_polling` блокирует. Ctrl+C / процесс kill: polling прерывается библиотекой; daemon (sender, schedule, worker) **без** join. Executor без shutdown. File locks job — если job в `request_job`, снятие lock в finally runner (**ожидаемо**, не проверялось live).

Это **не** graceful shutdown. Isolated не копировать «убить процесс = stop».

### Целевой порядок stop (когда API появятся)

1. Прекратить приём новых schedule dispatch (stop event / не звать dispatch) — **нет** сейчас; `JOB_ACCEPT` **нет**.
2. `Application.stop` / `shutdown` / выйти из `run_polling`.
3. Остановить schedule thread (нужен event; **нет**).
4. `ThreadPoolExecutor.shutdown` (есть только test helper).
5. Worker: sentinel + join (**нет**).
6. Sender loop stop (**нет**).

Пока шага нет в коде — в контракте: «механизм отсутствует; процесс exit / kill». Не писать в success criteria «graceful shutdown sender».

### Частичный сбой startup (isolated)

| Этап отказа | Уже создано | Действие |
|-------------|-------------|----------|
| gate / token | ничего из run | exit 2/≠0 как boot |
| assemble | AccessRules/logger; частичный `JOB_REGISTRY` возможен при ошибке после bind — TASK-16: неуспех не считать сборкой | **запрет run**; не snapshot, не Application |
| snapshot | сборка в памяти процесса | exit ≠ 0; Application/threads **не** создавать |
| Application.build / add_handler | snapshot ok; Application может существовать | если объект создан — вызвать доступный `shutdown` если уже initialize; иначе процесс exit. Не стартовать polling/schedule |
| worker start | Application без polling | worker сейчас no-op; при появлении start — join если API есть, иначе log «нет stop» |
| schedule thread | polling ещё нет | нужен stop event **до** start thread; не стартовать thread без event, если этот PR вводит loop |
| polling | все предыдущие | выход из `run_polling`; затем обратный порядок **только** для API, которые уже есть |

Не начинать следующий этап, если предыдущий неуспешен. Не чинить mixed `main()` в TASK-19/первом code PR.

---

## 9. Владение ресурсами

| Ресурс | Start API | Stop API | Что отсутствует | Минимальное изменение (не в TASK-19) |
|--------|-----------|----------|-----------------|--------------------------------------|
| Isolated boot process | `python -m apps.antares` | процесс exit 0 | argv `run` | сохранить boot; добавить argv |
| `JOB_REGISTRY` / handlers bind | `assemble_antares` | нет unbind | — | не clear() при отказе (уже TASK-16) |
| Workbook snapshot | `AccessRules.get_snapshot` / `get_snapshot_v2` | cache `invalidate` | отдельный «закрыть файл» не нужен | fail-fast вызов на `run` |
| PTB `Application` | `Application.builder().token().build()`; `run_polling` | PTB `stop`/`shutdown`; mixed не вызывает | обёртка isolated | создавать **после** snapshot; на ошибке после build — `shutdown` если initialize был |
| Polling / getUpdates | `run_polling` | выход из polling / `updater.stop` | Isolated не стартует | не в первом code PR |
| Sender loop + thread + queue + `Bot` | **import** `telegram_bot` | **нет** | stop/join/drain | не импортировать на `run` prefix; stop — отдельный PR **после** появления API |
| Job executor | lazy `get_job_executor` | `_reset_job_executor_for_tests` only | production shutdown | не dispatch на первом `run` prefix; позже вынести shutdown из test helper |
| Schedule thread | `Thread(schedule_loop).start()` | **нет** (while True) | Event/flag | не стартовать в первом code PR; когда вводить — фильтр семи keys **и** stop event в том же PR что и thread |
| WE worker queues/threads | `add_task` → daemon | **нет** | sentinel/join; `JOB_ACCEPT` | не вызывать bootstrap; stop — отдельный PR; не обещать drain |
| `STATE_DIR/locks` | `_try_lock` в `request_job` | unlock в runner | межпроцессный lock чужого mixed | не запускать jobs в первом `run` prefix |
| `/tmp/auth_state_wallet_editor_*.json` | Playwright storage_state | файл не «закрывается» процессом | отдельный каталог на cutover | не трогать в docs/code TASK-19 |
| Dropbox/PG/Telegram API | клиенты по вызову | нет единого shutdown | — | заглушки в subprocess-тестах |

---

## 10. Изоляция будущего run (обязательные проверки **до** polling)

Семь keys `JOB_REGISTRY` — **не** изоляция сервиса. Это только состав jobs. Raccoon handlers, чужие schedules, общий token, общие файлы остаются рисками.

### 10.1 До старта polling (обязать в run-контракте)

1. **Token:** непустой `TELEGRAM_BOT_TOKEN` после isolated dotenv. Второй процесс с тем же token (mixed `scheduler.py` / другой Antares) даёт конфликт getUpdates (Telegram 409). Isolated **не** детектит чужой процесс сам — операционное правило: один polling на token. Проверка в тестах: не поднимать второй polling на том же token.
2. **Workbook/routes:** fail-fast snapshot; handlers из `assemble_antares`, не `tg_commands.get_handlers()`.
3. **`STATE_DIR`:** default `/data/state`; locks `STATE_DIR/locks/{job}.lock`. Общий с mixed → взаимный `job_rejected_busy` / порча lock. Отдельный `STATE_DIR` на первый start-PR — **не решено** (ENTRYPOINT UNKNOWN); не молча шарить prod state в тестах (sandbox).
4. **Locks / tmp / auth-state / WE:** auth-state **файлы** `/tmp/auth_state_wallet_editor_{profile}.json` (CONFIRMED `automation/runtime.py`). Очереди worker — **process-local** memory; durable registry/outbox — PG/Dropbox (**общие** внешние системы). Тесты run не писать в боевые пути.
5. **Нет mixed/raccoon bootstrap:** не импортировать `integrations.tg_commands`, `integrations.raccoon_jobs`, raccoon downloaders. Harness как TASK-18: загрузка = провал проверки.
6. **Запрет run после неуспешной сборки:** не snapshot, не Application.

### 10.2 Process-local vs общее

| Process-local | Общие файлы / внешние системы |
|---------------|-------------------------------|
| `JOB_REGISTRY`, bind handlers, AccessRules instance | `STATE_DIR` (если тот же env), lock files |
| PTB Application, polling session | Telegram Bot API (token) |
| sender loop/queue (после import) | тот же token на send |
| executor threads, schedule thread, WE queues | `/tmp/auth_state_wallet_editor_*.json`; WE result `/tmp/wallet_editor` |
| in-memory snapshot cache | `rules.xlsx` local cache / Dropbox |
| | Postgres registry, Dropbox workbook/registry |

Профили production, живой xlsx, Railway `startCommand` — **UNKNOWN**, не менять.

---

## 11. Первый code scope (после review этого плана)

**Не этот PR.** Один законченный code PR:

**argv `boot` (default) vs `run`; на `run` — fail-fast snapshot после успешного assemble; процесс снова завершается; Application / polling / worker / schedules / sender не стартуют.**

Проверяемый результат:

- `python -m apps.antares` — как TASK-18: 17 commands + document, семь jobs, exit 0, нет `telegram_bot` / mixed / raccoon.
- `python -m apps.antares run` + sandbox workbook, snapshot успешен → диагностика snapshot + assemble, **exit 0**, процесс не живёт, нет polling/sender/worker/schedule thread.
- `run` после отказа assemble (чужой key / bind conflict) → exit ≠ 0, snapshot/Application нет.
- `run` при отсутствии/непубликуемом workbook (`get_snapshot` бросает) → exit ≠ 0, нет Application.
- смешанный `scheduler.py` + `PROJECT_PROFILE=antares` → по-прежнему exit 2.
- неизвестный argv → отказ.

Почему это минимальный законченный кусок: отделяет boot от run **без** владения потоками, для которых нет stop API. Следующие code PR (не смешивать в один): Application+handlers без polling; polling+явный stop PTB; worker; schedules с фильтром **и** stop event.

Не включать в первый code PR: `JOB_ACCEPT`, Railway, cutover, sender shutdown, split `job_runner`, ослабление mixed gate, отдельный prod `STATE_DIR`, живой Telegram.

---

## 12. Subprocess-проверки будущего lifecycle (не запускать в TASK-19)

Отдельный процесс, как TASK-18. Pytest родителя не обходит gate. Живой Telegram/Dropbox/PG/Playwright — **заглушки на границе вызова**. Не класть `telegram_bot` в `sys.modules` заранее. Mixed bootstrap — запрет импорта.

| # | Сценарий | Ожидание (когда появится соответствующий code) |
|---|----------|------------------------------------------------|
| B | default boot | TASK-18: exit 0, нет sender/polling |
| R1 | `run` + валидный sandbox snapshot | prefix + snapshot ok; до Application в первом code PR — exit 0 без polling |
| R2 | отказ gate/token | как boot; `run` не идёт дальше |
| R3 | отказ assemble | exit ≠ 0; нет snapshot side effects, нет Application |
| R4 | отказ snapshot | exit ≠ 0; нет Application/polling/threads |
| R5 | отказ после Application.build (позже) | нет `run_polling`; нет schedule thread; если initialize был — `shutdown` |
| R6 | штатная остановка после polling (позже) | нет **новых** `dispatch_job_background` после начала shutdown; in-flight — по наличию API (executor/worker/sender могут **не** быть graceful) |
| R7 | schedule filter (когда появится loop) | raccoon/unknown `job_key` не dispatch; семь Antares keys могут |
| R8 | mixed неизменен | `scheduler.py` + `antares` → exit 2; mixed `get_handlers` / raccoon registration не вызываются из isolated |
| R9 | второй polling тот же token | **не** устраивать в CI против live; контракт: запрещено операционно; при необходимости — stub Telegram API, один updater |

Docs-only TASK-19: pytest **не** требуется.

---

## 13. Зависимости и блокеры

**Подтверждённые зависимости плана:** TASK-18 boot (`d1d11e3…`); `assemble_antares`; isolated gate; `AccessRules.get_snapshot` / `get_snapshot_v2`; lazy sender TASK-18.

**Не блокеры review этого документа:** отсутствие sender/worker/schedule stop API; отсутствие `JOB_ACCEPT`; Railway всё ещё `scheduler.py`; общий `STATE_DIR` в prod.

**Конкретные блокеры первого code PR (argv+snapshot):**

- нужен sandbox `rules.xlsx` / локальный путь, иначе `get_snapshot` уйдёт в Dropbox или `RuntimeError`;
- поведение LEGACY stale-reuse без exception — уточнить в том PR, не здесь;
- не подменять `telegram_bot` stub'ом в `sys.modules` (как TASK-18).

**Блокеры позднего run (не первый code PR):**

- нет stop API sender; import = бессрочный daemon;
- `schedule_loop` без stop event и без фильтра семи keys;
- worker без join; `JOB_ACCEPT` нет;
- executor shutdown только для тестов;
- конфликт Telegram при втором polling на том же token;
- общие `STATE_DIR` locks и `/tmp` auth-state с mixed;
- семь keys ≠ изоляция сервиса;
- mixed `tg_commands` регистрирует raccoon при любом import.

**UNKNOWN:** U12 raccoon schedules на сервисе Test; нужен ли отдельный `STATE_DIR` до cutover; содержимое prod workbook и routes; живые Railway переменные.

**Не UNKNOWN:** ослаблять mixed gate для isolated **не** нужно.
