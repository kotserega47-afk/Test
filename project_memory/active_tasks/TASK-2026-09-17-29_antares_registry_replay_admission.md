# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-29 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-28 (PR #31, review `f0bd06bf157f4b377ad7648410605ee67841835f`, закрытие `a81aa69a416b3ad03708bd5515ec135c12f0e896`) |
| **PR** | Draft [#32](https://github.com/deniskotdavydov1991-wq/Test/pull/32) `feat/task-2026-09-17-29-antares-registry-replay-admission`, base `feat/task-2026-09-17-28-antares-direct-ops-admission` |
| **Риск** | medium: isolated `/registry_replay` уходит в общий job executor; mixed остаётся sync в callback |

Isolated `/registry_replay` через `submit_if_open(get_job_executor(), replay_pending_outbox_records)`. Не job `wallet_editor_registry_replay` и не `request_job`. Mixed/unbound: прежний синхронный вызов в event loop. Внутренние действия replay и drain **не** закрыты. PR Draft. Не выпущено.

---

## Goal

Допустить внешний Telegram-вход `/registry_replay` тем же generic admission, что TASK-28.

---

## Success Criteria

- [x] Isolated: ACL → submit callable без аргументов → наблюдение Future до первого await → стартовый ответ → прежний итог
- [x] Closed/sealed и ACL deny без submit/replay/«Replaying»
- [x] Mixed/unbound: sync `replay_pending_outbox_records()` после стартового ответа, без executor
- [x] Формат attempted/synced/failed/skipped; errors до 10; пустой список без блока
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py tests/unit/test_tg_registry_export.py` **129 passed**, exit 0; `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**, exit 0; 3.12.10 |
| GPT | ещё не ревьюил |

---

## Out Of Scope

reload_rules, ingest, schedules, internal enqueue, drain, внутренние шаги replay/БД/outbox, serve, merge, исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | isolated `/registry_replay` admission; Draft PR; статус **review** |
