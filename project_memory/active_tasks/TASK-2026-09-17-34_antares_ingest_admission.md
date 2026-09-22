# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-34 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-33 close `cecb336da8f01877329d6ad4f0af72d86c42eb20` (review `4682e399…`); [MODULAR_REORG_ANTARES_INGEST_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_INGEST_ADMISSION.md) |
| **PR** | Draft на `feat/task-2026-09-17-34-antares-ingest-admission-impl`, base `feat/task-2026-09-17-33-antares-ingest-admission` |
| **Риск** | medium: isolated ingest на общей WE Queue; mixed `add_*_task` не менять |

Реализация контракта TASK-33. GPT review этого code ещё не принимался. TASK-33/30/32 повторно не закрывать. Conversion / Auto-Enable enqueue / schedules / drain / mixed-stop — обходы.

---

## Goal

Isolated Telegram `.xlsx` ingest: early reject + `put_nowait` под admission lock, владение файлом по контракту, mixed без изменений.

---

## Success Criteria

- [x] `WorkAdmission.put_nowait_if_open`: OPEN + реальный `put_nowait` под одним коротким lock; `qsize` вне lock
- [x] `ensure_profile_queue` вне admission lock; Thread.start не бизнес-loop в тестах
- [x] Isolated три маршрута; mixed `add_*_task`
- [x] I1–I18 и M1
- [ ] GPT review code
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `tests/test_antares_ingest_admission.py` **20 passed**, exit 0, Python 3.12.10 |
| Cursor | ingest/compat/registration/inventory: `tests/test_antares_document_ingest.py tests/unit/test_wallet_editor_tg_integration.py tests/test_behavior_baseline_registration.py tests/test_behavior_baseline_inventory.py tests/test_antares_ingest_admission.py` **87 passed**, exit 0, 3.12.10 |
| Cursor | admission/handlers/access_guard **123 passed**, exit 0, 3.12.10 |
| Cursor | lifecycle+boot **52 passed**, exit 0, 3.12.10 |
| GPT | ещё не ревьюил |

Наборы не суммировать. Worker `test_worker_passes_user_output_file_to_registry_schedule` — исторический AttributeError `stage_registry_result_copy`, не этот diff.

---

## Out Of Scope

conversion bridge; `enqueue_auto_enable_batch`; schedules; drain/join/sender stop; mixed-stop; serve; rules_provider; live Telegram; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | isolated ingest admit; Draft PR; статус **review (подготовлено)** |
