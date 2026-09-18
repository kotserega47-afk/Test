# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-10 |
| **Статус** | in_progress |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-09 (PR #12), `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft (будет заполнен после открытия) |
| **Риск** | medium: владение `cmd_registry_export` |

`cmd_registry_export` переносится в `modules.antares.handlers` как прямая операция (не job-dispatch). Совместимый re-export и mixed `get_handlers()` сохраняются. Isolated entry, `JOB_ACCEPT` и cutover **не** входят. GPT review ещё не выполнялся.

---

## Goal

`/registry_export` живёт в `modules.antares.handlers` через существующие `bind_rules` / `bind_logger`, с прежним ACL `command="registry_export"` и прежней доставкой Excel в Telegram.

---

## Граница изменения

Переносится только `cmd_registry_export`. Не меняются builder, PostgreSQL, формат XLSX, registry/outbox, правила хранения/удаления файлов, `expected_tg_commands.json`. Job-dispatch не вводится.

Совместимый путь: `integrations.tg_commands.cmd_registry_export` — тот же function object.

---

## Success Criteria

- [ ] Unbound / частичный bind — ошибка конфигурации до builder, стартового ответа и отправки
- [ ] ACL `registry_export` до построения и отправки
- [ ] Executor + caption[:1024] + extra reply при длине > 1024; файл закрывается
- [ ] Identity re-export + девять различимых callbacks
- [ ] Mixed expected JSON без изменений
- [ ] GPT review **не** ставился как пройденный
- [ ] merge/deploy PR #4–#12 не выполнены

---

## Ограничения покрытия

Isolated Antares **не** готов. В mixed `tg_commands` остаются: `start`/`help`/`status`, Raccoon cmds, `run_script_hello`, Auto-Enable, document ingest. Registry/outbox/БД и JOB_REGISTRY не переносились.

---

## Out Of Scope

isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#12; Railway; профили; Auto-Enable; document ingest.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-18 | выделение `cmd_registry_export` в `modules.antares.handlers` |
