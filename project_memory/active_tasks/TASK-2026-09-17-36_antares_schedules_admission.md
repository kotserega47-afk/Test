# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-36 |
| **Статус** | review (не закрыт; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-35 close `0be29ec80685753ba5286b2a03b0e02ab0048eed` (GPT review `ce5326a6882a24ed3bbb23e828df99fe7f8d15fb`, Draft PR #38); [MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_SCHEDULES_ADMISSION.md) |
| **PR** | Draft [#39](https://github.com/deniskotdavydov1991-wq/Test/pull/39) `feat/task-2026-09-17-36-antares-schedules-admission-impl`, base `feat/task-2026-09-17-35-antares-schedules-admission` |
| **Риск** | medium: общий job executor; mixed `schedule_loop` / `dispatch_job_background` не менять |

Реализация принятого контракта TASK-35. Tick **не** подключён к `boot`/`run`/`assemble_antares`. Serve, поток, бесконечный loop **нет**. TASK-36 **не закрывать**. TASK-35 повторно не закрывать. PR остаётся Draft. Merge/deploy нет.

Review HEAD, к которому правили: `5958c3a2d4bd69ffb98732be32f39a417664a3ae`.

---

## Goal

Isolated sequential `tick`: peek/commit HourlyGate, `submit_job_if_open`, tick-local `attempted`, один `add_done_callback` — без mixed `scheduler.py`.

---

## Success Criteria

- [x] `IsolatedScheduleState` + sync `tick`; `schedule_timing` / `hourly_gate` без импорта `scheduler.py`
- [x] Фильтр `ANTARES_ASSEMBLY_JOB_TYPES` до arm/gate/admission
- [x] Tick-local `attempted` по `(job_type, schedule_type)`
- [x] Подготовка deadline и candidate gate до submit; commit только после Accepted
- [x] SEALED на входе tick: нет arm, нет gate-skip потребления, нет submit; reset clocks отдельно
- [x] Ранняя проверка SEALED не резервирует допуск; `submit_job_if_open` остаётся атомарным
- [x] Rejected / submit exception не потребляют due/gate; gate-skip (OPEN) сдвигает `next_*`
- [x] S12: job удерживается Event до observer; лог после фактической записи callback; нет повторного submit до deadline
- [x] S12b: commit виден в синхронном callback уже done Future
- [ ] GPT review (повторно после review-fix)
- [ ] merge/deploy (намеренно открыто)

---

## Карта сценариев (`tests/test_antares_schedules_admission.py`)

| Тест | Сценарий |
|------|----------|
| `test_s1_interval_due_open_one_submit` | interval due + OPEN → один submit, next от `now_ts` |
| `test_s2_cron_arm_then_due` | cron arm, затем due → Accepted, next от `now_dt` |
| `test_s3_foreign_key_no_admission_no_clocks` | чужой key → нет admission, нет часов |
| `test_s4_seal_before_submit_lock_barrier` | OPEN на входе; seal после hourly peek, до submit; Rejected; clocks/gate не потреблены; executor нет |
| `test_s5_submit_before_seal_future_lives` | submit держит lock; seal ждёт; Future живёт |
| `test_sealed_empty_clocks_interval_and_cron_no_arm` | уже SEALED + пустые clocks → нет arm, executor нет |
| `test_sealed_due_hourly_gate_skip_does_not_consume` | уже SEALED + due hourly + gate-skip (interval и cron) → next/gate прежние, executor нет |
| `test_sealed_reset_clocks_without_arm` | SEALED + admin reset → clocks пусты, gate жив, нет arm |
| `test_s6_closed_rejects_without_consuming` | closed → Rejected, due не потреблён |
| `test_s7_submit_exception_does_not_consume` | submit exception до Future |
| `test_s7b_two_interval_rows_one_attempt` | две interval rows, submit exception → одна попытка |
| `test_two_interval_rows_rejected_one_admission_attempt` | две interval rows + Rejected → одна попытка admission |
| `test_hourly_submit_exception_same_bucket_retries` | hourly submit exception → next tick того же bucket принимает |
| `test_final_daily_reject_and_submit_error_do_not_commit_key` | `final_daily_time`: отказ/ошибка не фиксируют `last_final_key` |
| `test_s7c_next_tick_retries_unconsumed` | следующий tick после submit error |
| `test_s7d_invalid_cron_at_due_no_submit` | битый cron на due |
| `test_s8_hourly_rejected_keeps_bucket` | hourly peek fire → Rejected, затем Accepted |
| `test_s9_hourly_accepted_same_bucket_no_duplicate` | тот же bucket без второго submit |
| `test_s10_final_daily_fire_already_not_due` | final fire / skip / already |
| `test_s11_gate_skip_writes_next_not_last` | OPEN gate-skip сдвигает `next_*`, не last_* |
| `test_s12_future_error_logged_once_no_rollback` | Event hold до observer; лог 1× после записи callback; нет submit до deadline |
| `test_s12b_future_already_done_callback_sees_commit` | commit во время синхронного callback done Future |
| `test_s13_observer_attach_error_keeps_accepted_no_duplicate` | attach failure; следующий tick не дублирует слот |
| `test_s14_reset_clocks_keeps_gate` | reset при OPEN: clocks пусты затем arm; gate жив |
| `test_s15_two_interval_rows_first_accepted` | общий `next_every`; вторая row молчит |
| `test_s16_interval_and_cron_two_clocks` | два clock, до двух submit |
| `test_s17_forbidden_imports_in_new_process` | mixed `scheduler` / `telegram_bot` / `tg_commands` нет |
| `test_s18_get_job_params_outside_admission_lock` | peek I/O вне admission lock |
| `test_boot_run_source_does_not_start_schedules` | boot/run/assembly не зовут tick |
| `test_cron_formulas_match_mixed_source_without_importing_scheduler` | cron vs mixed source `exec` |
| `test_hourly_formulas_match_mixed_source_without_importing_scheduler` | hourly peek vs mixed mutate-on-eval |

Thread-тесты: lock `release` в `finally`; ошибки рабочих потоков в основной тест; `join`/`stub.release` при неуспехе.

---

## Происхождение проверки

Наборы не суммировать. SHA прогона — HEAD после этого review-fix (см. git).

| Кто | Что |
|-----|-----|
| Cursor | `tests/test_antares_schedules_admission.py` **31 passed**, exit 0, Python **3.12.10** |
| Cursor | `tests/test_scheduler_clocks_reset.py` **7 passed**, exit 0 |
| Cursor | `tests/unit/test_antares_work_admission.py` **84 passed**, exit 0 |
| Cursor | `tests/unit/test_antares_boot.py tests/unit/test_antares_lifecycle.py` **52 passed**, exit 0 |
| Cursor | mixed «два тика» **не** гонялся и **не** исправлялся в этом scope |
| GPT | повторный review ожидается; pytest GPT не запускал |

**real:** `WorkAdmission`, `executor.submit`, `add_done_callback`, `_apply_scheduler_clock_reset_if_requested`.  
**stub:** `request_job` в `modules.antares.work_admission`. Mixed формулы — `ast`/`exec` исходника `scheduler.py`, без `import scheduler`.

---

## Out Of Scope

internal Auto-Enable enqueue; drain/join; worker/executor/sender stop; serve; mixed-stop; wiring tick to boot/run/assembly; live polling; merge/retarget/deploy; исходное Test; правка mixed «два тика».

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-23 | реализация контракта TASK-35; Draft PR #39; статус **review** |
| 2026-09-23 | review-fix: SEALED no-op arm/gate-skip; S12 Event/log; матрица Rejected/hourly/final/S13 next tick; thread finally |
