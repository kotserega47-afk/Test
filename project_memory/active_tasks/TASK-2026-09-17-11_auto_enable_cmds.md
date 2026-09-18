# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-11 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-10 (PR #13), `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft [#14](https://github.com/deniskotdavydov1991-wq/Test/pull/14) `feat/task-2026-09-17-11-auto-enable-cmds`, base `feat/task-2026-09-17-10-registry-export` |
| **HEAD (проверен GPT)** | `0fa283eb54348c3f88f23a3970d602df6e60bd74` |
| **Риск** | medium: владение Auto-Enable Telegram callbacks |

`cmd_auto_enable_plan` и `cmd_auto_enable_run` **перенесены**. В `modules.antares.handlers` **11** callbacks. Auto-Enable вызывает прежний orchestrator через `loop.run_in_executor` (не job-dispatch). ACL, `Actor(kind="tg", ...)`, `manual=True`, ответы и обработка ошибок сохранены. Совместимый re-export и mixed `get_handlers()` сохранены. Review пройден. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы. PR #14 остаётся Draft.

---

## Goal

Оба Auto-Enable callback живут в `modules.antares.handlers` через существующие `bind_rules` / `bind_logger`, с прежними ACL `auto_enable_plan` / `auto_enable_run`, `Actor(kind="tg", ...)`, `manual=True` и прежними текстами ответов.

---

## Граница изменения

Перенесено: только два callback. Orchestrator, executor Wallet Editor, eligibility, настройки, registry/outbox и бизнес-логика Auto-Enable не менялись. `expected_tg_commands.json` не менялся. Job-dispatch не вводился.

Совместимые пути: `integrations.tg_commands.cmd_auto_enable_plan` и `cmd_auto_enable_run` — те же function objects. Команды по-прежнему регистрируются в mixed `get_handlers()`.

---

## Success Criteria

- [x] Unbound / частичный bind — ошибка конфигурации до стартового ответа и orchestrator
- [x] ACL до Actor, стартового ответа и executor
- [x] plan вызывает только `run_auto_enable_plan`; run — только `run_auto_enable`
- [x] Identity re-export + 11 различимых callbacks
- [x] Mixed expected JSON без изменений
- [x] GPT review HEAD `0fa283eb…`: блокирующих нет
- [ ] merge/deploy PR #14 (намеренно открыто)

---

## Ограничения покрытия

Isolated Antares **не** готов. В mixed `tg_commands` остаются: `start`/`help`/`status`, Raccoon cmds, `run_script_hello`, document ingest. Регистрация Auto-Enable в mixed `get_handlers()` сохраняется.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **86 passed** на HEAD `0fa283eb…`, Python **3.12.10** (`test_antares_handlers`, `test_wallet_editor_auto_enable_orchestrator`, inventory, registration, profile boot) |
| GPT | Проверил код и diff HEAD `0fa283eb…`. Набор 86 **независимо не запускал.** |

---

## Out Of Scope

isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#14; Railway; профили; document ingest; правка текстов dry_run/approval_required.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-18 | выделение `cmd_auto_enable_plan` и `cmd_auto_enable_run` в `modules.antares.handlers` |
| 2026-09-18 | GPT review HEAD `0fa283eb…`: блокирующих нет; merge/deploy нет |
