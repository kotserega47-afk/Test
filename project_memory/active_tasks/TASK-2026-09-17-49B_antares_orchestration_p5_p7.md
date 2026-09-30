# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49B |
| **Статус** | review (CODE; Draft PR; **не** ACCEPTED; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.A ACCEPTED/docs-close `e80aa6cdecc681a0e94e7fbbbebd17302f668496`; TASK-40/41/43; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft [#55](https://github.com/deniskotdavydov1991-wq/Test/pull/55) `feat/task-2026-09-17-49b-antares-orchestration-p5-p7`, base `feat/task-2026-09-17-49a-antares-producer-wait` @ `e80aa6c…` |
| **Риск** | medium: false producers_complete / WE partial / registry freeze before WE |

CODE review-fix: session-bound P4–P7 owner Task + WE create/join split. **P8/P9 / full P10 / production readiness / lifecycle wiring не заявлять**. **49.B не закрывать**. **49.C–E не начаты**. TASK-49.A повторно **не** закрывать.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | session-bound `run_owner_drain_p4_to_p7` owner Task; WE create vs join; regressions |
| Подключено к `run_ptb_lifecycle` | **нет** (design blocker — см. ниже) |
| Выпущено | **нет** |

---

## Graph survey (49.B)

| Вопрос | Ответ |
|--------|-------|
| Application create | `apps/antares.py` builds Application **without** `AntaresUpdateIntakeQueue` (default PTB queue) |
| Producer host install | **Must** be before `initialize()` (TASK-49.A); real entry path does **not** install |
| Producer sources | Antares intake + wrapped `process_update` / `Application.create_task`; raw `asyncio.create_task` unsupported |
| Supported mode | Pass when host pre-installed + Antares queue + issuer binding + post-COMPLETE entry refuse |
| Ownership | `ShutdownSession` (49.S) = sole P4–P7 owner; WE stop = process-local owner Task; registry wait = TASK-43 API |
| Q-HLP1 | **Separate module** `modules.antares.shutdown_orchestration` — does not duplicate ShutdownSession |

### Integration boundary / design blocker (not closed in 49.B)

**Real entry path today**

1. `apps/antares.py` → `Application.builder()…build()` → default `asyncio.Queue` (not Antares intake).
2. `run_ptb_lifecycle` after `stop.wait()` still `admission.seal()` → `_await_cleanup(app)` using the **old** helper path.
3. Neither installs `PtbProducerWaitHost` pre-initialize nor calls `run_owner_drain_p4_to_p7`.

**Safe partial wire scheme (required before claiming lifecycle wiring)**

```
pre-init: bind AntaresUpdateIntakeQueue + install PtbProducerWaitHost once
  → initialize/start (49.A conditions)
  → arm ShutdownSession (49.S)
  → P4–P7 owner join (this module; session.shutdown_deadline only)
  → P8 sender (49.C) → P9 executor (49.D)
  → then PTB cleanup / SESSION_TERMINAL (not before)
```

**Why unsafe in current scope:** inserting P4–P7 then jumping to today’s `_await_cleanup` **bypasses P8/P9**. Enabling that path would claim a false drain order. Therefore:

- lifecycle wiring is **not** marked done;
- the unsafe seal→cleanup shortcut is **not** enabled;
- **design blocker** remains until a wire plan keeps P8/P9 before cleanup (or explicitly refuses full graceful).

Do **not** invent `producers_complete=True` on the unwired entry path.

### Wiring conditions retained (49.A)

1. Single producer-host install before initialize/start  
2. Antares intake + supported PTB graph  
3. No untracked background producers  
4. Application/issuer binding  
5. Permanent entry refuse after COMPLETE  

---

## Delivered (review-fix)

- **One** P4–P7 owner Task per `ShutdownSession`; public API joins/observes; waiter cancel ≠ phase interrupt; concurrent/repeat ≠ second orchestration.
- Identity checks (issuer / Application / admission / owner loop) even after accepted proof; progress / partial / errors kept on owner state.
- Deadline = `session.shutdown_deadline` only (no separate per-phase drain budget). Expiry publishes partial remainder; does not cancel Accepted/continuation or started owner ops; forbids new destructive phases; P7 success ≠ SESSION_TERMINAL.
- WE stop: create-path unfinished gate vs join-path (tolerate this-session sentinel in `unfinished_tasks`; no second sentinel); identity before cached result; owner exception retrieved after all waiters detach.
- Regressions: P5/P7 waiter cancel; two orchestration waiters; deadline mid-P5 and mid-started P6; late WE before freeze; registry not before WE join; WE hold-before-sentinel join; owner error after detach.

---

## Success Criteria

- [x] P5–P7 order + real APIs under session-bound owner
- [x] WE create/join + owner-session semantics
- [x] Q-HLP1 module layout
- [x] Cursor pytest (see provenance)
- [ ] GPT re-review (Draft)
- [ ] lifecycle wiring (blocked — see design blocker)
- [ ] merge/deploy
- [ ] 49.C sender wiring

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10** @ Test SHA `cf48534a2b0b341e0381ac34688eeb7a746a99f9`; review-fix HEAD `a470a9750f8fa05baa1d94d1d80c74e7d8754e0a`: orchestration+WE **15**; related 40/41/43/49A/49S+49B = **110** (do not sum) |
| GPT | prior CHANGES on `f7477f8…`; re-review pending on `a470a97…` |

---

## Out Of Scope

49.C–E; sender/executor shutdown; Ready/merge/retarget/deploy; full shutdown claim; closing 49.B; enabling unsafe lifecycle wire; re-close 49.A/49.S.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: P5–P7 + WE owner-session; Draft PR #55; Test SHA `0867b48…` |
| 2026-09-30 | Review-fix HEAD `a470a97…`: session-bound owner + WE create/join + design blocker; Test SHA `cf48534…` (15/110); Draft for GPT re-review |
