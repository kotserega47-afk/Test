# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-40 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-39 close `fa08d7db059096766effb81fd5e243a0ce53d2b3` (review `69ae53c65b66a9ad918c294d3f6f1e53192c47a0`, Draft PR #42); [MODULAR_REORG_ANTARES_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) § 2 |
| **PR** | Draft [#43](https://github.com/deniskotdavydov1991-wq/Test/pull/43) `feat/task-2026-09-17-40-antares-accepted-executor-work`, base `feat/task-2026-09-17-39-antares-drain-stop` |
| **Риск** | medium: общий executor; callback под чужим потоком; не полный drain |

Реестр Accepted executor Futures на конкретном `WorkAdmission` и `wait_accepted_executor_work`. Это **не** полный drain: WE queues, registry, sender, helper, resource shutdown — следующие срезы. TASK-39 повторно не закрывать. TASK-38 повторно не закрывать. Merge/deploy нет.

---

## Goal

Все isolated `submit_if_open` / `submit_job_if_open` / `submit_auto_enable_run_if_open` регистрируют Future до выхода из admission lock; одно ожидание terminal без `wrap_future` и без отмены работы.

---

## Success Criteria

- [x] Реестр на instance `WorkAdmission`; три submit-пути
- [x] Регистрация под тем же lock, что OPEN-check/`executor.submit`; один callback после lock
- [x] `wait_accepted_executor_work` не блокирует event loop; не полный drain
- [x] Cancel/timeout ожидания не cancel Future, не revoke continuation, не pop реестра
- [x] Повторное ожидание без второго accounting callback
- [x] Ошибка callable видна на Future; wait при этом завершается
- [x] Event/barrier тесты (см. карту); потоки join в finally
- [x] `run_ptb_lifecycle` не вызывает это ожидание
- [ ] GPT review
- [ ] merge/deploy (намеренно открыто)

---

## Карта сценариев (`tests/test_antares_accepted_executor_work.py`)

| Тест | Сценарий |
|------|----------|
| `test_controlled_submit_wins_seal_sees_registered_future` | один lock: submit держит CS после register; seal не возвращается раньше; после seal Future учтён |
| `test_controlled_seal_wins_executor_submit_not_called` | один lock: seal в CS, submit на inner acquire; `executor.submit` не вызывается |
| `test_concurrent_submit_seal_keeps_registry_invariant` | доп. неуправляемая гонка Barrier |
| `test_already_done_future_callback_without_deadlock_subprocess` | already-done в child 8s; timeout защищает pytest |
| `test_mutation_callback_under_lock_fails_in_subprocess_not_hanging_pytest` | helpers в test-файле; subprocess подменяет submit; READY-маркер, затем TimeoutExpired |
| `test_mutation_register_after_unlock_detected_in_subprocess` | helpers в test-файле; subprocess подменяет submit; маркер INVISIBLE_FUTURE и exit 7 |
| `test_submit_exception_does_not_register_or_leak_continuation` | submit raise; нет записи и continuation |
| `test_queued_and_running_futures_stay_registered` | max_workers=1: running + queued учтены |
| `test_callable_exception_completes_accounting_and_stays_visible` | wait успешен; `future.result()` бросает |
| `test_submit_job_if_open_registers_future` | job-путь в том же реестре |
| `test_cancel_handler_after_real_wait_does_not_drop_accepted_work` | cancel `AdmittedJob.wait` после входа в await |
| `test_wait_timeout_and_repeat_keeps_queued_future_once` | cancel + wait_for; ровно 2 callback; callable один раз |
| `test_cancel_wait_keeps_ae_continuation_until_wrapper_done` | pending/active живы при cancel wait; revoke только в wrapper |
| `test_pending_auto_enable_after_seal_starts_and_revokes` | queued AE после seal стартует и revoke |
| `test_lifecycle_helper_does_not_wait_accepted_executor_work` | helper не подключён |

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | исправление доказательств (этот коммит): **15 passed** TASK-40, 3.12.10. Runtime не менялся. Остальные наборы исторические на `c3eaea0a832234d0f4f87d96fa2de5d45d1d4cd5`. |
| GPT | pytest **не** запускал (ожидается review) |

---

## Out Of Scope

WE sentinel/join; registry drain; sender stop; serve; mixed-stop; подключение к `run_ptb_lifecycle`; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | первый code-срез реестра Accepted executor Futures; статус **review (подготовлено)** |
| 2026-09-24 | усиление тестов: управляемый submit/seal lock, already-done subprocess, cancel/timeout. Mutation helpers в test-файле, подмена только в subprocess; production-мутации не внесены |
| 2026-09-24 | исправление доказательств: оригинальный AdmittedJob.wait; join обоих потоков до чтения outcome; точный INVISIBLE_FUTURE/exit 7; READY до callback-under-lock |
