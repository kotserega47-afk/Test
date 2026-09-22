# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-35 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-34 close `057614413480831795a393bcfd3ac14f69d89fe4` (review `f76f9c9…`); [MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md) |
| **PR** | Draft на `feat/task-2026-09-17-35-antares-schedules-admission`, base `feat/task-2026-09-17-34-antares-ingest-admission-impl` |
| **Риск** | medium: общий job executor; mixed `schedule_loop` не менять |

Docs-контракт isolated schedules. Runtime **не** менялся. Реализация **не** начиналась. GPT pytest **не** требовался. TASK-34/33 повторно не закрывать.

---

## Goal

Зафиксировать фильтр семи Antares keys, атомарный admit на `submit_job_if_open`, часы vs Accepted, stop источника новых schedule-задач и матрицу S1–S8 — без runtime и без правки mixed scheduler.

---

## Success Criteria

- [x] Цепочка `load_schedules` → due → `dispatch_job_background` → `submit` → `request_job` описана по исходникам
- [x] Фильтр семи keys до dispatch; admit только `submit_if_open` / `submit_job_if_open`
- [x] closed/sealed и ошибка submit ≠ успешный слот; Accepted Future после seal живёт
- [x] Пропуск/reload clocks / stop источника без durable cursor и exactly-once
- [x] Падение «два тика» воспроизведено в sandbox; mixed не чинился
- [x] Матрица fake clock + Event/barrier
- [ ] GPT review контракта
- [ ] реализация (будущий code)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование `scheduler.py` / `core/schedules.py` / `job_dispatch.py` на close TASK-34 `0576144…` |
| Cursor | `py -3.12 -m pytest tests/test_scheduler_dispatch.py::test_schedule_loop_calls_dispatch_job_background` **1 failed** (два `wallet`), 3.12.10, exit 1; mixed не менялся |
| GPT | ещё не ревьюил |

---

## Out Of Scope

runtime этого PR; mixed `schedule_loop`; internal Auto-Enable enqueue; conversion `add_task`; `tg_receiver`; drain/join/executor shutdown/sender stop; serve; mixed-stop; live polling; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | docs-контракт isolated schedules; статус **review (подготовлено)** |
