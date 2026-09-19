# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-15 |
| **Статус** | review (ожидает GPT; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-14 (PR #17, закрытие `99d2db55027b94cb7739efd18ff349b838b22a21`), `ops/MODULAR_REORG_ANTARES_ASSEMBLY.md` §4 подэтап 1 |
| **PR** | Draft [#18](https://github.com/deniskotdavydov1991-wq/Test/pull/18) `feat/task-2026-09-17-15-script-job-bind`, base `feat/task-2026-09-17-14-antares-assembly-plan` |
| **HEAD** | фиксируется docs-коммитом после прогона |
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

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10**. Набор script_jobs+inventory+registration: **64 passed**, exit **0**, процесс завершился сам. `test_project_profile_boot`: **23 passed**, exit **0**, процесс завершился сам. Зависание: не job_runner; SIGINT (signum=2) в родительский pytest после subprocess bind/registration на Windows, далее `asyncio.run`/`Thread.start`. Исправление только в тестах. |
| GPT | ещё не проверял |
| GPT | ещё не проверял |

---

## Out Of Scope

Antares assembly; новые start/help/status; перенос whoami/reload_rules/rules_validate; entrypoint; изменение gate; `JOB_ACCEPT`; cutover; Railway; профили production; merge #4–#17.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-19 | selective script registration; статус — готово к GPT review |
| 2026-09-19 | bind conflict для `None`; изоляция pytest от SIGINT subprocess; review не пройден |
