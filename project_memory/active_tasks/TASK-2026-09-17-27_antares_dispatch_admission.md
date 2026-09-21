# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-27 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-26 (PR #29, review `960bf69516434cb7882de96263a78dfdf1ac8e1f`, закрытие `f3ed9e24c23322bc2a53c8f92efb2321aa6cdbba`) |
| **PR** | Draft [#30](https://github.com/deniskotdavydov1991-wq/Test/pull/30) `feat/task-2026-09-17-27-antares-dispatch-admission`, base `feat/task-2026-09-17-26-antares-run-wallet-admission` |
| **Риск** | medium: isolated шесть dispatch-команд через общий admission; mixed unbound без изменений |

Один общий isolated-путь для `/run_wallet`, `/run_hourly`, `/run_download`, `/run_rate`, `/operator_wallets_ready`, `/wallet_editor_refresh`. Registry/Auto-Enable/reload, ingest, schedules, internal enqueue **не** подключены. Serve/polling **нет**. `JOB_ACCEPT` **нет**. PR Draft. Не выпущено.

---

## Goal

Подключить тот же `WorkAdmission` ко всем шести Antares dispatch-командам без копирования тел.

---

## Success Criteria

- [x] Общая реализация `_run_antares_command` для шести команд
- [x] Isolated: ACL → атомарный submit → наблюдение Future до первого await → ответы
- [x] Mixed/unbound: прежний `run_job_async` и порядок
- [x] closed/sealed без submit и без «Запускаю»
- [x] Accepted Future не отменяется с callback; ошибка job один раз
- [x] Диагностика с фактическим `job_type`
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py` **90 passed**, exit 0; `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**, exit 0; 3.12.10 |
| GPT | ещё не ревьюил |

---

## Out Of Scope

registry/Auto-Enable/reload, ingest, schedules, internal enqueue, mixed gate, serve, JOB_ACCEPT, merge, исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | шесть dispatch-команд на общем admission; Draft PR; статус **review** |
