# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-29 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-28 (PR #31, review `f0bd06bf157f4b377ad7648410605ee67841835f`, закрытие `a81aa69a416b3ad03708bd5515ec135c12f0e896`) |
| **PR** | Draft [#32](https://github.com/deniskotdavydov1991-wq/Test/pull/32) `feat/task-2026-09-17-29-antares-registry-replay-admission`, base `feat/task-2026-09-17-28-antares-direct-ops-admission` |
| **Риск** | medium: isolated `/registry_replay` уходит в общий job executor; mixed остаётся sync в callback |

Review **пройден** на HEAD `71fce3b9eb4f621164799dd8ed379140fd3ebf5a`. Этот docs-коммит — закрытие TASK-29. Isolated `/registry_replay` через общий executor. Mixed/unbound — прежний sync в callback. Перенос **не** выпущен.

Внутренние шаги уже принятого replay — продолжение операции, не отдельный допуск. Самостоятельные новые постановки — отдельные входы. Drain — ожидание завершения принятого, не «обход допуска».

Cursor: admission/handlers/dispatch/inventory/registration/export **129 passed**, lifecycle/boot **52 passed**, Python 3.12.10, exit 0. GPT: код/diff; pytest **не** запускал.

---

## Goal

Допустить внешний Telegram-вход `/registry_replay` тем же generic admission, что TASK-28.

---

## Success Criteria

- [x] Isolated: ACL → submit callable без аргументов → наблюдение Future до первого await → стартовый ответ → прежний итог
- [x] Closed/sealed и ACL deny без submit/replay/«Replaying»
- [x] Mixed/unbound: sync `replay_pending_outbox_records()` после стартового ответа, без executor
- [x] Формат attempted/synced/failed/skipped; errors до 10; пустой список без блока
- [x] GPT проверил код/diff; pytest не запускал
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py tests/unit/test_tg_registry_export.py` **129 passed**, exit 0; `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**, exit 0; 3.12.10 |
| GPT | код/diff на `71fce3b9eb4f621164799dd8ed379140fd3ebf5a`; pytest **не** запускал |

---

## Out Of Scope

reload_rules, ingest, schedules, самостоятельные новые постановки (internal enqueue), drain как отдельный механизм stop, serve, merge, исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | isolated `/registry_replay` admission; Draft PR; статус **review** |
| 2026-09-21 | review пройден на `71fce3b9eb4f621164799dd8ed379140fd3ebf5a`; закрытие docs; не выпущено |
