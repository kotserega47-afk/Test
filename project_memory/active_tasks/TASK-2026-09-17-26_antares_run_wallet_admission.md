# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-26 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-25 (PR #28, review `eda7144…`, закрытие `a33df9c653422da5948e75178131de6fcd870a5b`), `modules/antares/work_admission.py` |
| **PR** | Draft [#29](https://github.com/deniskotdavydov1991-wq/Test/pull/29) `feat/task-2026-09-17-26-antares-run-wallet-admission`, base `feat/task-2026-09-17-25-antares-work-admission` |
| **Риск** | medium: isolated `/run_wallet` + helper bind/open/seal; mixed unbound без изменений |

Review **пройден** на HEAD `960bf69516434cb7882de96263a78dfdf1ac8e1f`. Этот docs-коммит — закрытие TASK-26. Защищён **только** `/run_wallet`. Перенос **не** выпущен. PR Draft. Merge/deploy нет.

Cursor: admission+handlers+dispatch **62 passed**, lifecycle+boot **52 passed**, exit 0. GPT: review кода и тестов; отдельно минимальное воспроизведение `AdmittedJob`, Python **3.12.14**, PASS. GPT полные pytest-наборы **не** запускал.

---

## Goal

Примитив допуска + один путь `/run_wallet` по контракту TASK-25.

---

## Success Criteria

- [x] Состояния unbound / bound_closed / open / sealed; seal идемпотентен; open sealed запрещён
- [x] `submit_job_if_open`: проверка open + `executor.submit` под коротким lock
- [x] Isolated `/run_wallet`: ACL → submit → наблюдение Future до первого await → «Запускаю» → wait; closed/sealed без submit и без «Запускаю»
- [x] Mixed/unbound: прежний `run_job_async` и порядок
- [x] Helper: bind до initialize, open после start, seal до cleanup await; shield TASK-24 сохранён
- [x] `/run_hourly` и прочие dispatch — обход (TASK-27)
- [x] GPT review кода/тестов + минимальный `AdmittedJob` на 3.12.14 PASS; полные pytest GPT не гонял
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | реализация: admission+handlers+job_dispatch **55 passed**; lifecycle+boot **48 passed**; 3.12.10, PTB 22.8, httpx 0.28.1 |
| Cursor | review-fix от HEAD `137fa6396b80d62702bae26b81f837bad86cfbe3`: `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py` **61 passed**; `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**; 3.12.10, PTB 22.8, httpx 0.28.1. SHA этого прогона: `6b9fa4d26e726f20e8c9ec30898ebc2ce56820e4` |
| Cursor | unhandled asyncio.Future: база `6b9fa4d26e726f20e8c9ec30898ebc2ce56820e4`; принятый review HEAD `960bf69516434cb7882de96263a78dfdf1ac8e1f`; `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py` **62 passed** (exit 0); `py -3.12 -m pytest tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed** (exit 0) |
| GPT | review кода и тестов на `960bf69516434cb7882de96263a78dfdf1ac8e1f`; отдельно минимальное воспроизведение `AdmittedJob`, Python **3.12.14**, PASS; полные pytest-наборы не запускал |

Границы: `request_job` в unit-тестах — функция с Event; live Telegram/кабинеты/WE worker не запускались.

**Новые проверки (review-fix на `6b9fa4d…` / `960bf69…`):** cancel `/run_wallet` на первом reply + ошибка принятого job (Future не cancelled, job error ровно 1); cancel во время wait; submit перед первым reply; executor `max_workers=1` (request_job после seal); stop из loop-thread / чужого потока с loop / без loop; initialize vs start failure; subprocess Application+admission (closed / open / sealed до cleanup); restore handlers `_rules`/`_logger`; внутренний Future без `set_exception`.

**Прежняя регрессия:** admission+handlers+dispatch и lifecycle/boot без admission.

Известное падение scheduler **не чинилось**:

```
py -3.12 -m pytest tests/test_scheduler_dispatch.py::test_schedule_loop_calls_dispatch_job_background -q --tb=short
```

на закрытии TASK-25 `a33df9c653422da5948e75178131de6fcd870a5b` и на исходном HEAD TASK-26 `137fa6396b80d62702bae26b81f837bad86cfbe3`: **1 failed** (два тика `wallet` вместо одного).

---

## Out Of Scope

остальные commands (TASK-27), ingest, internal Auto-Enable, schedules, JOB_ACCEPT env, serve, mixed-stop API, merge, исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | реализация WorkAdmission + isolated `/run_wallet`; Draft PR #29 |
| 2026-09-21 | review-fix: observe Accepted Future сразу; stop без fallback `Event.set`; усиление тестов |
| 2026-09-21 | unhandled asyncio.Future: результат только через `_take()`; notify без `set_exception` |
| 2026-09-21 | review пройден на `960bf69516434cb7882de96263a78dfdf1ac8e1f`; закрытие docs; защищён только `/run_wallet`; не выпущено |
