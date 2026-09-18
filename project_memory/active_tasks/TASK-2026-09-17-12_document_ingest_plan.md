# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-12 |
| **Статус** | in_progress (подготовлено к review; не «review пройден») |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-11 (PR #14), `ops/MODULAR_REORG_DOCUMENT_INGEST.md`, `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft [#15](https://github.com/deniskotdavydov1991-wq/Test/pull/15) `feat/task-2026-09-17-12-document-ingest-plan`, base `feat/task-2026-09-17-11-auto-enable-cmds` |
| **HEAD** | `1bdcd9d1dd6e87847be4d4399e0a47f314a00ea6` |
| **Риск** | low: только документы |

План выделения Wallet Editor document ingest **подготовлен**. Runtime и тесты в этой задаче не менялись. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы. GPT review ещё не выполнялся.

---

## Goal

Точный план переноса реализации ingest в `modules.antares.document_ingest` с сохранением поведения, chat allowlist, identity callback и mixed `get_handlers()`.

---

## Current Behavior (исходники)

Callback уже в `integrations/wallet_editor_tg.py`. Mixed `tg_commands` только импортирует и регистрирует `MessageHandler(filters.Document.ALL, ...)`. Allowlist и operator map — не command ACL. Startup warning — import-time этого модуля (через импорт `tg_commands` в scheduler). Постановка в in-memory очереди worker ≠ durable inbox.

Обследованный SHA: `8e57e49f2b04c919680918c3d704136032015bdc`.

---

## Desired Behavior

Документ решения: карта зависимостей, контракт, владелец реализации, re-export без второго состояния, карта тестов и объём следующего code PR.

---

## Success Criteria

- [x] Карта вызовов, patch-путей, scheduler/harness
- [x] Контракт последовательности ingest и поля трёх очередей
- [x] Владелец `modules.antares.document_ingest`; compat re-export; не bind_rules
- [x] Пробелы тестов названы; регистрация не считается proof worker
- [ ] GPT review **не** ставился как пройденный
- [ ] merge/deploy PR #4–#14 не выполнены
- [ ] code PR ingest (отдельное задание)

---

## Out Of Scope

runtime/тесты этого PR; isolated entry; `JOB_ACCEPT`; cutover; Railway; профили; merge #4–#14; живые кабинеты/Telegram/БД/worker.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-18 | план document ingest подготовлен к review |
