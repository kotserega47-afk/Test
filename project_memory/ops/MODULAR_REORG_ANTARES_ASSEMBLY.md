# План минимальной сборки Antares (TASK-2026-09-17-14)

| Мета | Значение |
|------|----------|
| **Статус** | подготовлено к review; **не** runtime; merge нет |
| **Репозиторий** | `deniskotdavydov1991-wq/Test` |
| **Обследованный SHA** | `5f131ce50091a80cc03989d6fdf1e8b60f84b6ab` (закрытие TASK-13; код ingest = GPT `4a1e7796…`) |
| **Уточнение плана** | поверх `8fe4efbdadea40d189d4a714835ad1ea315501c0` |
| **Источники** | ADR, MIGRATION, HANDLER_SPLIT; `integrations/tg_commands.py`; `modules/antares/{handlers,jobs,document_ingest}.py`; `integrations/script_jobs/{__init__,runtime,registry}.py`; `integrations/script_jobs/scripts/operator_wallets_ready.py`; `main.py`; `core/{job_runner,job_dispatch,job_health,lock_status,scheduler_health,project_profile_boot}.py`; `scheduler.py` |
| **Эталон mixed** | `tests/fixtures/behavior_baseline/expected_tg_commands.json` (**не** менять) |

Это обследование **исходников**, не production. Isolated entry **нет**. `JOB_ACCEPT`, durable inbox, cutover **не** входят. Runtime в TASK-14 **не** менять.

---

## 1. Цель

Самостоятельная сборка Antares:

- регистрирует только Antares jobs и Antares handlers;
- **не** импортирует `integrations.tg_commands` и `integrations.raccoon_jobs`;
- dispatch идёт через существующий `request_job` → **`core.job_runner.JOB_REGISTRY`** (не произвольный mapping);
- не запускает polling, worker execution, schedules и внешние действия в проверке сборки.

Этап 1 **не** готовность entrypoint / polling / cutover. Early gate mixed **не** ослаблять.

---

## 2. Состав будущих Antares handlers

Порядок — **будущий isolated** список (новый JSON позже). Mixed 20 команд + document **не** менять.

**Владелец новых `start` / `help` / `status`:** `modules.antares.handlers` (тот же модуль, что 11 уже выделенных command callbacks). Отдельный `control.py` **не** вводится.

| # | Handler | Isolated callback | Владелец | ACL | Mixed |
|---|---------|-------------------|----------|-----|-------|
| 1 | `start` | **новый** `cmd_start` | `modules.antares.handlers` | `start` | mixed `cmd_start` остаётся в `tg_commands` (другой объект) |
| 2 | `help` | **новый** `cmd_help` | то же | `help` | mixed `_help_text()` без изменений |
| 3 | `status` | **новый** `cmd_status` | то же | `status` | mixed `cmd_status` остаётся; `KNOWN_JOB_TYPES` без изменений |
| 4 | `whoami` | перенос тела | то же | `whoami` | identity re-export |
| 5 | `reload_rules` | перенос тела | то же | `reload_rules` | identity re-export |
| 6–9 | `run_wallet` / `hourly` / `download` / `rate` | уже есть | то же | command = имя | уже re-export |
| 10 | `operator_wallets_ready` | уже есть | то же | `operator_wallets_ready` | уже re-export |
| 11 | `rules_validate` | перенос тела | то же | `rules_validate` | identity re-export |
| 12–13 | `auto_enable_plan` / `run` | уже есть | то же | те же ACL | уже re-export |
| 14 | `wallet_editor_refresh` | уже есть | то же | `wallet_editor_refresh` | уже re-export |
| 15–17 | `registry_health` / `replay` / `export` | уже есть | то же | те же ACL | уже re-export |
| 18 | `filters.Document.ALL` | уже есть | `document_ingest` | chat allowlist | mixed via `wallet_editor_tg` identity |

**Не входят:** `run_raccoon`, `run_hourly_raccoon`, `run_script_hello`.

Isolated: **17 CommandHandler + 1 MessageHandler**. Mixed: **20 + 1**.

---

## 3. Registry = реестр dispatch

`core.tg_command_dispatch.run_job_async` → `dispatch_job_async` → `request_job` читает **`core.job_runner.JOB_REGISTRY.get(job_type)`**. Произвольный `dict`, переданный в сборку, этот путь **не** переключает. Обобщённую DI для dispatch **не** вводить.

### Контракт этапа 1

- Сборка пишет **только** в фактический `core.job_runner.JOB_REGISTRY`.
- Если у функции есть параметр `registry`, допустим **только** `registry is JOB_REGISTRY`; иначе отказ **до** bind и **до** записи в registry.
- Отдельный mapping **не** называть рабочей сборкой.
- Процесс: **fresh**, без предварительного `import integrations.tg_commands` / `raccoon_jobs` / package bootstrap `script_jobs`.
- Чужие ключи в `JOB_REGISTRY` (raccoon, `hello_world`, любой лишний) → **явный отказ**. **Не** `clear()`. **Не** удалять Raccoon/чужие ключи как способ «починить» процесс.
- После **успешной** сборки: `set(JOB_REGISTRY) ==` ровно семь ключей § 5, и `request_job` видит те же callables.

### Повторный вызов

Проверяемые предусловия **до** изменения bind и registry:

| Ситуация | Итог |
|----------|------|
| Те же объекты `rules` и `logger`, что уже bound; `JOB_REGISTRY` уже ровно семь согласованных ключей с **теми же** executor objects | допустим (no-op или повторная запись тех же callables) |
| Другой `rules` / `logger`, чем bound | отказ |
| Конфликтующий executor на одном из семи ключей (другой callable) | отказ |
| Чужие ключи | отказ (см. выше) |
| `registry is not JOB_REGISTRY` | отказ |

### Сбой импорта / частичная регистрация

Если импорт downloader/script bind/handlers падает **после** начала записи в registry или bind: сборка **неуспешна**; частичный `JOB_REGISTRY` / частичный bind **не использовать**; polling/worker/schedules **запрещены**. Откат через `clear()` или вырезание чужих ключей **не** делать. Процесс считать грязным; для новой попытки — **новый процесс**.

---

## 4. Selective script API (без авторегистрации)

### Факт импорта (SHA обследования)

```
integrations.script_jobs.__init__
  → registry.py  (тянет operator_wallets_ready)
  → runtime.py
      → module-level register_script_jobs()   # оба ключа в JOB_REGISTRY
```

Любой `from integrations.script_jobs.<sub> import …` **сначала** выполняет пакетный `__init__.py`. Параметр `keys=` у **текущей** `register_script_jobs` **недостаточен**: `hello_world` уже может оказаться в `JOB_REGISTRY` до вызова.

Запрещены как изоляция: env-флаг; удаление `hello_world` после импорта; `JOB_REGISTRY.clear()`.

### Транзитив `operator_wallets_ready` (не «просто тяжёлый import»)

`integrations/script_jobs/registry.py` делает:

`from integrations.script_jobs.scripts.operator_wallets_ready import run_operator_wallets_ready`

Этот модуль делает `from main import CONVERSION_COLUMNS`. **`main.py` на import** загружает:

- `analyzers.selector` (`get_analyzer`);
- `integrations.dropbox_watcher` (`download_file`, `move_file`);
- `integrations.telegram_bot` (`send_message_sync`);
- `run_once_guard`;
- `utils.logger` (handlers на `logs/app.log`).

Это **не** выполнение `run_operator_wallets_ready`, но это загрузка selector, Dropbox-клиента и Telegram send stack в процесс сборки, если импортировать `registry.py` или `operator_wallets_ready.py`. Isolated сборка **не должна** импортировать эти модули на этапе bind.

### Выбранное решение

| Модуль | Роль | Import-time регистрация в JOB_REGISTRY |
|--------|------|----------------------------------------|
| `integrations/script_jobs/identity.py` (**новый**) | `SCRIPT_JOB_PREFIX`, `script_job_type`, `parse_script_job_type` | нет |
| `integrations/script_jobs/bind.py` (**новый**) | `register_script_job(script_key)` пишет **lazy** callable в `JOB_REGISTRY` (только `JOB_REGISTRY`, identity). Lazy `import` runner — при **`request_job`**, не при bind | нет, пока не вызвать функцию |
| `integrations/script_jobs/runtime.py` | исполнение `run_script_job`; **убрать** module-level `register_script_jobs()` | нет после правки |
| `integrations/script_jobs/registry.py` | `SCRIPT_REGISTRY` (hello + operator spec) | нет (спеки, не JOB_REGISTRY) |
| `integrations/script_jobs/bootstrap.py` (**новый**) | `register_all_script_jobs()` — оба ключа, как сегодня, для **mixed** | только при явном вызове |
| `integrations/script_jobs/__init__.py` | реэкспорт types/identity/**без** import `runtime` и **без** вызова register | нет |

Isolated импортирует **`integrations.script_jobs.bind`** (пакетный `__init__` больше не bootstrap'ит JOB_REGISTRY) и вызывает `register_script_job("operator_wallets_ready")`. Не импортирует `registry.py` / `runtime.py` / `scripts.operator_wallets_ready` на bind.

Mixed: заменить `from integrations import script_jobs  # noqa: F401` в `integrations/tg_commands.py` на явный `from integrations.script_jobs.bootstrap import register_all_script_jobs` + вызов. Оба jobs как сейчас.

`bind.py` не импортирует `registry.py`. Executor на ключ `script_job:operator_wallets_ready` при первом `request_job` импортирует `runtime` → тогда уже `registry` / `operator_wallets_ready` / `main` — это **исполнение**, не сборка.

### Затрагиваемые существующие пути (подэтап 1)

- `integrations/script_jobs/__init__.py`, `runtime.py`
- `integrations/tg_commands.py` (явный bootstrap)
- тесты, которые считают `import integrations.script_jobs` регистрацией: `tests/unit/test_script_jobs_registry.py`, `test_script_jobs_runtime.py`, `test_script_jobs_operator_wallets_ready.py` (строки `import integrations.script_jobs  # bootstrap`)
- `test_script_jobs_delivery.py` импортирует `delivery` — после slim `__init__` не должен регистрировать jobs; проверить, что тест не зависит от авторегистрации

Это **первый code PR**, отдельно от полной сборки handlers.

---

## 5. Jobs isolated Antares

Семь ключей, согласованных с `request_job`:

```
wallet
hourly
rate
download
wallet_editor_registry_refresh
wallet_editor_registry_replay
script_job:operator_wallets_ready
```

**Запрещены:** `raccoon_*`, `script_job:hello_world`.

`modules.antares.jobs.register_jobs` сегодня принимает mapping; на этапе 1 вызывать **только** как `register_jobs(JOB_REGISTRY)` (тот же объект). При вызове импортирует bakai/downloader_wallets/registry refresh — регистрация, не run. Тест сборки не вызывает эти callables.

---

## 6. Antares `start` / `help` / `status`

Владелец: **`modules.antares.handlers`**. Mixed callbacks не reuse.

`start`: `"Ок.\nЯ готов.\n\n"` + Antares help (не mixed `_help_text()`).

`help`: перечень isolated команд § 2, без `/run_raccoon`, `/run_hourly_raccoon`, `/run_script_hello`. Mixed `_help_text()` не менять.

### `/status` — блоки (не «mixed observation минус locks»)

Константа isolated locks/jobs: `ANTARES_STATUS_JOB_TYPES` = те же **семь** ключей § 5. Mixed `KNOWN_JOB_TYPES` **не** менять и **не** передавать в Antares status.

| Блок | Источник | Область | Фильтр | До запуска scheduler (этап 1 / нет loop) | Нет данных |
|------|----------|---------|--------|------------------------------------------|------------|
| Observation off: running jobs | `core.job_runner.get_status()` | `_RUNNING` процесса | **да**: показать только ключи из семи; чужой ключ в `_RUNNING` не выводить | пустой `_RUNNING` | `🟢 Сейчас ничего не выполняется.` (как mixed idle) |
| Observation on: scheduler | `get_scheduler_health_snapshot()` | in-memory tick/error/schedule count/hourly gate этого процесса | нет job-keys (это scheduler, не raccoon jobs) | `tick_ts`/`tick_age`/`active_schedules` = `unknown`; `last_error` = `none`; gate reason `none`, age `unknown` | те же `unknown`/`none` в существующих форматах строк mixed |
| Observation on: telegram_sender | `get_telegram_sender_health_snapshot()` | in-memory sender процесса | нет (не каталог jobs) | счётчики 0, ages `none`/`unknown` по текущему snapshot | `- unknown` при exception (как mixed) |
| Observation on: telegram_routes | `format_telegram_routes_status_lines()` | workbook routes процесса (hourly/wallet/conversion/bakai **Antares Test**, не `raccoon_*` jobs) | **нет** фильтра по семи keys: это маршруты отчётов, не JOB_REGISTRY. Не вызывать, если нужен raccoon-only catalog — его здесь нет | может читать rules snapshot при вызове **status**, не при сборке; `loaded`/`missing_sheet` как helper | `telegram_routes:` + `- unknown` |
| Observation on: **locks** | `get_lock_status_for_job_types(ANTARES_STATUS_JOB_TYPES)` | PID lock files | **только семь keys** | `pid=none age=none` если файла нет (`lock_status._read_lock_fields`) | `- unknown` при exception |
| Observation on: conversion | `state_get("conversion", …)` как `_format_conversion_observation_lines` | Antares conversion state_store, **не** ключ JOB_REGISTRY | не job-key filter; это не raccoon. Блок **оставить**: операционное состояние Test/Antares | `- no data` если status/run ts отсутствуют | `- no data` / `- unknown` |
| Observation on: job_health | `get_job_health_snapshot` / разбор `jobs` | сейчас `format_job_health_lines()` итератор **`KNOWN_JOB_TYPES` (есть raccoon)** | **нельзя** вызывать mixed `format_job_health_lines()` as-is. Antares формат: `mode` + `executor_queue_depth` + строки **только** по семи keys | mode `off` или snapshot пустой → `state=unknown`/`idle` по текущей логике helper для отсутствующих jt | `job_health:` + `- unknown` при exception |
| Observation on: jobs | `get_status()` | `_RUNNING` | **да**, только семь keys | `🟢 idle` | `🟢 idle` / `unknown` при exception |

Почему каждый из семи в **locks** (и в job_health per-job):

| Ключ | Locks / job_health | Почему |
|------|--------------------|--------|
| `wallet` `hourly` `rate` `download` | **включены** | `request_job` берёт PID lock по `job_type`; mixed уже показывает первые три + download |
| `wallet_editor_registry_refresh` | **включён** | в mixed `KNOWN_JOB_TYPES`; job в семи |
| `wallet_editor_registry_replay` | **включён** | job в семи; mixed status **сейчас не** показывает этот lock — isolated **согласует со сборкой**, не копирует пропуск mixed |
| `script_job:operator_wallets_ready` | **включён** | job в семи; `job_runner` лочит `script_job:operator_wallets_ready`; mixed `KNOWN_JOB_TYPES` script jobs **не** содержит — isolated не копирует этот пробел |
| `raccoon_*` / `hello_world` | **исключены** | нет в семи; не читать их lock files |

---

## 7. Последовательность сборки

1. Профиль — только будущий isolated entry. Mixed gate не трогать.
2. Создать `AccessRules(...)` + MAIN logger (конструктор workbook не читает).
3. Предусловия § 3 (identity registry, чужие ключи, bind).
4. `bind_rules` / `bind_logger`.
5. `register_jobs(JOB_REGISTRY)` + `register_script_job("operator_wallets_ready")`.
6. Собрать handlers § 2.
7. `Application` / polling / `ensure_worker_started` / `schedule_loop` — **этап 3**, не этап 1.

Этапы 1–4 как прежде: сборка без запуска; entrypoint нет; polling только mixed; cutover нет.

---

## 8. Проверки реальной сборки (будущие code PR)

Разделить тесты:

| Слой | Что проверять | Subprocess |
|------|-----------------|------------|
| Import модуля сборки | нет `tg_commands`, `raccoon_jobs`, `raccoon_*`; `JOB_REGISTRY` ещё **не** обязан быть семёркой (bind не вызван) | да, fresh |
| Вызов функции сборки | успех; handlers 17+document | тот же процесс после import **или** отдельный вызов в том же child |
| Фактический `JOB_REGISTRY` | `JOB_REGISTRY is` объект, из которого `request_job` делает `.get`; `set(keys)==` семь; `hello_world` нет | **реальные** `job_runner.JOB_REGISTRY`, `modules.antares.jobs.register_jobs`, `script_jobs.bind` — **не** stub этих модулей |
| Повторная сборка | same rules/logger/executors → OK | |
| Отказ при загрязнении | заранее вставить чужой ключ → отказ, ключ **остаётся**, семёрка не «вычищается» | |
| Запрещённые импорты **после вызова** сборки | `sys.modules` не содержит `integrations.tg_commands`, `integrations.raccoon_jobs`, `integrations.raccoon_wallet_downloader` и т.п. | не подменять raccoon stub'ом sitecustomize: stub маскирует запрещённый import |
| Сбой импорта | если forced failure mid-register — процесс не стартует; тест не использует частичный registry как success | |

Внешние границы (Playwright, live Telegram, PG, Dropbox download) — stubs **вне** registration path.

Mixed baseline — **отдельный** прогон: inventory, registration harness, `test_help_text_lists_raccoon_commands`, script_jobs tests после явного bootstrap. Не смешивать с isolated subprocess.

---

## 9. Следующий code scope

### Подэтап 1 (первый code PR) — обязателен раньше полной сборки

Файлы:

- `integrations/script_jobs/__init__.py` — без import `runtime`, без авторегистрации
- `integrations/script_jobs/runtime.py` — удалить module-level `register_script_jobs()`
- `integrations/script_jobs/identity.py` — новый
- `integrations/script_jobs/bind.py` — новый, lazy, только `JOB_REGISTRY`
- `integrations/script_jobs/bootstrap.py` — новый, оба jobs для mixed
- `integrations/tg_commands.py` — явный `register_all_script_jobs()`
- `tests/unit/test_script_jobs_{registry,runtime,operator_wallets_ready}.py` — явный bootstrap вместо import-side-effect
- тесты: isolated import `bind` в fresh process **не** создаёт `script_job:hello_world`; не загружает `main` / `analyzers.selector` / `dropbox_watcher`

Не в этом PR: Antares `get_handlers`, start/help/status, entrypoint, gate, `JOB_ACCEPT`.

### Подэтап 2 — сборка этапа 1

- `modules.antares.assembly` (имя фиксируется в code PR) + `cmd_start`/`cmd_help`/`cmd_status` в **`handlers.py`**
- перенос whoami / reload_rules / rules_validate + mixed re-export
- `register_jobs(JOB_REGISTRY)` + `register_script_job("operator_wallets_ready")`
- тесты § 8; mixed JSON без изменений
- не менять `scheduler.py` / `project_profile_boot.py`

Вне обоих: этап 2–4, Railway, merge #4–#17.

---

## 10. Нерешённые блокеры

Для **плана** этапа 1 после этого уточнения блокирующих UNKNOWN нет.

Ограничения:

1. Пока подэтап 1 не сделан, isolated **не может** импортировать пакет `script_jobs` без авторегистрации hello_world — поэтому сборка не объединяется с bind-split.
2. `register_jobs()` по-прежнему импортирует downloader modules при вызове; не run.
3. Грязный процесс после неуспешной частичной регистрации не восстанавливается in-process.
4. Isolated entry / `PROJECT_PROFILE=antares` на mixed scheduler — другая задача.

`JOB_ACCEPT`, durable inbox, cutover — не блокеры этого плана.
