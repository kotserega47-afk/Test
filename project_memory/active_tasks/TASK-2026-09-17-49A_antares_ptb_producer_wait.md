# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49A |
| **Статус** | review (CODE; GPT review pending; Q-PTB1 **не** закрыт; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.S ACCEPTED/docs-close `1f078eb7704c3f3d6cfd90a59b5f16ce9dc0b6c5` (accepted review `f7dd672…`, Test SHA `bab80586…`, Draft PR #53); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft (creating) `feat/task-2026-09-17-49a-antares-producer-wait`, base `feat/task-2026-09-17-49s-antares-shutdown-session` @ `1f078eb…` |
| **Риск** | medium: false producers_complete / seal races / foreign attestation |

CODE: Q-PTB1 producer-wait primitive. **Production wiring в lifecycle/P5–P9 отсутствует**. WE/registry/sender/executor shutdown **не** подключены. **Q-PTB1 остаётся OPEN** до GPT acceptance. 49.B–E **не** начаты. TASK-39–49S повторно не закрывать.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | `AntaresUpdateIntakeQueue`, `PtbProducerWaitHost`, attestation mint + shutdown_session bind checks, unit tests on real PTB 22.8 |
| Подключено | **нет** (`run_ptb_lifecycle` / boot / P5–P9 не wired) |
| Выпущено | **нет** |

---

## PTB survey (installed **22.8**)

| Вопрос | Ответ |
|--------|-------|
| Что создаёт работу | Updates в `Application.update_queue`; fetcher `__update_fetcher`; `__process_update_wrapper`; `Application.create_task` (`__create_task_tasks`) для concurrent updates и `block=False` handlers |
| Как прекращаются новые producers | Seal `AntaresUpdateIntakeQueue` (refuse update `put`; allow PTB `_STOP_SIGNAL` for later `app.stop`); Updater must not be running |
| Как доказывается завершение | After seal: queue empty + `_unfinished_tasks==0` + no live `__create_task_tasks` + `current_concurrent_updates==0`, double-checked after yield |
| Поддерживается | Same graph as `run_ptb_lifecycle`: `SimpleUpdateProcessor`, updater present, no persistence/JobQueue, Antares intake queue, Application running, updater not running |
| Отклоняется | Plain `asyncio.Queue`, unsupported processor/persistence/job_queue, updater running, Application not running, foreign/`for_tests` attestation on bound host |

**Не** proof: stop Event, admission SEALED, empty queue without seal, no polling, caller bool, drain deadline expiry.

---

## Goal

Truthful `ProducersCompleteAttestation` for post-OPEN cleanup gate — without closing Application HTTP and without inventing producers_complete.

---

## Success Criteria

- [x] Sealed intake + idle observation on real PTB APIs (no live Telegram)
- [x] Event/barrier scenarios 1–8
- [x] Attestation bound to Application; shutdown_session rejects foreign/`for_tests` when bound
- [x] Waiter cancel ≠ cancel producers; deadline → incomplete; late complete same host; repeat joins owner wait
- [ ] GPT review / Q-PTB1 acceptance
- [ ] lifecycle wiring (49.B+)
- [ ] merge/deploy

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10** @ Test SHA `58cfb1cb40f29488731dcbad03bc168dd8ab7040`: producer_wait **8**; + shutdown_session + accepted_executor_work + work_admission = **140**; lifecycle unit **19** (наборы не суммировать); CI PASS не заявлять |
| GPT | pending |

---

## Out Of Scope

P5–P9 wiring; WE/registry/sender/executor stop; serve/live polling; 49.B–E; Ready/merge/retarget/deploy; closing Q-PTB1 before GPT accept; повторное закрытие TASK-39–49S.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: producer-wait primitive + intake seal; Draft PR base 49.S |
