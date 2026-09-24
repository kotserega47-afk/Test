# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-41 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-40 close `85c0b75ca7b86b3648c8ad79d0caeedf413533d5` (review `04c6f8f4b29460e792c41a6cd7c4aa098667a39f`, Draft PR #43); [MODULAR_REORG_ANTARES_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) § 4.1 |
| **PR** | Draft [#44](https://github.com/deniskotdavydov1991-wq/Test/pull/44) `feat/task-2026-09-17-41-antares-profile-worker-stop`, base `feat/task-2026-09-17-40-antares-accepted-executor-work` |
| **Риск** | medium: process-local `_profile_workers`; freeze после снимка; не полный graceful |

Production `ProfileWorkerStopSentinel` + `stop_isolated_profile_workers`. Не полный drain: registry daemon, sender, executor shutdown, helper, serve — следующие срезы. TASK-40/39 повторно не закрывать. Merge/deploy нет.

---

## Goal

После sealed admission, завершённых PTB producers (аттестация caller), idle Accepted executor work, пустого continuation map и `unfinished_tasks==0` положить production sentinel в каждую profile queue и join потоки без блокировки event loop.

---

## API и предусловия

| Имя | Роль |
|-----|------|
| `ProfileWorkerStopSentinel` / `PROFILE_WORKER_STOP` | production token; `worker_loop` `break` + `task_done` |
| `snapshot_isolated_profile_workers()` | наблюдение живого registry во время drain |
| `stop_isolated_profile_workers(admission, *, producers_complete, timeout=None)` | freeze registry, один sentinel на worker, join через `asyncio.to_thread` |
| `IsolatedProfileWorkerStopError.remainder` | failure без retry |
| `IsolatedProfileWorkerCreateRejected` | `_ensure_profile_worker` после freeze |

Полное использование только если:

1. `bound_admission() is admission` (mixed/unbound не стопать);
2. `producers_complete is True` (PTB producers — аттестация caller, этот срез их не считает);
3. `admission.state is SEALED`;
4. `accepted_executor_futures()` пуст;
5. continuation map пуст;
6. у каждого worker `unfinished_tasks == 0` (пустой `qsize` недостаточен);
7. thread жив до sentinel.

Пустая Queue не разрешает stop. `_HarnessQueue.end_loop` не используется. Повторный успешный stop не кладёт второй sentinel.

---

## Success Criteria

- [x] Production sentinel в `worker_loop`; не harness
- [x] Freeze registry; late `_ensure_profile_worker` rejected
- [x] Join вне event loop (`asyncio.to_thread`)
- [x] Dead worker / deadline → remainder, без retry
- [x] `run_ptb_lifecycle` не вызывает stop
- [ ] GPT review
- [ ] merge/deploy (намеренно открыто)

---

## Карта сценариев (`tests/test_antares_profile_worker_stop.py`)

| Тест | Сценарий |
|------|----------|
| `test_idle_worker_exits_on_sentinel_and_joins` | idle + sealed → join |
| `test_active_item_empty_queue_is_not_drained` | get без task_done |
| `test_late_ae_worker_after_seal_is_in_final_stop_list` | AE после seal создаёт профиль |
| `test_pending_continuation_blocks_stop` | pending continuation |
| `test_active_continuation_blocks_stop` | active continuation |
| `test_repeated_stop_does_not_put_second_sentinel` | второй stop без extra put |
| `test_dead_worker_fails_without_retry` | мёртвый thread |
| `test_join_deadline_fails_without_retry` | timeout join |
| `test_sentinel_task_done_clears_unfinished` | unfinished==0 после stop |
| `test_unbound_admission_does_not_stop_workers` | mixed/unbound |
| `test_frozen_registry_rejects_new_worker_after_stop` | freeze после stop |

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **12 passed** TASK-41 (3.12.10) на SHA этого PR. Related на том же SHA, наборы не суммировать: **15** accepted executor; **25** AE enqueue; **52** boot+lifecycle; **7** jobs. Worker unit: **8 passed, 1 failed** `test_worker_passes_user_output_file_to_registry_schedule` (как на TASK-38 / `8efa1ec…`). `test_disable_flow_unchanged` в этом прогоне не падал |
| GPT | pytest **не** запускал (ожидается review) |

---

## Out Of Scope

helper; registry daemon; sender; executor shutdown; serve; mixed-stop; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | production WE sentinel/join; статус **review (подготовлено)** |
