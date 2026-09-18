# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-11 |
| **Статус** | in_progress |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-10 (PR #13), `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft (будет заполнен после открытия) |
| **Риск** | medium: владение Auto-Enable Telegram callbacks |

`cmd_auto_enable_plan` и `cmd_auto_enable_run` переносятся в `modules.antares.handlers` как прямые операции через executor (не job-dispatch). Совместимый re-export и mixed `get_handlers()` сохраняются. Isolated entry, `JOB_ACCEPT` и cutover **не** входят. GPT review ещё не выполнялся.

---

## Goal

Оба Auto-Enable callback живут в `modules.antares.handlers` через существующие `bind_rules` / `bind_logger`, с прежними ACL `auto_enable_plan` / `auto_enable_run`, `Actor(kind="tg", ...)`, `manual=True` и прежними текстами ответов.

---

## Граница изменения

Переносятся только два callback. Orchestrator, executor Wallet Editor, eligibility, настройки, registry/outbox и бизнес-логика Auto-Enable не меняются. `expected_tg_commands.json` не меняется. Job-dispatch не вводится.

Совместимые пути: `integrations.tg_commands.cmd_auto_enable_plan` и `cmd_auto_enable_run` — те же function objects.

---

## Success Criteria

- [ ] Unbound / частичный bind — ошибка конфигурации до стартового ответа и orchestrator
- [ ] ACL до Actor, стартового ответа и executor
- [ ] plan вызывает только `run_auto_enable_plan`; run — только `run_auto_enable`
- [ ] Identity re-export + 11 различимых callbacks
- [ ] Mixed expected JSON без изменений
- [ ] GPT review **не** ставился как пройденный
- [ ] merge/deploy PR #4–#13 не выполнены

---

## Ограничения покрытия

Isolated Antares **не** готов. В mixed `tg_commands` остаются: `start`/`help`/`status`, Raccoon cmds, `run_script_hello`, document ingest. Регистрация Auto-Enable в mixed `get_handlers()` сохраняется.

---

## Out Of Scope

isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#13; Railway; профили; document ingest; правка текстов dry_run/approval_required.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-18 | выделение `cmd_auto_enable_plan` и `cmd_auto_enable_run` в `modules.antares.handlers` |
