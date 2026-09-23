# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-35 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-34 close `057614413480831795a393bcfd3ac14f69d89fe4` (review `f76f9c9…`); [MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md) |
| **PR** | Draft [#38](https://github.com/deniskotdavydov1991-wq/Test/pull/38) `feat/task-2026-09-17-35-antares-schedules-admission`, base `feat/task-2026-09-17-34-antares-ingest-admission-impl` |
| **Риск** | medium: общий job executor; mixed `schedule_loop` не менять |

Docs-контракт isolated schedules. Runtime **не** менялся. Реализация **не** начиналась. TASK-34/33 повторно не закрывать.

---

## Goal

Зафиксировать peek/commit HourlyGate, однозначные часы, API `tick` без импорта mixed `scheduler.py`, identity по `job_type` как mixed, наблюдение Accepted Future — без runtime.

---

## Success Criteria

- [x] Peek gate без мутации; commit gate+`next_*` только после Accepted
- [x] closed / submit error не потребляют due/gate; Future error не откатывает; нет intra-tick retry
- [x] Gate-skip **сдвигает** `next_*`; sealed = явный no-op submit
- [x] Owner `modules.antares.scheduler`; копия cron/hourly формул; boot/run не стартуют tick
- [x] Observation callback сразу после Accepted; seal не cancel; диагностика ≠ повтор слота
- [x] Несколько rows одного `job_type`: сохранить mixed (не ключ по `id`)
- [x] Матрица S1–S18; «два тика» — дефект ожидания теста
- [ ] GPT review контракта
- [ ] реализация (будущий code)
- [ ] merge/deploy (намеренно открыто)

---

## Выбранные решения (этот docs)

| Тема | Решение |
|------|---------|
| HourlyGate | peek на копии; commit только Accepted |
| Следующий запуск | от `now_ts`/`now_dt` входов tick |
| Gate-skip | сдвигает `next_*`, не last_* |
| Rejected / submit error | не сдвигать; следующий tick может принять |
| Sealed | явный no-op на tick, не обязательный stop thread |
| Формулы cron/hourly | копия в `modules.antares.{schedule_timing,hourly_gate}`; mixed файл не импортировать |
| Identity | `job_type` + раздельные dict interval/cron, как mixed |
| Accepted | постановка `request_job`, не успех job / не busy-rollback |

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование на close TASK-34 `0576144…`; уточнение контракта от `209b96dc30df324d69c90d4619deae6aee2e7af8` |
| Cursor | «два тика»: **1 failed** на указанном fake clock; mixed не менялся |
| GPT | ещё не ревьюил этот уточнённый контракт |

Runtime этого PR не менялся. Pytest GPT не требовался.

---

## Out Of Scope

runtime; mixed `schedule_loop`; internal Auto-Enable; conversion `add_task`; `tg_receiver`; drain/join/sender stop; serve; mixed-stop; live polling; merge/retarget/deploy; исходное Test; extract формул в `core/` (отдельное решение).

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | docs-контракт isolated schedules; статус **review (подготовлено)** |
| 2026-09-23 | уточнение: peek/commit gate; однозначные часы; API tick; identity job_type; матрица S1–S18 |
