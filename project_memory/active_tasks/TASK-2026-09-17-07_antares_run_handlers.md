# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-07 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-06 (PR #9), `ops/MODULAR_REORG_HANDLER_SPLIT.md` § 4 |
| **PR** | Draft [#10](https://github.com/deniskotdavydov1991-wq/Test/pull/10) `feat/task-2026-09-17-07-antares-handlers`, base `feat/task-2026-09-17-06-handler-plan` |
| **HEAD (проверен GPT)** | `94be31279657da4255650aa2c08ac226f9490219` |
| **План** | `380a4bb57d161ad39897cd32c5a6de2a64a57668` |
| **Риск** | medium: владение четырьмя Antares run-командами и общими TG helpers |

Четыре Antares `run_*` **выделены**. Три helper — `core.tg_command_dispatch`. `bind_rules` / `bind_logger` реализованы. Совместимые экспорты и mixed `get_handlers()` сохранены. Review пройден. Isolated entry и cutover **не** реализованы. PR #10 остаётся Draft.

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
- [x] GPT review HEAD `94be3127…`: оба замечания закрыты, блокирующих нет
- [ ] merge/deploy PR #10 (намеренно открыто)

---

## Ограничения покрытия

Isolated Antares **не** готов. По-прежнему в mixed `tg_commands`: `start`/`help`/`status` (mixed-перечни), Raccoon cmds, WE cmds, document ingest, script jobs. Dispatch для оставшихся callers идёт через тонкие обёртки над core.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **180 passed** на HEAD `898af4e9…` (связанный набор handlers/inventory/registration/jobs/parser+gate/raccoon/scripts/concurrent/ACL/reload/WE callers). **Не** повторялся на `94be3127…`. |
| Cursor | **33 passed** на HEAD `94be3127…`, Python **3.12.10** (`tests/test_antares_handlers.py`, `tests/test_behavior_baseline_inventory.py`, `tests/test_behavior_baseline_registration.py`, `tests/test_scheduler_clocks_reset.py`) |
| GPT | Проверил код и diff HEAD `94be3127…`. Наборы 180 и 33 **независимо не запускал.** |

---

## Out Of Scope

help/status/start; Raccoon/WE/document; isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#10; Railway; профили.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Выделены четыре Antares run-команды и три core helper |
| 2026-09-17 | Тесты: AsyncFunctionDef в AST; reload через новый snapshot AccessRules |
| 2026-09-17 | GPT review HEAD `94be3127…`: блокирующих нет; merge/deploy нет |
