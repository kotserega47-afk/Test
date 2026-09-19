# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-13 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-12 (PR #15, закрытие `66b99ab1d2e619cc24dbb0122c5d1f7820d82f96`), `ops/MODULAR_REORG_DOCUMENT_INGEST.md` |
| **PR** | Draft [#16](https://github.com/deniskotdavydov1991-wq/Test/pull/16) `feat/task-2026-09-17-13-document-ingest`, base `feat/task-2026-09-17-12-document-ingest-plan` |
| **HEAD (проверен GPT)** | `4a1e7796b13eac0b55b7f1b62054adf0e82b1fe6` |
| **Риск** | medium: владение Telegram document ingest |

Тело `handle_wallet_editor_document` **выделено** в `modules.antares.document_ingest`. `integrations.wallet_editor_tg` — identity re-export тех же function objects. Mixed `get_handlers()` по-прежнему регистрирует `MessageHandler(filters.Document.ALL, …)` через compat. Chat allowlist, operator map и `automation.audit.log` сохранены. `bind_rules` / command ACL не добавлялись. Review пройден. Перенос **не выпущен**. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы. PR #16 остаётся Draft.

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
- [x] GPT review HEAD `4a1e7796…`: блокирующих нет
- [ ] merge/deploy PR #16 (намеренно открыто)

---

## Ограничения покрытия

Isolated Antares **не** готов. Durable inbox нет. Harness dump со stub `wallet_editor_tg` не считается identity. Живые кабинеты / Telegram / очередь worker не гонялись. Production этим PR не переключался.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **141 passed** на HEAD `16d126d…` (ingest/compat/inventory/registration/boot/handlers/jobs) |
| Cursor | **60 passed** на HEAD `4a1e7796…`, Python **3.12.10** (`test_antares_document_ingest`, `test_wallet_editor_tg_integration`) |
| GPT | Проверил код и diff HEAD `4a1e7796…`. Наборы **141** и **60** самостоятельно **не** запускал |

---

## Out Of Scope

isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#16; Railway; профили; bind_rules / command ACL для ingest.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-18 | перенос ingest в `modules.antares.document_ingest`; Draft code PR |
| 2026-09-19 | уточнены startup-счётчик, monkeypatch и отделение Draft PR #16 от production |
| 2026-09-19 | GPT review HEAD `4a1e7796…`: блокирующих нет; merge/deploy нет |
