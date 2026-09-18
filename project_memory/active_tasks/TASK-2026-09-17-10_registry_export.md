# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-10 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-09 (PR #12), `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft [#13](https://github.com/deniskotdavydov1991-wq/Test/pull/13) `feat/task-2026-09-17-10-registry-export`, base `feat/task-2026-09-17-09-registry-cmds` |
| **HEAD (проверен GPT)** | `8c37766689accbdbf5a8aff677dd096e4aaca06c` |
| **Риск** | medium: владение `cmd_registry_export` |

`cmd_registry_export` **перенесён**. В `modules.antares.handlers` **девять** callbacks. Это прямая операция через `loop.run_in_executor` (не job-dispatch). Совместимый re-export и mixed-сборка сохранены. Review пройден. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы. PR #13 остаётся Draft.

---

## Goal

`/registry_export` живёт в `modules.antares.handlers` через существующие `bind_rules` / `bind_logger`, с прежним ACL `command="registry_export"` и прежней доставкой Excel в Telegram.

---

## Граница изменения

Перенесено: только `cmd_registry_export`. Builder, PostgreSQL, формат XLSX, registry/outbox, правила хранения/удаления файлов и `expected_tg_commands.json` не менялись. Job-dispatch не вводился.

Совместимый путь: `integrations.tg_commands.cmd_registry_export` — тот же function object. Команда по-прежнему регистрируется в mixed `get_handlers()`.

---

## Success Criteria

- [x] Unbound / частичный bind — ошибка конфигурации до builder, стартового ответа и отправки
- [x] ACL `registry_export` до построения и отправки
- [x] Executor + caption[:1024] + extra reply при длине > 1024; файл закрывается
- [x] Identity re-export + девять различимых callbacks
- [x] Mixed expected JSON без изменений
- [x] GPT review HEAD `8c37766…`: блокирующих нет
- [ ] merge/deploy PR #13 (намеренно открыто)

---

## Ограничения покрытия

Isolated Antares **не** готов. В mixed `tg_commands` остаются: `start`/`help`/`status`, Raccoon cmds, `run_script_hello`, Auto-Enable, document ingest. Регистрация `registry_export` в mixed `get_handlers()` сохраняется. Registry/outbox/БД и JOB_REGISTRY не переносились.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **97 passed** на HEAD `8c37766…`, Python **3.12.10** (`test_antares_handlers`, `test_tg_registry_export`, inventory, registration, profile boot, registry export builder/format) |
| GPT | Проверил код и diff HEAD `8c37766…`. Набор 97 **независимо не запускал.** |

---

## Out Of Scope

isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#13; Railway; профили; Auto-Enable; document ingest.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-18 | выделение `cmd_registry_export` в `modules.antares.handlers` |
| 2026-09-18 | GPT review HEAD `8c37766…`: блокирующих нет; merge/deploy нет |
