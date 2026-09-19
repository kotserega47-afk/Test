# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-13 |
| **Статус** | review (не пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-12 (PR #15, закрытие `66b99ab1d2e619cc24dbb0122c5d1f7820d82f96`), `ops/MODULAR_REORG_DOCUMENT_INGEST.md` |
| **PR** | Draft [#16](https://github.com/deniskotdavydov1991-wq/Test/pull/16) `feat/task-2026-09-17-13-document-ingest`, base `feat/task-2026-09-17-12-document-ingest-plan` |
| **HEAD** | `612b8f9b96f77c1ba23f6442c0818f4aabac2b1d` |
| **Риск** | medium: владение Telegram document ingest |

Тело `handle_wallet_editor_document` перенесено в `modules.antares.document_ingest`. `integrations.wallet_editor_tg` — identity re-export тех же function objects. Mixed `get_handlers()` по-прежнему регистрирует `MessageHandler(filters.Document.ALL, …)` через compat. Chat allowlist, operator map и `automation.audit.log` сохранены. `bind_rules` / command ACL не добавлялись. Review **не** пройден. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы.

---

## Goal

Владелец ingest — `modules.antares.document_ingest`; mixed путь и поведение enqueue без изменений worker/engine/Excel contracts.

---

## Граница изменения

Перенесено: callback, helpers, `TMP_DIR`, `_ALLOWLIST_STARTUP_LOGGED`, единственный import-time startup warning.

Не менялись: worker, engine, Excel contracts, очереди, scheduler, credentials, профили, `expected_tg_commands.json`.

Совместимый путь: `from integrations.wallet_editor_tg import handle_wallet_editor_document` — тот же function object.

---

## Success Criteria

- [x] Owner callback + helpers; compat только re-export
- [x] Три очереди, AMBIGUOUS unlink, except без нового удаления
- [x] Lazy worker/routing после доступа
- [x] Тесты §4.2, identity вне harness, startup в subprocess
- [ ] GPT review (ещё не пройден)
- [ ] merge/deploy (намеренно открыто)

---

## Ограничения покрытия

Isolated Antares **не** готов. Durable inbox нет. Harness dump со stub `wallet_editor_tg` не считается identity. Живые кабинеты / Telegram / очередь worker не гонялись.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | pytest ingest/compat/inventory/boot (см. отчёт PR) |
| GPT | ещё не проверял |

---

## Out Of Scope

isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#16; Railway; профили; bind_rules / command ACL для ingest.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-18 | перенос ingest в `modules.antares.document_ingest`; Draft code PR |
| 2026-09-19 | уточнены startup-счётчик, monkeypatch и отделение Draft PR #16 от production |
