# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-35 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-34 close `057614413480831795a393bcfd3ac14f69d89fe4`; [MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md); реализация — TASK-36 |
| **PR** | Draft [#38](https://github.com/deniskotdavydov1991-wq/Test/pull/38) `feat/task-2026-09-17-35-antares-schedules-admission`, base `feat/task-2026-09-17-34-antares-ingest-admission-impl` |
| **Риск** | medium: общий job executor; mixed `schedule_loop` не менять |

Review **пройден**. GPT проверил контракт на полном SHA `ce5326a6882a24ed3bbb23e828df99fe7f8d15fb`. GPT pytest **не** запускал. Runtime **не** менялся. Этот docs-коммит — закрытие TASK-35. Реализация **ещё не** выполнена (TASK-36). TASK-34 повторно не закрывать. PR #38 остаётся Draft. Merge/deploy нет.

Историческое падение mixed-теста «два тика» сохранено: дефект ожидания теста, mixed runtime не менять.

---

## Goal

Зафиксировать peek/commit HourlyGate, однозначные часы, API `tick` без импорта mixed `scheduler.py`, identity по `job_type`, один observer Accepted Future — без runtime.

---

## Success Criteria

- [x] Peek gate без мутации; commit только подготовленного состояния после Accepted
- [x] Tick-local `attempted` по `(job_type, schedule_type)`; отказ ≠ вторая попытка в том же tick
- [x] Подготовка deadline/gate до submit; битый cron на due без ложного Accepted
- [x] Один `add_done_callback`; без второго `watch_admitted_future`
- [x] Последовательный `tick` на одном state; параллельный tick не поддерживается
- [x] Матрица S1–S18 + S7b/c/d, S12b; «два тика» — дефект ожидания теста
- [x] GPT review контракта на `ce5326a…`; pytest GPT не запускал
- [ ] реализация (TASK-36)
- [ ] merge/deploy (намеренно открыто)

---

## Выбранные решения (этот docs)

| Тема | Решение |
|------|---------|
| HourlyGate | peek на копии; commit только Accepted |
| Следующий запуск | заранее подготовленный next от `now_ts`/`now_dt`; не `_next_cron_run` после Accepted |
| Gate-skip | записывает подготовленный `next_*`, не last_* |
| Rejected / submit error | не сдвигать; `attempted` в tick; следующий tick может принять |
| Дубли interval rows | одна попытка на clock за tick (isolated) |
| Sealed | явный no-op на tick |
| Observer | один `add_done_callback` на sync tick |
| Tick concurrency | только последовательный вызов на state |
| Identity clocks | `job_type` + раздельные interval/cron dict |
| Accepted | постановка `request_job`, не успех job |

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование и контракт; runtime не менялся |
| Cursor | «два тика»: **1 failed** на указанном fake clock; mixed не менялся |
| GPT | контракт на `ce5326a6882a24ed3bbb23e828df99fe7f8d15fb`; pytest **не** запускал |

---

## Out Of Scope

runtime этого PR; реализация tick (TASK-36); mixed `schedule_loop`; internal Auto-Enable; conversion `add_task`; `tg_receiver`; drain/join/sender stop; serve; mixed-stop; live polling; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | docs-контракт isolated schedules; статус **review (подготовлено)** |
| 2026-09-23 | уточнение: peek/commit gate; attempted; prepare; один observer; sequential tick |
| 2026-09-23 | GPT review на `ce5326a…`; закрытие docs; runtime нет; реализация — TASK-36 |
