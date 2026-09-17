# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-09 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-08 (PR #11), `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft [#12](https://github.com/deniskotdavydov1991-wq/Test/pull/12) `feat/task-2026-09-17-09-registry-cmds`, base `feat/task-2026-09-17-08-antares-dispatch-cmds` |
| **HEAD (проверен GPT)** | `b8085a2891c8c38df2947bb193fe572411336b41` |
| **Риск** | medium: владение registry health/replay callbacks |

`cmd_registry_health` и `cmd_registry_replay` **перенесены**. В `modules.antares.handlers` **восемь** callbacks. Прямые операции, ACL, ответы и обработка ошибок сохранены. Совместимые экспорты и mixed-сборка сохранены. Review пройден. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы. PR #12 остаётся Draft.

---

## Goal

`cmd_registry_health` и `cmd_registry_replay` живут в `modules.antares.handlers` как прямые операции (не job dispatch). Mixed re-export тех же function objects.

---

## Граница изменения

Перенесено: два callback. Registry/outbox/БД, JOB_REGISTRY, `registry_export` не менялись.

Совместимые пути: `integrations.tg_commands.cmd_registry_health` и `cmd_registry_replay`.

---

## Success Criteria

- [x] ACL `registry_health` / `registry_replay`; replay не через job
- [x] Identity re-export + восемь различимых callbacks
- [x] Mixed expected JSON без изменений
- [x] GPT review HEAD `b8085a28…`: блокирующих нет
- [ ] merge/deploy PR #12 (намеренно открыто)

---

## Ограничения покрытия

Isolated Antares **не** готов. В mixed `tg_commands` остаются: `start`/`help`/`status`, Raccoon cmds, `run_script_hello`, Auto-Enable, `registry_export`, document ingest. Registry/outbox/БД и JOB_REGISTRY не переносились.

Покрытие TASK-04/05/07/08 без изменения: нет полного эталона wallet file→DTO, payout-report, conversion Excel, raccoon daily conversion, Platform wallet-analyzer; регистрация jobs не доказывает production schedules.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **86 passed** на HEAD `b8085a28…`, Python **3.12.10** (`test_antares_handlers`, inventory, registration, profile boot, WE refresh, registry export) |
| GPT | Проверил код и diff HEAD `b8085a28…`. Набор 86 **независимо не запускал.** |

---

## Out Of Scope

registry_export; Auto-Enable; document ingest; isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#12; Railway; профили.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | registry health/replay callbacks перенесены в `modules.antares.handlers` |
| 2026-09-18 | GPT review HEAD `b8085a28…`: блокирующих нет; merge/deploy нет |
