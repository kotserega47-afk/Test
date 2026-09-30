# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49B |
| **Статус** | review (CODE; Draft PR; **не** ACCEPTED; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.A ACCEPTED/docs-close `e80aa6cdecc681a0e94e7fbbbebd17302f668496`; TASK-40/41/43; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft [#55](https://github.com/deniskotdavydov1991-wq/Test/pull/55) `feat/task-2026-09-17-49b-antares-orchestration-p5-p7`, base `feat/task-2026-09-17-49a-antares-producer-wait` @ `e80aa6c…` |
| **Риск** | medium: false producers_complete / WE partial / registry freeze before WE |

CODE review-fix: deadline snapshot ≠ owner terminal; public `observe_drain_orchestration`; helper cancel hygiene. **P8/P9 / full P10 / production readiness / lifecycle wiring не заявлять**. **49.B не закрывать**. **49.C–E не начаты**. TASK-49.A повторно **не** закрывать.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | session-bound P4–P7 owner; WE create/join; deadline waiter partial; public progress snapshot |
| Подключено к `run_ptb_lifecycle` | **нет** (design blocker — см. ниже) |
| Выпущено | **нет** |

---

## Graph survey (49.B)

| Вопрос | Ответ |
|--------|-------|
| Application create | `apps/antares.py` builds Application **without** `AntaresUpdateIntakeQueue` (default PTB queue) |
| Producer host install | **Must** be before `initialize()` (TASK-49.A); real entry path does **not** install |
| Ownership | `ShutdownSession` (49.S) = sole P4–P7 owner; WE stop = process-local owner Task |
| Q-HLP1 | **Separate module** `modules.antares.shutdown_orchestration` |

### Integration boundary / design blocker (not closed in 49.B)

**Real entry path today**

1. `apps/antares.py` → default `asyncio.Queue` (not Antares intake).
2. `run_ptb_lifecycle` after `stop.wait()` still `admission.seal()` → `_await_cleanup(app)`.
3. Neither installs `PtbProducerWaitHost` nor calls `run_owner_drain_p4_to_p7`.

**Safe partial wire** (required before claiming lifecycle wiring):

```
pre-init Antares intake + producer host
→ arm ShutdownSession
→ P4–P7 owner (this module)
→ P8 sender (49.C) → P9 executor (49.D)
→ only then PTB cleanup / SESSION_TERMINAL
```

Inserting P4–P7 then jumping to today’s cleanup **bypasses P8/P9**. Lifecycle wiring is **not** marked done; unsafe path is **not** enabled.

---

## Delivered (review-fix)

- One P4–P7 owner Task per `ShutdownSession`; join/observe; cancel waiter ≠ phase interrupt.
- **Deadline snapshot ≠ owner completion:** expiry publishes a waiter-side partial wave; owner keeps observing late producer proof and Accepted/P5 work; no new `shutdown_deadline`; after expiry no new destructive P6/P7.
- **Public** `observe_drain_orchestration(session)` progress snapshot (not private state).
- Deadline during started P6/P7: waiter can take partial; owner continues join; rejoin observes same procedure.
- Waiter detach cancels/gathers **only** that call’s helper Tasks (not owner / producers / Accepted / continuations).
- WE create vs join; identity before cached result; owner exception after detach.

---

## Success Criteria

- [x] P5–P7 order + session-bound owner
- [x] Deadline snapshot separated from owner terminal
- [x] Public drain progress observation
- [x] WE create/join semantics
- [x] Cursor pytest on verified runtime SHA (see provenance)
- [ ] GPT re-review (Draft)
- [ ] lifecycle wiring (blocked)
- [ ] merge/deploy
- [ ] 49.C sender wiring

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | cwd `C:\Users\sereg\PycharmProjects\Test_antares_orchestration_49b`; clean tree; Python **3.12.10**; **Test SHA = runtime HEAD** `5b62d6bf31ed219cc5fac64b33b162a0a46eb999` |
| Commands | `py -3.12 -m pytest tests/unit/test_antares_shutdown_orchestration_49b.py -q --tb=line` → **18 passed**; `py -3.12 -m pytest tests/unit/test_antares_shutdown_orchestration_49b.py tests/test_antares_profile_worker_stop.py tests/unit/test_antares_shutdown_session.py tests/unit/test_antares_ptb_producer_wait.py tests/test_antares_accepted_executor_work.py tests/test_antares_registry_daemon_join.py -q --tb=line` → **113 passed** (do not sum) |
| Note | Do **not** cite `cf48534…` as SHA of current 49.B tests (test file absent there). Docs-only commits after this SHA must be confirmed by compare. |
| GPT | re-review pending on tip after docs |

---

## Out Of Scope

49.C–E; sender/executor shutdown; Ready/merge/retarget/deploy; full shutdown claim; closing 49.B; enabling unsafe lifecycle wire; re-close 49.A/49.S.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: P5–P7 + WE owner-session; Draft PR #55 |
| 2026-09-30 | Review-fix: session-bound owner + WE create/join + design blocker |
| 2026-09-30 | Review-fix: deadline snapshot ≠ owner terminal; public observe; helpers try/finally; Test SHA `5b62d6b…` (18/113); Draft for GPT re-review |
