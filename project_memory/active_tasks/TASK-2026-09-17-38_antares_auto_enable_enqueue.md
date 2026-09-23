# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-38 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-37 close `8efa1ec9a88d1ad628e679bee83755d758dc9382` (review `741f5ccf7e59e172d5ec427fa630343238bb768c`, Draft PR #40); [MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md](../ops/MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md) |
| **PR** | Draft [#41](https://github.com/deniskotdavydov1991-wq/Test/pull/41) `feat/task-2026-09-17-38-antares-auto-enable-enqueue-impl`, base `feat/task-2026-09-17-37-antares-auto-enable-enqueue` |
| **Риск** | medium: shared WE `CONVERSION_AUTO` queue; mixed ensure/put order не менять |

Review **пройден**. GPT проверил код/diff и тесты на полном SHA `f12811035433bce0306ef9ef9d328db43652795a`. GPT pytest **не** запускал. Этот docs-коммит — закрытие TASK-38. Isolated Auto-Enable continuation **реализован**, **не выпущен**. Harness `_HarnessQueue.end_loop` **не** production stop API. Already-dead worker и смерть worker до `get()` остаются блокерами lifecycle (TASK-39). TASK-37 повторно не закрывать. PR #41 остаётся Draft. Merge/deploy нет.

---

## Goal

Isolated `/auto_enable_run` регистрирует continuation до `executor.submit(wrapper)`; bound `enqueue_auto_enable_batch` проверяет её до credentials/ensure/put; AE worker Future завершается один раз.

---

## Success Criteria

- [x] Continuation map на конкретном `WorkAdmission`; pending до submit; cleanup при submit exception
- [x] Wrapper activate + TLS/owner_thread; finally revoke при любом выходе
- [x] seal / cancel TG-handler не отзывают Accepted run
- [x] isolated `/auto_enable_run` — `submit_auto_enable_run_if_open`; `/auto_enable_plan` без continuation
- [x] bound enqueue gate до credentials/ensure/put; unbound mixed прежний
- [x] qsize/info/exception-log после put не отменяют wait; terminal worker не зависит от log.exception
- [x] E1–E15: чужой admission, отозванная запись, чужой поток, реальный ImportError до execute; E9 вход в activate до возврата submit
- [x] Тестовый harness: временный registry, Finite/Harness Queue, join потоков, restore handlers
- [x] GPT review кода/diff и тестов на `f128110…`; pytest GPT не запускал
- [ ] merge/deploy (намеренно открыто)

---

## Карта сценариев (`tests/test_antares_auto_enable_enqueue.py`)

| Тест | Сценарий |
|------|----------|
| `test_e1_seal_before_new_run` | E1 |
| `test_e2_accepted_then_seal_before_enqueue` | E2 |
| `test_e3_n_batches_one_run` | E3 |
| `test_e3b_batches_after_seal` | E3b |
| `test_e4_error_before_put` | E4 |
| `test_e5_put_ok_execute_set_exception` | E5 |
| `test_e6_isolated_direct_enqueue_without_continuation` | E6 |
| `test_e6_foreign_admission_instance` | чужой admission-instance; ensure/put нет |
| `test_e6_revoked_record_replay` | повтор отозванной записи; ensure/put нет |
| `test_e6_foreign_thread` | чужой поток + живой token; ensure/put нет |
| `test_e7_seal_during_result_wait` | E7 |
| `test_e8_unbound_mixed_enqueue_unchanged` | E8 |
| `test_e9_callable_starts_before_submit_returns` | E9: вход в activate до возврата submit; тело до unlock нет |
| `test_e10_queued_accepted_then_seal_then_start` | E10 |
| `test_e11_submit_exception_cleans_continuation` | E11 |
| `test_e12_cancel_tg_handler_after_accepted` | E12 |
| `test_e13_qsize_info_and_exception_log_after_put` | E13: qsize/info/exception-log; один put; wait |
| `test_e14_wait_log_and_exception_logger` | E14 wait-log + exception logger; task_done |
| `test_e14_execute_and_exception_logger` | E14 execute + exception logger |
| `test_e14_start_log` | E14 start-log |
| `test_e14_real_import_before_execute` | E14 `from … import execute_enable_batch` ImportError |
| `test_e15_diag_after_set_result_does_not_set_exception` | E15 finished-log + exception logger; диагностика до конца теста |
| `test_tls_cleared_on_executor_thread_reuse` | TLS после revoke на том же TPE thread |
| `test_plan_does_not_register_continuation` | `/auto_enable_plan` |
| `test_empty_candidates_bound_without_continuation_rejected` | пустой batch без token |

---

## Происхождение проверки

Наборы не суммировать. Прогон Cursor — SHA review `f12811035433bce0306ef9ef9d328db43652795a`.

### Успешно

| Кто | Что |
|-----|-----|
| Cursor | `tests/test_antares_auto_enable_enqueue.py` **25 passed**, exit 0, Python **3.12.10**, SHA `f128110…` |
| Cursor | `tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py` **120 passed**, exit 0, тот же SHA |
| Cursor | `tests/unit/test_antares_boot.py tests/unit/test_antares_lifecycle.py` **52 passed**, exit 0, тот же SHA |
| GPT | код/diff и тесты на `f12811035433bce0306ef9ef9d328db43652795a`; pytest **не** запускал |

### Прежние падения (не этого PR; те же на base `8efa1ec…`)

| Кто | Что |
|-----|-----|
| Cursor | orchestrator/concurrency: **40 passed, 1 failed** `test_disable_flow_unchanged` — `patch("automation.worker.schedule_registry_append")`; в `automation/worker.py` символа нет на `8efa1ec…` и на `f128110…` |
| Cursor | ingest/worker: **36 passed, 1 failed** `test_worker_passes_user_output_file_to_registry_schedule` — `git grep stage_registry_result_copy 8efa1ec -- automation/worker.py` пусто; совпадение только тест L232; HEAD тот же пробел |

**real:** `WorkAdmission`, `executor.submit`, production `worker_loop` на тестовой `_HarnessQueue`, `Queue`, `Future`.  
**stub:** `execute_enable_batch` (кроме E14 import delattr), credentials, Playwright. Harness `end_loop` — только тест, **не** production stop API.

---

## Out Of Scope

надзор/restart мёртвого worker; production drain/join; serve; scheduled Auto-Enable; mixed-stop; live кабинеты/Telegram; merge/retarget/deploy; исходное Test; повторное закрытие TASK-37.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-23 | реализация контракта TASK-37; статус **review (подготовлено)** |
| 2026-09-23 | review-fix: безопасная диагностика batch/worker; harness без clear живых потоков; E6/E9/E14/E15 |
| 2026-09-23 | GPT review на `f128110…`; закрытие docs; continuation не выпущен; drain — TASK-39 |
