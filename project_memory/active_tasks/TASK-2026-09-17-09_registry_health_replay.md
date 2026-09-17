# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-09 |
| **Статус** | in_progress (Draft PR на review; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-08 (PR #11), `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft, base `feat/task-2026-09-17-08-antares-dispatch-cmds` |
| **Риск** | medium: владение registry health/replay callbacks |

В `modules.antares.handlers` теперь **восемь** callbacks. Isolated entry и cutover **не** реализованы.

---

## Goal

`cmd_registry_health` и `cmd_registry_replay` живут в `modules.antares.handlers` как прямые операции (не job dispatch). Mixed re-export тех же function objects.

---

## Граница изменения

Перенесено: два callback. Registry/outbox/БД, JOB_REGISTRY, `registry_export` не менялись.

---

## Success Criteria

- [x] ACL `registry_health` / `registry_replay`; replay не через job
- [x] Identity re-export + восемь различимых callbacks
- [x] Mixed expected JSON без изменений
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

registry_export; Auto-Enable; document ingest; isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#11.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | registry health/replay callbacks перенесены в `modules.antares.handlers` |
