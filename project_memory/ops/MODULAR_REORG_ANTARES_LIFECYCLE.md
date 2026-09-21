# Контракт lifecycle isolated Antares (TASK-19)

| Мета | Значение |
|------|----------|
| **Статус** | план review пройден (HEAD `b76a377…`); diagnostic `run` реализован в TASK-20 (не выпущен); сервисный lifecycle не реализован; merge нет |
| **База** | закрытие TASK-18 `d1d11e308c1abc9b0a5ee4531c3249559b16c5a0` (review HEAD `2c6eeac34940f70413be70da35ddbc81e720ec83`; тесты `0603eb9ac42c3c04b282df6b38ed804b62db7307`) |
| **План entry** | [MODULAR_REORG_ANTARES_ENTRYPOINT.md](MODULAR_REORG_ANTARES_ENTRYPOINT.md) § 3–4 |
| **Сборка** | [MODULAR_REORG_ANTARES_ASSEMBLY.md](MODULAR_REORG_ANTARES_ASSEMBLY.md) |
| **Application без polling** | [MODULAR_REORG_ANTARES_APPLICATION.md](MODULAR_REORG_ANTARES_APPLICATION.md) — TASK-21, review пройден (`777a52f…`) |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Это обследование **исходников** на SHA закрытия TASK-18; diagnostic `run` добавлен TASK-20 (не выпущен). Живой workbook, Railway env и production-настройки **не** читались. Неизвестное — **UNKNOWN**. Polling, worker, schedules и sender **не** стартуют. Runtime, `rules_provider` и mixed gate в docs TASK-19/21 **не** менять.

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
| `python -m apps.antares run` | TASK-20: prefix + локальный `.xlsx` + snapshot + exit 0. Не сервис. | реализован, не выпущен |
| `python -m apps.antares run` (следующий code) | после успешного snapshot: `Application.build()` + Antares handlers; без initialize/polling; exit 0 | TASK-21 code (не этот docs) |
| поздний `run` (после этого подэтапа) | snapshot → Application → worker → filtered schedules → polling | отдельные code PR |
| иной argv | отказ, exit ≠ 0, без run | первый code PR |

Idle без polling **не** вводить. Успешный boot и успешный первый `run` **завершают** процесс.

Поздние шаги живого сервиса (ENTRYPOINT § 4):

1. локальная проверка workbook — TASK-20, не выпущено
2. Application + Antares handlers без polling — [APPLICATION](MODULAR_REORG_ANTARES_APPLICATION.md) (docs TASK-21; code отдельно)
3. worker (lazy как сейчас допустимо)
4. schedule thread **с фильтром семи keys до dispatch**
5. polling

Удалённый источник workbook и допустимость stale reuse **перед настоящим запуском сервиса** — отдельное будущее решение (§ 4.3). В TASK-21 docs/code **не** входят.  

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

Isolated **поздний** run инвертирует порядок: snapshot **до** Application. Первый диагностический `run` Application **не** создаёт: сборка + локальный workbook + exit 0. Сбой snapshot → exit ≠ 0.

---

## 4. Workbook: `force_sync`, кеш и первый `run`

### 4.1 `AccessRules` и путь

**CONFIRMED** `AccessRules.__init__(rules_env_path=…)` аргумент **не использует**; `_snap = None`; xlsx не читает. Путь берёт `rules_provider` из **`os.environ["RULES_XLSX_PATH"]`**, не из конструктора.

Isolated boot сейчас: `AccessRules(os.getenv("RULES_XLSX_PATH", "").strip())` — вызов косметический. Первый `run` обязан после dotenv проверить env-путь **сам** (существует, файл, `.xlsx`) и отказать **до** `get_snapshot`. Не чинить конструктор в этом подэтапе.

### 4.2 Четыре разных механизма (не смешивать)

`force_sync=True` **не** доказательство свежести удалённого workbook. `STRICT` **не** достаточная защита от дискового fallback.

| Что | Что делает код | Роль `force_sync=True` | Роль STRICT |
|-----|----------------|------------------------|-------------|
| Повторная загрузка / построение snapshot | `get_snapshot_v2` при `force_sync=True` **не** возвращает `_last_v2_snapshot` по TTL/stat; заново `evaluate_snapshot_publish` | обходит in-memory v2 cache | не про источник файла |
| Подтверждение свежести Dropbox | отдельного «rev/mtime remote == local» **нет** | **нет** | **нет** |
| Повторное использование дискового кеша | `_RULES_LOCAL` = `/tmp/rules_cache/rules.xlsx` (module-level). Если `_try_local_workbook_path()` не нашёл файл: download; при неуспехе, если `_RULES_LOCAL.exists()`, snapshot **с диска** даже в **свежем процессе** (нет `_last_rules_wb`) | download всё равно вызывается, но успех может быть **старый файл на диске** | **не** блокирует эту ветку: STRICT проверяется только на reuse `_last_rules_wb` |
| LEGACY fallback на snapshot **в памяти** | `get_snapshot_v2` LEGACY: при build/load error может вернуть `_last_v2_snapshot` без exception | `force_sync=True` всё равно идёт в evaluate; fallback — если уже был snapshot в **этом** процессе | STRICT/`publish_allowed` другой путь; не путать с дисковым `_RULES_LOCAL` |

`_try_local_workbook_path()`: `RULES_XLSX_PATH` → `Path.resolve()`; только если **is_file()** и suffix `.xlsx`. Иначе путь считается Dropbox-строкой (`_rules_dropbox_path`), даже если это «локально выглядящая» несуществующая строка.

Итог для свежего процесса без local file: `force_sync=True` + STRICT **могут** принять содержимое уже лежащего `/tmp/rules_cache/rules.xlsx` после неуспешного download. Это **не** свежесть remote.

Политику `get_rules_snapshot` / `get_snapshot_v2` в TASK-19 и в первом code PR **не** менять.

### 4.3 Первый диагностический `run` — источник без UNKNOWN

Успешный сценарий **этого** подэтапа — только локальный файл:

1. Boot-prefix (gate, dotenv, token, AccessRules/logger, `assemble_antares`).
2. Отказ run, если сборка неуспешна — **до** проверки файла и **до** snapshot.
3. После dotenv: `RULES_XLSX_PATH` strip; пусто / не `.xlsx` / path не существует как файл → явный отказ, **без** вызова `get_snapshot` / `get_snapshot_v2` (нет Dropbox, нет `_RULES_LOCAL`).
4. Файл есть → `AccessRules.get_snapshot` (реальный provider). Из-за п. 3 `_try_local_workbook_path()` должен вернуть этот файл; download не входит в успех.
5. Publish policy процесса принимает snapshot. Иначе exit ≠ 0, **без** строки успешной диагностики.
6. Печать диагностики → **exit 0**. Процесс **не** остаётся живым.

Диагностика успеха значит: **локальные правила прочитаны и приняты текущей политикой**. Не значит: свежесть Dropbox, работающий сервис, готовность к cutover, polling, worker.

В успешный сценарий **не** входят: удалённая загрузка; fallback на `/tmp/rules_cache/rules.xlsx`; LEGACY in-memory stale; «тихий» успех с чужим дисковым кешем.

**Отдельное будущее решение** (не первый code PR): когда isolated процесс станет сервисом — какой remote sync обязателен, можно ли stale disk/memory, что считать свежестью. До этого решения настоящий запуск с Dropbox-путём **не** входит в контракт этого подэтапа.

Содержимое живого production workbook — **UNKNOWN** (не читалось). Тесты — синтетический sandbox `.xlsx`.

Когда ещё читается workbook (mixed / позже), без смены смысла первого `run`:

| Момент | Что происходит | Источник |
|--------|----------------|----------|
| Isolated **boot** | workbook **не** читается | `apps/antares.py` |
| Mixed start | `RULES.get_snapshot(force_sync=True)` **после** Application | `scheduler.py` |
| `/reload_rules` | `invalidate()` + `get_snapshot(force_sync=True)` | handlers |
| Тик schedules | `load_schedules(force_sync=False)` | `schedule_loop` |
| `request_job` | `get_rules_snapshot` | `job_runner` |

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
| битый/непубликуемый snapshot на старте | exception после Application | первый `run`: exit ≠ 0 **до** Application; нет успешной диагностики |
| битый snapshot на тике loop | warning, loop жив | loop жив (позже); **не** этот подэтап |
| `/reload_rules` | invalidate + force snapshot; ошибка в reply; clocks reset | то же для handlers позже; **не** первый `run` |
| LEGACY reuse stale snapshot в памяти | возможно в том же процессе | первый `run`: свежий процесс + только local file; in-memory stale не сценарий успеха. Remote/stale до сервиса — § 4.3 |
| Disk `_RULES_LOCAL` после неуспешного download | возможен даже STRICT + `force_sync` | **запрещён** как успех этого подэтапа: отказ, если нет существующего local `RULES_XLSX_PATH` **до** snapshot |

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
| assemble | AccessRules/logger; частичный `JOB_REGISTRY` возможен при ошибке после bind — TASK-16: неуспех не считать сборкой | **запрет run**; проверка файла и snapshot **не** вызываются |
| нет/не файл `RULES_XLSX_PATH` | сборка ok | явный отказ **до** snapshot; нет Dropbox/`_RULES_LOCAL` |
| snapshot / publish reject | сборка + local file | exit ≠ 0; **нет** успешной диагностики; Application/threads **не** создавать |
| Application.build / add_handler | snapshot ok; Application может существовать | **не** `Application.shutdown()` после одного `build()` (no-op без initialize — PTB 22.8). Не initialize ради cleanup. Процесс exit — только одноразовая диагностика, не graceful shutdown сервиса. Не стартовать polling/schedule. См. APPLICATION.md |
| worker start | Application без polling | worker сейчас no-op; при появлении start — join если API есть, иначе log «нет stop» |
| schedule thread | polling ещё нет | нужен stop event **до** start thread; не стартовать thread без event, если этот PR вводит loop |
| polling | все предыдущие | выход из `run_polling`; затем обратный порядок **только** для API, которые уже есть |

Не начинать следующий этап, если предыдущий неуспешен. Не чинить mixed `main()` в TASK-19/первом code PR.

---

## 9. Владение ресурсами

| Ресурс | Start API | Stop API | Что отсутствует | Минимальное изменение (не в TASK-19) |
|--------|-----------|----------|-----------------|--------------------------------------|
| Isolated boot process | `python -m apps.antares` | процесс exit 0 | argv `run` | сохранить boot; добавить argv |
| Isolated diagnostic `run` | argv `run` + local xlsx + snapshot | процесс exit 0 | — | не Application/polling |
| `JOB_REGISTRY` / handlers bind | `assemble_antares` | нет unbind | — | не clear() при отказе (уже TASK-16) |
| Local workbook path | env `RULES_XLSX_PATH` после dotenv | — | конструктор AccessRules путь **не** хранит | проверить файл **до** snapshot |
| Workbook snapshot | `AccessRules.get_snapshot` / `get_snapshot_v2` | cache `invalidate` | подтверждение Dropbox freshness | только local file на этом подэтапе |
| PTB `Application` | `Application.builder().token().concurrent_updates(True).build()` | `stop`/`shutdown` только после initialize; mixed не вызывает | обёртка isolated | создавать **после** snapshot; build-only диагностика → процесс exit (**не** graceful shutdown сервиса), **не** initialize |
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
2. **Workbook (первый `run`):** существующий локальный `.xlsx` в `RULES_XLSX_PATH` после dotenv; handlers из `assemble_antares`, не `tg_commands.get_handlers()`. Remote/кеш `/tmp/rules_cache` — не успех этого подэтапа.
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

## 11. Первый code scope (после принятия этого уточнения)

**Не этот PR.** Один законченный code PR — **диагностический `run`**, не сервис:

**argv: default/`boot` = TASK-18. `run` = сборка + обязательный локальный `.xlsx` + snapshot текущей политики → диагностика → exit 0. Application, polling, worker, schedules, sender не стартуют.**

Источник workbook в успехе **известен**: существующий локальный файл из env. Нет UNKNOWN «Dropbox или кеш или LEGACY memory».

Проверяемый результат:

- `python -m apps.antares` — как TASK-18: 17 commands + document, семь jobs, exit 0; workbook не читается; нет `telegram_bot` / mixed / raccoon.
- `python -m apps.antares run` + синтетический sandbox `.xlsx` в `RULES_XLSX_PATH` + политика принимает → диагностика «локальные правила прочитаны и приняты»; **exit 0**; процесс не живёт; нет Application/polling/sender/worker/schedule thread.
- Пустой / не-xlsx / несуществующий `RULES_XLSX_PATH` после успешной сборки → явный отказ **до** snapshot; download и `_RULES_LOCAL` не используются для успеха.
- Повреждённый xlsx или `ContractPublishRejected` / отказ publish → exit ≠ 0; **успешная диагностика отсутствует**.
- Конфликт сборки (чужой key / bind) → exit ≠ 0; snapshot **не** вызывается.
- Смешанный `scheduler.py` + `PROJECT_PROFILE=antares` → exit 2.
- Неизвестный argv → отказ.

Почему это минимальный законченный кусок: отделяет boot от `run` и фиксирует источник правил без владения потоками и без смены `rules_provider`. Следующий code: Application+handlers без polling ([APPLICATION](MODULAR_REORG_ANTARES_APPLICATION.md)); далее polling+stop PTB; worker; schedules с фильтром **и** stop event; **отдельно** — remote source / stale reuse (§ 4.3).

Не включать в code TASK-20: изменение общей политики `rules_provider`; mixed gate; `JOB_ACCEPT`; Railway; cutover; sender shutdown; split `job_runner`; отдельный prod `STATE_DIR`; живой Telegram/Dropbox; Application.

---

## 12. Subprocess-проверки первого `run` (не запускать в TASK-19)

Отдельный процесс, как TASK-18. Pytest родителя не обходит gate. **Реальные** `AccessRules` и `core.rules_provider` (`get_snapshot` / `get_snapshot_v2` / publish). Не stub'ить snapshot целиком.

Сеть и внешние SDK (Dropbox `download_file`, Telegram, PG, Playwright) — отказ на **границе вызова**, не подменой `telegram_bot` в `sys.modules`. Не класть `telegram_bot` / `tg_commands` / raccoon заранее в `sys.modules`. Загрузка sender/mixed/raccoon = провал проверки.

Workbook: **синтетический** sandbox `.xlsx` (как C5 baseline / compose lifecycle), не живой prod файл.

Побочные записи только в sandbox:

| Побочный эффект | Default в коде | В тесте |
|-----------------|----------------|---------|
| Publish audit JSONL | `RULES_VALIDATE_AUDIT_JSONL` пусто → `/tmp/rules_validate_audit.jsonl` | путь в sandbox **или** выключить (`0`/`off`) |
| Identity registry | `RULES_IDENTITY_SAVE` default off; иначе `{folder}/state/rules_identity_registry.v1.json` от `RULES_XLSX_PATH` | не включать **или** `RULES_IDENTITY_REGISTRY_PATH` в sandbox |
| `_RULES_LOCAL` `/tmp/rules_cache/rules.xlsx` | общий диск хоста | не использовать как вход успеха; не писать рабочие реестры |

Рабочие identity/audit файлы репозитория и `/tmp` хоста **не** затрагивать.

| # | Сценарий | Ожидание |
|---|----------|----------|
| B | default boot (без argv) | TASK-18: exit 0; нет snapshot; нет sender/mixed/raccoon |
| R1 | `run` + валидный sandbox xlsx, политика принимает | assemble + local snapshot; диагностика успеха; exit 0; нет Application/polling/sender |
| R2 | `run`, `RULES_XLSX_PATH` отсутствует в env (после dotenv) | отказ **до** snapshot |
| R3 | путь задан, файла нет | отказ **до** snapshot |
| R4 | файл есть, не читаемый/повреждённый xlsx | snapshot/publish fail; exit ≠ 0; **нет** успешной диагностики |
| R5 | файл валидный zip/xlsx, publish policy отклоняет | `ContractPublishRejected` (или эквивалент); exit ≠ 0; нет успешной диагностики |
| R6 | конфликт сборки (foreign key / bind) | exit ≠ 0; **snapshot не вызывался** (нет audit/identity/provider download) |
| R7 | отказ snapshot | нет строки boot/run success; нет Application |
| R8 | sender / mixed `tg_commands` / raccoon | не импортировались |
| R9 | mixed `scheduler.py` + `antares` | exit 2 (без изменений) |

Позже (code после TASK-20): отказ после Application.build; состав handlers; filter schedules; polling — см. APPLICATION.md. Pytest в docs TASK-19/21 **не** требуется.

---

## 13. Зависимости и блокеры

**Подтверждённые зависимости плана:** TASK-18 boot (`d1d11e3…`); `assemble_antares`; isolated gate; env `RULES_XLSX_PATH` + `_try_local_workbook_path`; `AccessRules.get_snapshot` / `get_snapshot_v2` **без** смены их политики; lazy sender TASK-18.

**Не блокеры review этого документа:** отсутствие sender/worker/schedule stop API; отсутствие `JOB_ACCEPT`; Railway всё ещё `scheduler.py`; общий `STATE_DIR` в prod; отсутствие remote-freshness API.

**Конкретные блокеры первого code PR:**

- нужен синтетический sandbox `.xlsx`, который текущая publish policy **принимает**;
- проверка пути **до** snapshot обязательна: иначе несуществующий `RULES_XLSX_PATH` уходит в Dropbox/`_RULES_LOCAL`;
- audit JSONL и `RULES_IDENTITY_SAVE` увести в sandbox (default audit = `/tmp/rules_validate_audit.jsonl`);
- не подменять `telegram_bot` stub'ом в `sys.modules`;
- не менять `rules_provider` / mixed gate в том PR.

**Снято как UNKNOWN источника первого `run`:** успех = локальный существующий `.xlsx`. Disk cache и Dropbox в успех не входят.

**Блокеры позднего run (не первый code PR):**

- нет stop API sender; import = бессрочный daemon;
- `schedule_loop` без stop event и без фильтра семи keys;
- worker без join; `JOB_ACCEPT` нет;
- executor shutdown только для тестов;
- конфликт Telegram при втором polling на том же token;
- общие `STATE_DIR` locks и `/tmp` auth-state с mixed;
- семь keys ≠ изоляция сервиса;
- mixed `tg_commands` регистрирует raccoon при любом import;
- **нет принятого решения** о remote sync и stale reuse перед сервисом (`force_sync`/`STRICT` это не закрывают).

**UNKNOWN:** U12 raccoon schedules на сервисе Test; нужен ли отдельный `STATE_DIR` до cutover; содержимое prod workbook и routes; живые Railway переменные.

**Не UNKNOWN:** ослаблять mixed gate для isolated **не** нужно; первый диагностический `run` не сервис и не cutover.
