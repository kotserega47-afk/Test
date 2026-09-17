# План отделения Telegram handlers (TASK-2026-09-17-06)

| Мета | Значение |
|------|----------|
| **Статус** | DRAFT design; **не** runtime |
| **Репозиторий** | `deniskotdavydov1991-wq/Test` |
| **Обследованный SHA** | `0ce4d5340fdbe5ea890c1a31a5cec6dfbbcff66f` (код handlers = `a6d7ebcf…` / jobs `6db95d76…`; последующие коммиты TASK-05 — документация) |
| **Источник handlers** | `integrations/tg_commands.py` `get_handlers()` |
| **Эталон mixed** | `tests/fixtures/behavior_baseline/expected_tg_commands.json` (**не** менять под isolated) |

Это обследование **исходников**, не runtime-прогон и не production. Isolated Antares entry **ещё нет**. Legacy mixed остаётся рабочим режимом.

---

## 1. Карта обработчиков

ACL команд: `_guard_or_deny(update, "<command>")` → `check_access(RULES, ctx, command)` + `deny_message`.  
Исключение: document ingest **не** использует `_guard_or_deny`; чат-allowlist `WALLET_EDITOR_ALLOWED_CHAT_IDS` в `integrations/wallet_editor_tg.py`.

Глобали `tg_commands`: `RULES = AccessRules(RULES_XLSX_PATH)`, `log`, `MSK`. Импорт модуля регистрирует jobs (`register_jobs`, `raccoon_jobs`, `script_jobs`).

Порядок ниже = порядок `get_handlers()` (контракт mixed).

| # | Handler | Callback | ACL | Операция / job | Зависимости | Владелец | Тесты |
|---|---------|----------|-----|----------------|-------------|----------|-------|
| 1 | `start` | `cmd_start` | `start` | reply + **полный** `_help_text()` (Antares+Raccoon+WE+scripts) | `RULES`, `_help_text` | mixed-compat (текст mixed) | нет отдельного; AST `get_handlers` |
| 2 | `help` | `cmd_help` | `help` | `_help_text()` mixed-перечень | то же | mixed-compat | `tests/test_raccoon_tg_commands.py::test_help_text_lists_raccoon_commands` |
| 3 | `status` | `cmd_status` | `status` | observation **или** `get_status()` всех running jobs | `OBSERVATION_ENABLED`; locks `KNOWN_JOB_TYPES` (вкл. raccoon); conversion state; TG sender; routes; job_health | mixed-compat | `tests/test_cmd_status_observation.py`; `test_tg_concurrent_handlers.py`; `test_raccoon_tg_commands.py` (status+dispatch); `test_wallet_editor_tg_integration.py` |
| 4 | `whoami` | `cmd_whoami` | `whoami` затем `RULES.get_snapshot().access_map` | ответ chat/user/level | `RULES` | смешанный ACL (workbook процесса) | нет dedicated |
| 5 | `reload_rules` | `cmd_reload_rules` | `reload_rules` | `RULES.invalidate` + `get_snapshot(force_sync=True)` + `request_scheduler_clocks_reset` | rules snapshot, scheduler clocks | mixed-compat (один workbook процесса) | `tests/test_scheduler_clocks_reset.py` |
| 6 | `run_wallet` | `cmd_run_wallet` | `run_wallet` | `_run_job_async(..., "wallet")` | `dispatch_job_async`, `Actor` | **Antares** | нет dedicated cmd; job identity — registration harness |
| 7 | `run_hourly` | `cmd_run_hourly` | `run_hourly` | job `hourly` | то же | **Antares** | `tests/test_tg_concurrent_handlers.py` |
| 8 | `run_download` | `cmd_run_download` | `run_download` | job `download` | то же | **Antares** | нет dedicated |
| 9 | `run_rate` | `cmd_run_rate` | `run_rate` | job `rate` | то же | **Antares** | нет dedicated |
| 10 | `run_raccoon` | `cmd_run_raccoon` | `run_raccoon` | job `raccoon_wallet` | то же | **Raccoon** | `tests/test_raccoon_tg_commands.py` |
| 11 | `run_hourly_raccoon` | `cmd_run_hourly_raccoon` | `run_hourly_raccoon` | job `raccoon_hourly` | то же | **Raccoon** | то же |
| 12 | `run_script_hello` | `cmd_run_script_hello` | `run_script_hello` | job `script_job:hello_world` | то же | **generic script** (нет Antares-данных) | `tests/unit/test_script_jobs_tg_command.py` |
| 13 | `operator_wallets_ready` | `cmd_operator_wallets_ready` | `operator_wallets_ready` | job `script_job:operator_wallets_ready` | download Antares wallets, `CONVERSION_COLUMNS` | **Antares** (по содержимому, не по префиксу) | `tests/unit/test_script_jobs_operator_wallets_ready.py` |
| 14 | `rules_validate` | `cmd_rules_validate` | `rules_validate` | `build_rules_validate_telegram_chunks_with_payload` + audit hook | rules_v2 ops | mixed-compat (workbook процесса) | `tests/test_access_guard_commands_map.py` (ACL map, не текст) |
| 15 | `auto_enable_plan` | `cmd_auto_enable_plan` | `auto_enable_plan` | `run_auto_enable_plan(actor, manual=True)` **не** JOB_REGISTRY | WE Auto-Enable | **Antares WE** | `tests/unit/test_wallet_editor_auto_enable_orchestrator.py` |
| 16 | `auto_enable_run` | `cmd_auto_enable_run` | `auto_enable_run` | `run_auto_enable(actor, manual=True)` | WE Auto-Enable | **Antares WE** | то же |
| 17 | `wallet_editor_refresh` | `cmd_wallet_editor_refresh` | `wallet_editor_refresh` | job `wallet_editor_registry_refresh` | `_run_job_async` | **Antares WE** | `tests/unit/test_wallet_editor_registry_refresh.py` (ACL allow/deny) |
| 18 | `registry_health` | `cmd_registry_health` | `registry_health` | `build_registry_health_report` sync | WE registry | **Antares WE** | нет dedicated cmd |
| 19 | `registry_replay` | `cmd_registry_replay` | `registry_replay` | `replay_pending_outbox_records()` **не** job `wallet_editor_registry_replay` | WE outbox | **Antares WE** | нет dedicated cmd |
| 20 | `registry_export` | `cmd_registry_export` | `registry_export` | PG export + `reply_document` | `InputFile`, postgres builder | **Antares WE** | `tests/unit/test_tg_registry_export.py` |
| 21 | `filters.Document.ALL` | `handle_wallet_editor_document` | chat allowlist, не `check_access` | очередь WE xlsx | `integrations/wallet_editor_tg.py`, worker | **Antares WE** | `tests/unit/test_wallet_editor_tg_integration.py` |

Инвентаризация имён/порядка `CommandHandler`: `tests/test_behavior_baseline_inventory.py` (AST). AST также фиксирует наличие `MessageHandler`, **не** `filters.Document.ALL`. Конкретный `Document.ALL`: `tests/test_behavior_baseline_registration.py`. Wiring scheduler: `tests/unit/test_project_profile_boot.py`.

### Группы (не путать имя с изоляцией)

**Общие по имени, mixed по содержимому:** `start`/`help` (перечень Raccoon), `status` (все job_type + raccoon locks), `whoami`/`reload_rules`/`rules_validate` (AccessRules и workbook текущего процесса). Isolated Antares **не** может брать эти callbacks без смены текстов/`KNOWN_JOB_TYPES`.

**Antares run (тонкий dispatch):** `run_wallet` / `run_hourly` / `run_download` / `run_rate` → jobs TASK-05.

**Raccoon:** `run_raccoon` → `raccoon_wallet`; `run_hourly_raccoon` → `raccoon_hourly`. Остаются в mixed `tg_commands` до отдельного raccoon handler PR.

**Script jobs:**  
- `hello_world` — заглушка без кабинета/карт; **не** Antares-бизнес.  
- `operator_wallets_ready` — выгрузка Antares-кошельков; **Antares**, несмотря на `script_job:*`.

**WE:** Auto-Enable (прямой orchestrator), registry (часть через job, replay/health/export — прямые вызовы), document ingest (другой ACL).

---

## 2. Границы кода (предложение)

| Слой | Содержимое |
|------|------------|
| `modules/antares/handlers.py` (будущий) | callbacks тонкого dispatch: `cmd_run_wallet|hourly|download|rate`; позже WE cmds и `operator_wallets_ready` |
| mixed `integrations/tg_commands.py` | `get_handlers()` как **сборка** mixed; help/status/start; raccoon cmds; `hello_world`; создание `RULES` и `log`; `bind_rules(RULES)` и передача `log`; вызов `register_jobs` + raccoon/script imports |
| `core/tg_command_dispatch.py` (TASK-07, узкий) | три helpers: `build_access_context` (сейчас `_ctx`), `guard_or_deny(update, command, rules)`, `run_job_async(update, job_type, logger)` — без имён Antares/Raccoon jobs, без help/status-текста. Не framework. Help/status **остаются** в `tg_commands` |
| `integrations/wallet_editor_tg.py` | document ingest остаётся; mixed только регистрирует `MessageHandler` |

### 2.1 `bind_rules(RULES)` — единственный способ передачи правил в TASK-07

Других вариантов (`configure`, замыкание на `RULES` из `tg_commands`, второй `AccessRules(...)`) **нет**.

Контракт (один runtime-контур на процесс; **не** несколько приложений с разными правилами в одном процессе):

- импорт `modules.antares.handlers` **не** создаёт `AccessRules` и **не** читает workbook;
- mixed создаёт `RULES = AccessRules(...)` как сейчас и сразу вызывает `bind_rules(RULES)` — передаётся существующий экземпляр;
- до `bind_rules` любой из четырёх Antares callbacks завершается **явной ошибкой конфигурации**: без `dispatch_job_async`, без reply «Запускаю»;
- повторный `bind_rules` **того же объекта** допустим;
- попытка подменить другим объектом `AccessRules` — отказ;
- `reload_rules` по-прежнему делает `invalidate` / `get_snapshot` **внутри того же** экземпляра; повторный bind после reload **не** нужен;
- handlers **не** хранят отдельную копию snapshot; ACL читает живой `AccessRules` при каждом вызове.

### 2.2 Logger и core `run_job_async`

`run_job_async(update, job_type, logger)` получает logger **параметром**. `core` и `modules.antares.handlers` **не** импортируют `integrations.tg_commands`.

Четыре Antares callbacks берут logger так же, как RULES: mixed создаёт существующий `log = get_logger(...)` (`_mk("MAIN")`) и вызывает `bind_logger(log)`. Callbacks передают этот объект в `run_job_async(..., logger=...)`. Handlers **не** вызывают `get_logger` сами (второй MAIN-logger не создаётся). Повторный bind того же объекта допустим; другой объект — отказ. До bind — та же явная ошибка конфигурации, без dispatch и без «Запускаю».

Для mixed сохранить текущее поведение `_run_job_async` (`integrations/tg_commands.py` на SHA обследования):

- `Actor(kind="tg", chat_id, user_id)` из `effective_chat` / `effective_user`;
- reply `🚀 Запускаю: {job_type}` **до** `dispatch_job_async`;
- при успехе reply `✅ Принято: {job_type}\njob_id={job_id}` **после** результата dispatch;
- в `except Exception`: `logger.exception("❌ TG job error: %s", job_type)`, reply «❌ Ошибка при выполнении.\nХвост трейса:», затем `err[-3500:]` (`traceback.format_exc()`);
- те же границы try/except: Actor и «Запускаю» снаружи try; dispatch и «Принято» внутри.

Остальные Raccoon / script / WE callers, которым нужны `_guard_or_deny` / `_run_job_async`, остаются на тонких совместимых именах в `tg_commands` (в т.ч. `patch("integrations.tg_commands._guard_or_deny")`). Тела — делегирование в core с `RULES` и `log` процесса. **Вычисление** общего поведения одно: в `core/tg_command_dispatch.py`.

**Где создаётся сегодня (SHA обследования):**

- `RULES` — import-time в `tg_commands.py`
- `log` — `get_logger` в `tg_commands`
- Telegram `Application` / polling — `scheduler.py` после `get_handlers()`
- Jobs — `register_jobs(JOB_REGISTRY)` в `tg_commands` (TASK-05)

**Запрет:** `modules.antares.*` → `integrations.tg_commands`. Re-export: `from modules.antares.handlers import cmd_run_hourly` в `tg_commands`.

---

## 3. Контракт совместимости (будущий code)

**Mixed (нельзя ломать без нового эталона):**

- имена и **порядок** 20 `CommandHandler` + `MessageHandler(filters.Document.ALL, handle_wallet_editor_document)`
- те же callback-объекты на re-export путях, которые тесты импортируют (`cmd_run_hourly`, `cmd_run_raccoon`, …)
- ACL до операции; тексты deny / «Запускаю» / «Принято» / хвост traceback
- `Actor(kind="tg", chat_id, user_id)` в `_run_job_async`
- `expected_tg_commands.json` **не** переписывать под isolated

**Isolated Antares (ожидание, отдельный JSON позже):**

Handlers: `start`, `help` (**без** raccoon строк), `status` (без raccoon locks / без raccoon jobs в help), `whoami`, `reload_rules`, `rules_validate`, четыре `run_*`, `run_script_hello` (опционально), `operator_wallets_ready`, WE cmds + document.  
**Не** включать `run_raccoon` / `run_hourly_raccoon`.  
Jobs: шесть ключей TASK-05 + `script_job:operator_wallets_ready` (+ опционально `hello_world`). Не raccoon keys.

Это **не** текущий mixed baseline.

---

## 4. Минимальный следующий code PR (TASK-07 candidate)

Один шаг, зеркало TASK-05 для **команд-dispatch**, не всего `tg_commands.py`.

**Перенос:**

| Файл | Что |
|------|-----|
| `core/tg_command_dispatch.py` | `build_access_context`, `guard_or_deny`, `run_job_async(..., logger)` — единственная реализация путей `_ctx` / `_guard_or_deny` / `_run_job_async` |
| `modules/antares/handlers.py` | `bind_rules`, `bind_logger`, четыре `cmd_run_*`; import **не** регистрирует handlers, **не** читает workbook, **не** импортирует `tg_commands` |
| `integrations/tg_commands.py` | `RULES` и `log` как сейчас; сразу `bind_rules(RULES)` и `bind_logger(log)`; тела четырёх cmd удалить; `from modules.antares.handlers import cmd_run_wallet, …`; `get_handlers()` без смены порядка; `_ctx` / `_guard_or_deny` / `_run_job_async` — тонкие обёртки над core (имена для Raccoon/script/WE и существующих `patch`) |

В TASK-07 три helpers **выделяются вместе** с четырьмя run-командами. Тексты `help`/`status`/`start` **не** переносятся.

**Не в этом PR:** help/status/start тексты; raccoon cmds; WE cmds; document handler; scheduler; isolated entry; `JOB_ACCEPT`; перенос downloaders/аналитики.

**Старые пути:** `integrations.tg_commands.cmd_run_hourly` (и wallet/download/rate) = те же function objects, что в `modules.antares.handlers`.

**Подключение mixed:** import callbacks в `get_handlers()` как сейчас по именам.

**Проверки (добавлять в code PR TASK-07; этот docs PR тесты не расширяет):**

Текущее (не путать слои):

- AST (`tests/test_behavior_baseline_inventory.py`): имена и **порядок** `CommandHandler`; факт наличия `MessageHandler` в `get_handlers`. **Не** проверяет `filters.Document.ALL`.
- subprocess registration (`tests/test_behavior_baseline_registration.py` + dump): `document_handlers == 1` и `handler.filters is filters.Document.ALL`.

Будущее (риски шага):

1. Identity callbacks: каждая из четырёх команд в собранном `get_handlers()` указывает на **ожидаемый function object** (`modules.antares.handlers.cmd_run_hourly` **is** `integrations.tg_commands.cmd_run_hourly`, и то же для wallet/download/rate).
2. `bind_rules` / `bind_logger`: unbound → ошибка конфигурации, без dispatch и без «Запускаю»; same-object bind OK; different-object bind отказ; после `invalidate`/`reload` на том же `AccessRules` callbacks используют обновлённые правила без повторного bind.
3. Существующие: `test_raccoon_tg_commands` (чужой job), `test_script_jobs_tg_command` (patch `_guard_or_deny`), `test_tg_concurrent_handlers`.
4. Deny ACL → `dispatch_job_async` не вызывается; allow → `job_type` `wallet`/`hourly`/`download`/`rate` точно.
5. Document filter не менять; падает **registration** dump (`document_filter_is_all`), не AST inventory.

**Этот шаг ещё не устраняет:** isolated Antares без импорта всего `tg_commands.py` (останутся help/raccoon/WE на том же модуле); mixed help/status; два script-job; WE ingest.

---

## 5. Открытые вопросы (не блокируют § 4)

1. Isolated `help`/`status`: отдельные callbacks vs параметр profile — решать **после** четырёх run-команд.
2. Держать ли `hello_world` на isolated Antares (удобство ops vs чистота профиля).
3. `cmd_registry_replay` vs job `wallet_editor_registry_replay` — разные пути; не смешивать в одном «тонком dispatch».

Передача `RULES` для TASK-07 закрыта: только `bind_rules(RULES)` (§ 2.1).
