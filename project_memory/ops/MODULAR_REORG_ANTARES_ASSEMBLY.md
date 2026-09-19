# План минимальной сборки Antares (TASK-2026-09-17-14)

| Мета | Значение |
|------|----------|
| **Статус** | подготовлено к review; **не** runtime; merge нет |
| **Репозиторий** | `deniskotdavydov1991-wq/Test` |
| **Обследованный SHA** | `5f131ce50091a80cc03989d6fdf1e8b60f84b6ab` (закрытие TASK-13; код ingest = GPT `4a1e7796…`) |
| **Источники** | `ops/MODULAR_REORG_ADR.md`, `ops/MODULAR_REORG_MIGRATION.md`, `ops/MODULAR_REORG_HANDLER_SPLIT.md`; `integrations/tg_commands.py`; `modules/antares/{handlers,jobs,document_ingest}.py`; `integrations/{raccoon_jobs.py,script_jobs/}`; `core/{lock_status.py,project_profile_boot.py,job_runner.py}`; `scheduler.py` |
| **Эталон mixed** | `tests/fixtures/behavior_baseline/expected_tg_commands.json` (**не** менять) |

Это обследование **исходников** на SHA выше, не production и не прогон. Isolated Antares entry **ещё нет**. `JOB_ACCEPT`, durable inbox и cutover **не** входят. Runtime в TASK-14 **не** менять.

---

## 1. Цель

Определить минимальную **самостоятельную** сборку Antares, которая:

- регистрирует только Antares jobs и Antares Telegram handlers;
- **не** импортирует `integrations.tg_commands`;
- **не** импортирует `integrations.raccoon_jobs` и не регистрирует ключи `raccoon_*`;
- не запускает polling, worker execution, schedules и внешние действия в проверке сборки.

Наличие сборки handlers/jobs **не** означает готовность entrypoint, polling или production cutover. Early gate mixed `scheduler.py` **не** ослаблять: `PROJECT_PROFILE=antares` на legacy entry по-прежнему отказ.

---

## 2. Состав будущих Antares handlers

Порядок ниже — **будущий isolated** список (новый JSON позже). Mixed порядок 20 команд + document **не** менять.

| # | Handler | Isolated callback | Владелец реализации | ACL | Регистрация isolated | Mixed совместимость | Проверки |
|---|---------|-------------------|---------------------|-----|----------------------|---------------------|----------|
| 1 | `start` | **новый** Antares `cmd_start` | `modules.antares.handlers` (или узкий sibling `control.py`) | `start` | `CommandHandler` | mixed `cmd_start` **остаётся** в `tg_commands` (другой объект; текст mixed) | isolated: нет `/run_raccoon`; mixed help-тест Raccoon не падает |
| 2 | `help` | **новый** Antares `cmd_help` | то же | `help` | то же | mixed `_help_text()` **без изменений** | отдельный expected Antares help; `test_help_text_lists_raccoon_commands` только mixed |
| 3 | `status` | **новый** Antares `cmd_status` | то же | `status` | то же | mixed `cmd_status` остаётся; `KNOWN_JOB_TYPES` как сейчас | isolated locks: только Antares tuple; mixed observation по-прежнему полный `KNOWN_JOB_TYPES` |
| 4 | `whoami` | перенос существующего тела | `modules.antares.handlers` | `whoami` + `access_map` | то же | identity re-export в mixed | ACL deny; тот же `bind_rules` instance |
| 5 | `reload_rules` | перенос существующего тела | то же | `reload_rules` | то же | identity re-export | `invalidate` + `get_snapshot(force_sync=True)` **того же** `AccessRules`; `request_scheduler_clocks_reset` без старта loop |
| 6 | `run_wallet` | уже есть | `handlers.cmd_run_wallet` | `run_wallet` | то же | уже re-export | job_type `wallet` |
| 7 | `run_hourly` | уже есть | `cmd_run_hourly` | `run_hourly` | то же | уже re-export | `hourly` |
| 8 | `run_download` | уже есть | `cmd_run_download` | `run_download` | то же | уже re-export | `download` |
| 9 | `run_rate` | уже есть | `cmd_run_rate` | `run_rate` | то же | уже re-export | `rate` |
| 10 | `operator_wallets_ready` | уже есть | `cmd_operator_wallets_ready` | `operator_wallets_ready` | то же | уже re-export | job `script_job:operator_wallets_ready` **есть** в registry |
| 11 | `rules_validate` | перенос существующего тела | `handlers` | `rules_validate` | то же | identity re-export | executor + audit hook; без dispatch job |
| 12 | `auto_enable_plan` | уже есть | `cmd_auto_enable_plan` | `auto_enable_plan` | то же | уже re-export | не JOB_REGISTRY |
| 13 | `auto_enable_run` | уже есть | `cmd_auto_enable_run` | `auto_enable_run` | то же | уже re-export | не JOB_REGISTRY |
| 14 | `wallet_editor_refresh` | уже есть | `cmd_wallet_editor_refresh` | `wallet_editor_refresh` | то же | уже re-export | job `wallet_editor_registry_refresh` |
| 15 | `registry_health` | уже есть | `cmd_registry_health` | `registry_health` | то же | уже re-export | прямой вызов, не job |
| 16 | `registry_replay` | уже есть | `cmd_registry_replay` | `registry_replay` | то же | уже re-export | прямой outbox; **не** путать с job `wallet_editor_registry_replay` |
| 17 | `registry_export` | уже есть | `cmd_registry_export` | `registry_export` | то же | уже re-export | PG export |
| 18 | `filters.Document.ALL` | уже есть | `document_ingest.handle_wallet_editor_document` | chat allowlist | последний `MessageHandler` | mixed через `wallet_editor_tg` identity | тот же function object |

**Не входят в Antares handlers:** `run_raccoon`, `run_hourly_raccoon`, `run_script_hello`.

Итого isolated: **17 CommandHandler + 1 MessageHandler**. Mixed: **20 + 1** (эталон JSON).

Уже выделенные **11** command callbacks остаются в `modules.antares.handlers`.

---

## 3. Решение: Antares help / start / status

Mixed callbacks **нельзя** переиспользовать.

Источник mixed help (`integrations/tg_commands.py` `_help_text` на обследованном SHA): перечень включает `/run_raccoon`, `/run_hourly_raccoon`, `/run_script_hello`. Тест `tests/test_raccoon_tg_commands.py::test_help_text_lists_raccoon_commands` фиксирует mixed текст. **Не** менять.

Источник mixed status: при `OBSERVATION_ENABLED` — scheduler health, `get_lock_status_for_job_types(KNOWN_JOB_TYPES)`, conversion `state_get`, Telegram sender, routes, job_health. `KNOWN_JOB_TYPES` в `core/lock_status.py` содержит `raccoon_wallet`, `raccoon_hourly`, `raccoon_daily_conversion`. Без observation — `get_status()` по **всему** `_RUNNING` процесса.

**Выбор:** отдельные Antares callbacks (не параметр profile у mixed функций).

| Поверхность | Antares текст / данные | Граница |
|-------------|------------------------|---------|
| `start` | `"Ок.\nЯ готов.\n\n"` + **Antares** help | не вызывать mixed `_help_text()` |
| `help` | тот же перечень команд, что isolated handlers, **без** raccoon и **без** `/run_script_hello` | строки `/run_raccoon` и `/run_hourly_raccoon` запрещены |
| `status` (observation) | те же блоки scheduler / conversion / TG sender / routes / job_health, что mixed, но locks только по Antares tuple: `wallet`, `hourly`, `rate`, `download`, `wallet_editor_registry_refresh` | **не** передавать default `KNOWN_JOB_TYPES`; не читать raccoon lock files «за компанию» |
| `status` (без observation) | `get_status()` процесса | корректно, только если в процессе нет raccoon keys (обеспечивает registry сборки) |

`core.lock_status.KNOWN_JOB_TYPES` **не** сужать в этом плане: mixed observation зависит от полного кортежа. Isolated передаёт явный список.

---

## 4. Решение: `run_script_hello`

**Не включать** в isolated Antares handlers и **не** регистрировать `script_job:hello_world` в Antares JOB_REGISTRY.

Обоснование (исходники SHA обследования):

- `hello_world` — заглушка без кабинета/карт (`integrations/script_jobs/registry.py`); не Antares-бизнес.
- `integrations.script_jobs.runtime.register_script_jobs()` на import регистрирует **все** ключи `SCRIPT_REGISTRY` (`hello_world` и `operator_wallets_ready`) в глобальный `JOB_REGISTRY`.
- `operator_wallets_ready` **нужен** Antares (выгрузка кошельков, уже `handlers.cmd_operator_wallets_ready`).
- Импорт пакета `integrations.script_jobs` тянет `scripts/operator_wallets_ready.py` → `main.CONVERSION_COLUMNS` / Excel export. Это не Raccoon, но и не «пустой» import.
- Полный script registry в Antares-сборке дал бы лишний `hello_world` job (побочная регистрация) и лишнюю команду.

Как регистрировать нужный script job:

- ввести **явный** API вида `register_script_jobs(keys=("operator_wallets_ready",))` (или `register_script_job("operator_wallets_ready")`), который пишет в переданный `registry` **только** `script_job:operator_wallets_ready`;
- mixed по-прежнему делает `from integrations import script_jobs` (оба ключа) — поведение mixed не менять, пока отдельный PR не докажет identity;
- isolated **не** импортирует raccoon и **не** вызывает текущий import-time `register_script_jobs()` «на всём словаре», если это добавляет `hello_world`.

Команда `/run_script_hello` остаётся только в mixed `tg_commands`. Isolated help её не перечисляет.

---

## 5. Jobs isolated Antares

Точный набор ключей:

```
wallet
hourly
rate
download
wallet_editor_registry_refresh
wallet_editor_registry_replay
script_job:operator_wallets_ready
```

Семь ключей. `ANTARES_JOB_KEYS` (шесть) уже в `modules.antares.jobs`. Replay-job **регистрируется**, хотя TG-команда `registry_replay` идёт прямым вызовом outbox — как сегодня в mixed.

**Запрещены:** `raccoon_wallet`, `raccoon_hourly`, `raccoon_daily_conversion`, `script_job:hello_world`.

`register_jobs(registry)` на обследованном SHA при **вызове** импортирует `bakai_monitor_playwright`, `downloader_wallets`, `wallet_editor_registry`, `wallet_editor_registry_refresh`. Это регистрация, не запуск job. Проверка сборки **может** загрузить эти модули, но **не** должна вызывать callables, Playwright, TG send, PG write.

---

## 6. Последовательность сборки (будущая)

Порядок, когда появится код:

1. **Проверка профиля** — только на **isolated** entry (ещё нет). Mixed `enforce_legacy_scheduler_profile()` не трогать: unset → legacy mixed; явное `antares`/`raccoon`/`wr` → отказ. Не расширять mixed scheduler до isolated.
2. **Rules / logger** — создать существующие объекты: `AccessRules(...)` (конструктор workbook **не** читает) и `get_logger` профиля `MAIN`, как mixed. Не второй экземпляр правил «для Antares» в том же процессе, что mixed.
3. **bind** — `bind_rules(rules)` + `bind_logger(logger)` на те же объекты. Reload — `invalidate` внутри того же instance.
4. **Регистрация jobs** — `register_jobs(registry)` + один script job `operator_wallets_ready`. Registry — переданный mapping (в процессе обычно `JOB_REGISTRY`). Без `raccoon_jobs`.
5. **Handlers** — `get_antares_handlers()`: 17 команд + `MessageHandler(filters.Document.ALL, handle_wallet_editor_document)`.
6. **Подключение к приложению** — `Application.add_handler` + `run_polling` — это **этап 3**, не этап 1.

Разделение этапов (ADR / MIGRATION):

| Этап | Что | Готовность сейчас |
|------|-----|-------------------|
| **1** | Построить Antares handlers/jobs **без запуска** | план; кода сборки нет |
| **2** | Отдельный entrypoint (`apps/…` или аналог), не `scheduler.py` mixed | **нет**; `apps/` в репозитории нет |
| **3** | Polling + `ensure_worker_started` + `schedule_loop` | только mixed `scheduler.py` |
| **4** | Production cutover | нет `JOB_ACCEPT`, нет durable inbox |

Этап 1 **не** включает 2–4.

---

## 7. Что мешает сборке без mixed `tg_commands`

Зафиксировано по исходникам SHA обследования.

### Уже можно не импортировать `tg_commands`

- `modules.antares.handlers` — telegram + `core.tg_command_dispatch` / `Actor`. Тест `test_import_handlers_does_not_load_mixed_tg_commands`.
- `modules.antares.jobs` — import не регистрирует jobs; `register_jobs` лениво тянет Antares downloaders.
- `modules.antares.document_ingest` — `automation.audit.log`, startup warning; worker/routing ленивые. Не импортирует `tg_commands`.

### Глобальные реестры и process state

| Объект | Где | Риск |
|--------|-----|------|
| `JOB_REGISTRY` | `core.job_runner` (пустой dict, наполняется импортами) | `import raccoon_jobs` / `import script_jobs` пишут в **тот же** процесс |
| `_RUNNING` | `job_runner` | mixed `get_status()` видит все job_type процесса |
| `handlers._rules` / `_logger` | module globals | один bind на процесс |
| `_ALLOWLIST_STARTUP_LOGGED` | `document_ingest` | один startup log на процесс |
| `KNOWN_JOB_TYPES` | `lock_status` | mixed status; isolated не должен использовать default |
| `ThreadPoolExecutor` | `core.job_dispatch.get_job_executor` | создаётся при **dispatch**, не при import handlers |

### Side effects импорта

| Импорт | Эффект |
|--------|--------|
| `integrations.tg_commands` | `raccoon_jobs` + `script_jobs` (оба script keys) + `register_jobs(JOB_REGISTRY)` + `AccessRules()` + bind + ingest через compat |
| `integrations.raccoon_jobs` | три raccoon keys; импорт raccoon downloaders/analyzers |
| `integrations.script_jobs` | `register_script_jobs()` на всём `SCRIPT_REGISTRY` |
| `document_ingest` | startup allowlist log; `utils.logger` → `logs/app.log` |
| `handlers` → `job_runner` | `rules_provider` на import chain (workbook **не** обязан читаться до `get_snapshot`) |
| `register_jobs(...)` | import Playwright-adjacent downloader modules |
| `scheduler.py` | gate, затем `tg_commands`, Application, `ensure_worker_started`, daemon `schedule_loop`, `run_polling` |

### Scheduler dependencies (не этап 1)

`scheduler.py`: `get_handlers, RULES` из `tg_commands`; `ensure_worker_started`; `load_schedules`; `dispatch_job_background`. Isolated сборка **не** вызывается отсюда, пока нет отдельного entry.

---

## 8. Приёмка будущего code PR (не этот PR)

- Isolated registry **точно** семь ключей § 5; raccoon keys отсутствуют.
- Отдельный expected список Antares handlers (новый fixture; **не** переписывать mixed `expected_tg_commands.json`).
- Import сборки / `modules.antares.assembly` (имя уточнит code PR) **не** загружает `integrations.tg_commands`, `integrations.raccoon_jobs`, `integrations.raccoon_*`.
- Mixed baseline: inventory AST + registration dump + help Raccoon — без регрессии.
- ACL на перенесённых whoami/reload/validate; reload того же `AccessRules` без повторного bind.
- Сборка **не** вызывает `run_polling`, `ensure_worker_started`, `schedule_loop`, job callables, живой Telegram, Playwright run, PG write.
- Identity: 11 существующих callbacks и ingest object те же, что mixed re-export.
- Early gate mixed **без** изменений: явное `antares` на `scheduler.py` по-прежнему отказ.

Проверки — subprocess / sandbox, без живых кабинетов.

---

## 9. Минимальный следующий code PR (не TASK-14)

Один PR сборки **этапа 1**:

1. Селективная регистрация `script_job:operator_wallets_ready` без обязательной регистрации `hello_world` на isolated пути.
2. Функция сборки: принять `registry`, `rules`, `logger` → bind → `register_jobs` → script job → список handlers § 2.
3. Antares `start`/`help`/`status` — новые callbacks; mixed тексты не трогать.
4. Перенос `whoami` / `reload_rules` / `rules_validate` в Antares handlers + identity re-export в mixed (как остальные команды).
5. Тесты § 8. Mixed `get_handlers()` имена/порядок JSON без изменений.
6. **Не** добавлять isolated entrypoint; **не** менять `scheduler.py` / `project_profile_boot.py`; **не** вводить `JOB_ACCEPT`; **не** merge #4–#16.

Вне этого PR: этап 2 entry, этап 3 polling/worker/schedules, этап 4 cutover, Railway, профили production.

---

## 10. Нерешённые блокеры

Блокирующих UNKNOWN для **плана этапа 1** нет.

Ограничения, которые code PR должен учесть, но не обязан закрывать целиком:

1. `operator_wallets_ready.py` импортирует `from main import CONVERSION_COLUMNS` — тяжёлый транзитивный import при загрузке script registry. Не Raccoon; не считать «чистым» import. Сборка может грузить этот модуль при регистрации script job; не запускать `run_operator_wallets_ready`.
2. `register_jobs()` импортирует downloader/registry modules. Допустимо для регистрации; запрещены вызовы run-функций в тесте сборки.
3. Глобальный `JOB_REGISTRY`: тесты сборки — свежий subprocess, не после `import tg_commands`.
4. Isolated entry + разрешение `PROJECT_PROFILE=antares` — **другая** задача; иначе gate и ADR противоречат друг другу.

`JOB_ACCEPT`, durable inbox, cutover — отдельные задачи, не блокеры этого плана.
