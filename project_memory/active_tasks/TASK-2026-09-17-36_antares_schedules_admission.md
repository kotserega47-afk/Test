# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-36 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-35 close `0be29ec80685753ba5286b2a03b0e02ab0048eed` (GPT review `ce5326a6882a24ed3bbb23e828df99fe7f8d15fb`, Draft PR #38); [MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md) |
| **PR** | Draft [#39](https://github.com/deniskotdavydov1991-wq/Test/pull/39) `feat/task-2026-09-17-36-antares-schedules-admission-impl`, base `feat/task-2026-09-17-35-antares-schedules-admission` HEAD `44d36f6620451525bc43cc76097cdbed4530dce1` |
| **Риск** | medium: общий job executor; mixed `schedule_loop` / `dispatch_job_background` не менять |

Реализация принятого контракта TASK-35. Tick **не** подключён к `boot`/`run`/`assemble_antares`. Serve, поток, бесконечный loop **нет**. TASK-35 повторно не закрывать. PR остаётся Draft. Merge/deploy нет.

---

## Goal

Isolated sequential `tick`: peek/commit HourlyGate, `submit_job_if_open`, tick-local `attempted`, один `add_done_callback` — без mixed `scheduler.py`.

---

## Success Criteria

- [x] `IsolatedScheduleState` + sync `tick`; `schedule_timing` / `hourly_gate` без импорта `scheduler.py`
- [x] Фильтр `ANTARES_ASSEMBLY_JOB_TYPES` до arm/gate/admission
- [x] Tick-local `attempted` по `(job_type, schedule_type)`
- [x] Подготовка deadline и candidate gate до submit; commit только после Accepted
- [x] Rejected / submit exception не потребляют due/gate; gate-skip сдвигает `next_*`
- [x] Reset clocks без очистки HourlyGate; один state — последовательные tick
- [x] S1–S18 включая S7b/c/d и S12b; формулы vs mixed source без runtime-импорта `scheduler.py`
- [x] Запретные импорты в новом процессе; boot/run schedules не запускают
- [ ] GPT review
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `tests/test_antares_schedules_admission.py` **25 passed**, exit 0, Python **3.12.10** |
| Cursor | `tests/test_scheduler_clocks_reset.py` **7 passed**, exit 0, тот же интерпретатор |
| Cursor | `tests/unit/test_antares_work_admission.py` **84 passed**, exit 0 |
| Cursor | `tests/unit/test_antares_boot.py tests/unit/test_antares_lifecycle.py` **52 passed**, exit 0 |
| Cursor | mixed `tests/test_scheduler_dispatch.py tests/test_hourly_scheduler_gate.py tests/test_scheduler_health.py` **1 failed, 15 passed** — `test_schedule_loop_calls_dispatch_job_background` (два `("wallet", "scheduler")`); дефект ожидания mixed-теста, runtime не менялся |
| GPT | pytest **не** запускал (ожидается review) |

Наборы не суммировать.

**real:** `WorkAdmission`, `executor.submit`, `add_done_callback`, `_apply_scheduler_clock_reset_if_requested`.  
**stub:** `request_job` в `modules.antares.work_admission` (контролируемая заглушка). Mixed формулы — `ast`/`exec` исходника `scheduler.py`, без `import scheduler`.

---

## Out Of Scope

internal Auto-Enable enqueue; drain/join; worker/executor/sender stop; serve; mixed-stop; wiring tick to boot/run/assembly; live polling; merge/retarget/deploy; исходное Test; правка mixed «два тика».

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-23 | реализация контракта TASK-35; Draft PR; статус **review** |
