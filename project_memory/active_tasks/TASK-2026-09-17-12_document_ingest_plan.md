# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-12 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-11 (PR #14), `ops/MODULAR_REORG_DOCUMENT_INGEST.md`, `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft [#15](https://github.com/deniskotdavydov1991-wq/Test/pull/15) `feat/task-2026-09-17-12-document-ingest-plan`, base `feat/task-2026-09-17-11-auto-enable-cmds` |
| **HEAD (проверен GPT)** | `428d50fe93c0b9c75d1fc05baf2eaf5940f03462` |
| **Риск** | low: только документы |

План выделения Wallet Editor document ingest **принят**. Runtime и тесты в TASK-12 не менялись. Review пройден. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы. PR #15 остаётся Draft. Реализация — TASK-13 (отдельная ветка).

---

## Goal

Точный план переноса реализации ingest в `modules.antares.document_ingest` с сохранением поведения, chat allowlist, identity callback и mixed `get_handlers()`.

---

## Current Behavior (исходники)

Callback в `integrations/wallet_editor_tg.py`. Mixed `tg_commands` только импортирует и регистрирует `MessageHandler`. Allowlist и operator map — не command ACL.

Обследованный SHA: `8e57e49f2b04c919680918c3d704136032015bdc`. Принятый план: `428d50fe…`.

---

## Desired Behavior

Документ решения принят: единственный владелец состояния, lazy patch-пути, startup при импорте owner, identity вне harness.

---

## Success Criteria

- [x] Карта вызовов, patch-путей, scheduler/harness
- [x] Контракт последовательности ingest и поля трёх очередей
- [x] Владелец `modules.antares.document_ingest`; флаг и рабочий `TMP_DIR` только на owner
- [x] Lazy-import: патч `automation.worker.*` / routing-модуля, не `document_ingest.add_task`
- [x] Startup только при импорте owner; identity — отдельный тест, не harness dump
- [x] GPT review HEAD `428d50fe…`: блокирующих нет
- [ ] merge/deploy PR #15 (намеренно открыто)
- [ ] code PR ingest / TASK-13 (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы; pytest **не** требовался |
| GPT | Проверил план HEAD `428d50fe…`. Тесты **не** запускал. |

---

## Out Of Scope

runtime/тесты TASK-12; isolated entry; `JOB_ACCEPT`; cutover; Railway; профили; merge #4–#15; живые кабинеты/Telegram/БД/worker.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-18 | план document ingest подготовлен к review |
| 2026-09-18 | уточнены владелец состояния, lazy patch-пути, startup и identity-тесты |
| 2026-09-18 | GPT review HEAD `428d50fe…`: план принят; merge/deploy нет |
