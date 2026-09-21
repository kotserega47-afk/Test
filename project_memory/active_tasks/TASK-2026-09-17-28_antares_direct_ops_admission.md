# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-28 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-27 (PR #30, review `a84e9cd48095232d901d7b12ee499d3db61407d0`, закрытие `08132f26310d1a99c3f7f6f2f5ea32c76f1edef4`) |
| **PR** | Draft [#31](https://github.com/deniskotdavydov1991-wq/Test/pull/31) `feat/task-2026-09-17-28-antares-direct-ops-admission`, base `feat/task-2026-09-17-27-antares-dispatch-admission` |
| **Риск** | medium: isolated прямые executor-операции делят `get_job_executor()` с dispatch; mixed unbound без изменений |

Isolated `/registry_export`, `/auto_enable_plan`, `/auto_enable_run` через generic `submit_if_open` (явный executor + callable), не jobs и не `request_job`. Mixed/unbound: прежний `run_in_executor(None, ...)`. Внутренний `enqueue_auto_enable_batch` остаётся обходом. Serve/polling **нет**. `JOB_ACCEPT` **нет**. PR Draft. Не выпущено.

---

## Goal

Допустить три прямые операции Antares тем же атомарным submit, что dispatch, без фиктивных `job_type`.

---

## Success Criteria

- [x] Isolated: bind → ACL → прежние предварительные проверки → `executor.submit` под lock → наблюдение Future до первого await → стартовый ответ → результат
- [x] Closed/sealed: нет submit, бизнес-вызова и сообщения «начинаю»
- [x] Isolated использует существующий `get_job_executor()`; mixed — `run_in_executor(None, ...)`
- [x] Identity re-export, порядок регистрации, export caption/файл, ветки Auto-Enable сохранены
- [x] Шесть dispatch-команд без изменения пути
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py tests/unit/test_tg_registry_export.py` **120 passed**, exit 0; `tests/unit/test_wallet_editor_auto_enable_orchestrator.py -k cmd_auto_enable` **15 passed**, 6 deselected; `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**, exit 0; 3.12.10 |
| GPT | ещё не ревьюил |

---

## Out Of Scope

registry replay, reload, ingest, schedules, internal Auto-Enable enqueue, drain/stop, mixed gate, serve, merge, исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | isolated direct ops admission; Draft PR; статус **review** |
