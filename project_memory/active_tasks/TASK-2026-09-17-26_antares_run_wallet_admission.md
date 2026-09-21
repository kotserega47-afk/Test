# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-26 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-25 (PR #28, review `eda7144…`, закрытие `a33df9c653422da5948e75178131de6fcd870a5b`), `modules/antares/work_admission.py` |
| **PR** | Draft [#29](https://github.com/deniskotdavydov1991-wq/Test/pull/29) `feat/task-2026-09-17-26-antares-run-wallet-admission`, base `feat/task-2026-09-17-25-antares-work-admission` |
| **Риск** | medium: isolated `/run_wallet` + helper bind/open/seal; mixed unbound без изменений |

Реализованы `WorkAdmission` и isolated `/run_wallet`. Остальные команды, ingest, scheduler, mixed gate, sender/worker stop **не** менялись. Serve/polling **нет**. `JOB_ACCEPT` env **нет**. PR Draft. Не выпущено.

---

## Goal

Примитив допуска + один путь `/run_wallet` по контракту TASK-25.

---

## Success Criteria

- [x] Состояния unbound / bound_closed / open / sealed; seal идемпотентен; open sealed запрещён
- [x] `submit_job_if_open`: проверка open + `executor.submit` под коротким lock
- [x] Isolated `/run_wallet`: ACL → submit → «Запускаю» → Future; closed/sealed без submit и без «Запускаю»
- [x] Mixed/unbound: прежний `run_job_async` и порядок
- [x] Helper: bind до initialize, open после start, seal до cleanup await; shield TASK-24 сохранён
- [x] `/run_hourly` обход
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | реализация: admission+handlers+job_dispatch **55 passed**; lifecycle+boot **48 passed**; 3.12.10, PTB 22.8, httpx 0.28.1 |
| Cursor | review-fix от HEAD `137fa6396b80d62702bae26b81f837bad86cfbe3`: `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py` **61 passed**; `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**; 3.12.10, PTB 22.8, httpx 0.28.1 |
| GPT | ещё не ревьюил |

Границы: `request_job` в unit-тестах — функция с Event; live Telegram/кабинеты/WE worker не запускались.

**Новые проверки:** cancel `/run_wallet` на первом reply + ошибка принятого job (Future не cancelled, job error ровно 1); cancel во время wait; submit перед первым reply; executor `max_workers=1` (request_job после seal); stop из loop-thread / чужого потока с loop / без loop; initialize vs start failure; subprocess Application+admission (closed / open / sealed до cleanup); restore handlers `_rules`/`_logger`.

**Прежняя регрессия:** admission+handlers+dispatch и lifecycle/boot без admission.

Известное падение scheduler **не чинилось**:

```
py -3.12 -m pytest tests/test_scheduler_dispatch.py::test_schedule_loop_calls_dispatch_job_background -q --tb=short
```

на закрытии TASK-25 `a33df9c653422da5948e75178131de6fcd870a5b` и на исходном HEAD TASK-26 `137fa6396b80d62702bae26b81f837bad86cfbe3`: **1 failed** (два тика `wallet` вместо одного).

---

## Out Of Scope

остальные commands, ingest, internal Auto-Enable, schedules, JOB_ACCEPT env, serve, mixed-stop API, merge, исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | реализация WorkAdmission + isolated `/run_wallet`; Draft PR #29 |
| 2026-09-21 | review-fix: observe Accepted Future сразу; stop без fallback `Event.set`; усиление тестов; статус **review** |
