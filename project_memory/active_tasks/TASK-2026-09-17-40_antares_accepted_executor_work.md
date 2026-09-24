# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-40 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-39 close `fa08d7db059096766effb81fd5e243a0ce53d2b3` (review `69ae53c65b66a9ad918c294d3f6f1e53192c47a0`, Draft PR #42); [MODULAR_REORG_ANTARES_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) § 2 |
| **PR** | Draft (этот срез) `feat/task-2026-09-17-40-antares-accepted-executor-work`, base `feat/task-2026-09-17-39-antares-drain-stop` |
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
| `test_submit_wins_seal_future_already_in_registry` | submit, затем seal: Future уже в реестре |
| `test_seal_wins_submit_not_called` | seal, затем submit: `executor.submit` не вызывается |
| `test_concurrent_submit_seal_keeps_registry_invariant` | гонка: Accepted ⇒ в реестре; Rejected ⇒ submit не было |
| `test_already_done_future_callback_without_deadlock` | inline Future done до callback |
| `test_submit_exception_does_not_register_or_leak_continuation` | submit raise; нет записи и continuation |
| `test_queued_and_running_futures_stay_registered` | max_workers=1: running + queued учтены |
| `test_callable_exception_completes_accounting_and_stays_visible` | wait успешен; `future.result()` бросает |
| `test_submit_job_if_open_registers_future` | job-путь в том же реестре |
| `test_cancel_handler_does_not_drop_accepted_work` | cancel `AdmittedJob.wait` |
| `test_cancel_and_repeat_wait_does_not_cancel_queued_future` | cancel + повторный cancel wait; один callback |
| `test_pending_auto_enable_after_seal_starts_and_revokes` | queued AE после seal стартует и revoke |
| `test_lifecycle_helper_does_not_wait_accepted_executor_work` | helper не подключён |

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | реализация + pytest 3.12.10: **12** TASK-40; **25** AE; **120** admission/handlers; **31** schedules; **52** boot/lifecycle. Прежние **2 failed**: `test_disable_flow_unchanged`, `test_worker_passes_user_output_file_to_registry_schedule` (те же, что на `8efa1ec…` / TASK-38). Наборы не суммировать |
| GPT | pytest **не** запускал (ожидается review) |

---

## Out Of Scope

WE sentinel/join; registry drain; sender stop; serve; mixed-stop; подключение к `run_ptb_lifecycle`; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | первый code-срез реестра Accepted executor Futures; статус **review (подготовлено)** |
