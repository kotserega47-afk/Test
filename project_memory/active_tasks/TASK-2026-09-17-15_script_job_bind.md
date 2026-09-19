# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-15 |
| **Статус** | review (ожидает GPT; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-14 (PR #17, закрытие `99d2db55027b94cb7739efd18ff349b838b22a21`), `ops/MODULAR_REORG_ANTARES_ASSEMBLY.md` §4 подэтап 1 |
| **PR** | Draft [#18](https://github.com/deniskotdavydov1991-wq/Test/pull/18) `feat/task-2026-09-17-15-script-job-bind`, base `feat/task-2026-09-17-14-antares-assembly-plan` |
| **HEAD** | `6681e666d961b13ae3ab7f782845b92a1f23e078` (до коммита сужения изоляции; SHA коммита — следующим pin) |
| **Риск** | medium: смена способа регистрации script jobs в `JOB_REGISTRY` |

Selective script registration: `identity.py` / `bind.py` / `bootstrap.py`; пакетный `__init__` не импортирует runtime/registry и не регистрирует jobs. Mixed `tg_commands` явно вызывает `register_all_script_jobs()`. Исполнение scripts, delivery, Actor/context, правила и логика `operator_wallets_ready` не менялись. Isolated entry, сборка Antares, `JOB_ACCEPT` и cutover **не** реализованы. Review **не** пройден.

---

## Goal

Регистрация script jobs — явный whitelist bind на фактический `core.job_runner.JOB_REGISTRY`, без import-side-effect и без загрузки runtime до вызова executor.

---

## Граница изменения

Добавлено: `identity.py`, `bind.py`, `bootstrap.py`.

Изменено: slim `__init__.py`; `runtime.py` без module-level `register_script_jobs()`; `registry.py` реэкспорт identity; `tg_commands.py` явный mixed bootstrap.

Не менялись: `run_script` / `run_script_job` / delivery; handlers order; mixed expected JSON; Antares assembly; start/help/status; whoami/reload_rules/rules_validate; entrypoint; gate; cutover.

---

## Success Criteria

- [x] Import package и bind не регистрирует jobs
- [x] `register_script_job("operator_wallets_ready")` добавляет только выбранный ключ без runtime, registry, operator_wallets_ready, main, selector и telegram_bot. `dropbox_watcher` транзитивно импортируется через `core.job_runner` → `rules_provider`; отсутствие этого импорта текущая реализация не обеспечивает
- [x] Неизвестный ключ и конфликт (чужой callable или значение `None`) — отказ без изменения registry; повторный bind сохраняет identity executor
- [x] Mixed bootstrap — оба прежних script jobs, разные callables
- [x] Тесты import-side-effect адаптированы; script runtime/delivery сохранены
- [ ] GPT review: блокирующих нет
- [ ] merge/deploy PR (намеренно открыто)

---

## Ограничения покрытия

Isolated Antares **не** готов. `hello_world` остаётся в mixed. Живой script / браузер / БД / Telegram не запускались. Production этим PR не переключался. `dropbox_watcher` загружается при импорте `JOB_REGISTRY` через `core.job_runner` → `rules_provider`. Разделение `core.job_runner` в этот PR не входит.

Подтверждено: родительский pytest получал SIGINT (signum=2) — `asyncio.Runner._on_sigint` и `KeyboardInterrupt` в `Thread.start`/`Condition.wait`. Наблюдалось: daemon `Thread-1 (_loop_runner)` в `integrations.telegram_bot` после импорта настоящего модуля. Источник SIGINT **не установлен**. Не утверждается, что child `python -c` / registration dump шлёт `CTRL_C_EVENT`.

Глобальные `signal.signal(SIGINT, SIG_IGN)` и session-autouse остановка Telegram из `tests/conftest.py` сняты. Collection-stub `telegram_bot` и `async_test_runner` удалены. Parent pytest снова с обычным SIGINT. Тяжёлые импорты registry/runtime/delivery/operator/command идут в child `tests/unit/script_jobs_import_harness` (`sitecustomize` ставит stub Telegram только у ребёнка). Bind/bootstrap/`JOB_REGISTRY`, runtime/delivery и callbacks остаются реальными; внешние операции патчатся в тестах. Узкий stub `integrations.script_jobs.runtime` сохранён только в forwarding/error тестах bind. Timeout ребёнка — ошибка, не успех. Внутренний assertion ребёнка даёт ненулевой exit и падение parent-wrapper с stdout/stderr ребёнка (проба `probe-fail-visible`, parent EXIT=1).

### Соответствие прежних кейсов

Parent-wrapper — один тест на файл; число wrappers не равно числу внутренних кейсов.

| Область | Где | Кейсы |
|---------|-----|--------|
| identity / JOB_REGISTRY bootstrap | parent `test_script_jobs_registry.py` | `test_script_job_type_format`, `test_job_registry_contains_script_job_hello_world`, `test_bootstrap_does_not_remove_existing_job_registry_entries` |
| SCRIPT_REGISTRY | child, wrapper `test_script_registry_contents_in_import_harness` | `test_script_registry_contains_hello_world` |
| runtime / lifecycle | child, wrapper `test_script_jobs_runtime_in_import_harness` | `test_run_script_unknown_key_fails_closed`, `test_run_script_executes_and_returns_result`, `test_run_script_handles_execution_failure`, `test_script_lock_paths_are_distinct`, `test_script_a_does_not_block_script_b`, `test_same_script_blocks_concurrent_run` (+ `assert not thread.is_alive()`), `test_request_job_lifecycle_for_script_job`, `test_build_execution_context_manual_has_chat_id`, `test_build_execution_context_scheduled_has_route_from_params` |
| operator_wallets_ready | child, wrapper `test_script_jobs_operator_wallets_ready_in_import_harness` | прежние 19 функций, включая deny/allow command (`test_operator_wallets_ready_command_is_guarded`, `test_operator_wallets_ready_dispatches_script_job`) |
| delivery | child, wrapper `test_script_jobs_delivery_in_import_harness` | прежние 6 функций |
| command deny/allow hello | child, wrapper `test_script_jobs_tg_command_in_import_harness`; внутри `asyncio.run` | `test_run_script_hello_command_is_guarded`, `test_run_script_hello_dispatches_script_job_when_allowed` |
| job params | parent `test_script_jobs_job_params.py` (без wrapper) | `test_script_job_hello_world_in_legacy_whitelist`, `test_hourly_params_still_resolve_when_script_job_rows_present` |

Bind/None conflict тесты в parent `test_script_jobs_bind.py` не переносились.

### Прогоны (Python 3.12.10, worktree TASK-15, Start-Process, HasExited=True)

1. `py -3.12 -m pytest tests/unit/test_script_jobs_bind.py tests/unit/test_script_jobs_registry.py tests/unit/test_script_jobs_runtime.py tests/unit/test_script_jobs_operator_wallets_ready.py tests/unit/test_script_jobs_delivery.py tests/unit/test_script_jobs_tg_command.py tests/unit/test_script_jobs_job_params.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py -q --tb=line` → **28 passed**, 0 failed, 0 skipped, **EXIT=0**
2. `py -3.12 -m pytest tests/unit/test_project_profile_boot.py -q --tb=line` → **23 passed**, 0 failed, 0 skipped, **EXIT=0**
3. `py -3.12 -m pytest tests/unit/test_script_jobs_tg_command.py -q --tb=short` (без соседних файлов) → **1 passed** (wrapper; в ребёнке оба command-кейса), 0 failed, 0 skipped, **EXIT=0**

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | прогоны выше; GPT новый diff изоляции ещё не проверял |
| GPT | ещё не проверял этот diff |

---

## Out Of Scope

Antares assembly; новые start/help/status; перенос whoami/reload_rules/rules_validate; entrypoint; изменение gate; `JOB_ACCEPT`; cutover; Railway; профили production; merge #4–#17.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-19 | selective script registration; статус — готово к GPT review |
| 2026-09-19 | bind conflict для `None`; узкий child-harness вместо глобального SIG_IGN; review не пройден |
| 2026-09-19 | сняты глобальный SIG_IGN и collection telegram stub; тяжёлые импорты в child harness; asyncio.run возвращён; статус review |
