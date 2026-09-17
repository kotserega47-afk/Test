# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-07 |
| **Статус** | in_progress (Draft PR на review; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-06 (PR #9), `ops/MODULAR_REORG_HANDLER_SPLIT.md` § 4 |
| **PR** | Draft, base `feat/task-2026-09-17-06-handler-plan` |
| **План** | `380a4bb57d161ad39897cd32c5a6de2a64a57668` |
| **Риск** | medium: владение четырьмя Antares run-командами и общими TG helpers |

Четыре Antares `run_*` и три helper выделены. Isolated entry и cutover **не** реализованы. Mixed JSON не менялся.

---

## Goal

`cmd_run_wallet|hourly|download|rate` живут в `modules.antares.handlers` без импорта `integrations.tg_commands`. Общее поведение ACL/dispatch — в `core.tg_command_dispatch`. Mixed собирает те же function objects и передаёт `RULES`/`log` через `bind_rules` / `bind_logger`.

---

## Граница изменения

Перенесено: три helpers; четыре callbacks; bind контракт.

Не перенесено: help/status/start, Raccoon, WE, document ingest, аналитика, downloaders, scheduler, isolated entry.

Совместимые пути: `integrations.tg_commands.cmd_run_*` и `_ctx` / `_guard_or_deny` / `_run_job_async`.

`core` не импортирует `modules.antares`.

---

## Success Criteria

- [x] `bind_rules` / `bind_logger` identity; unbound/partial без dispatch и без «Запускаю»
- [x] ACL имя команды с `run_`; job_type wallet/hourly/download/rate; Actor tg
- [x] Re-export identity + registration dump Document.ALL
- [x] Mixed `expected_tg_commands.json` без изменений
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **180 passed**, Python **3.12.10**, связанный набор handlers/inventory/registration/jobs/parser+gate/raccoon/scripts/concurrent/ACL/reload/WE callers |
| GPT | ещё не ревьюил этот code PR |

---

## Out Of Scope

help/status/start; Raccoon/WE/document; isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#9; Railway; профили.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Выделены четыре Antares run-команды и три core helper |
