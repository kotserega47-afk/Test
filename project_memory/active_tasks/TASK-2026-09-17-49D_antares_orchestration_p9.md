# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49D |
| **Статус** | open (implemented + review-fix; awaiting GPT re-review; Draft PR; docs-close / merge не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.C open / implemented ACCEPTED @ `38a1b4d…` (P8); TASK-49.B OPEN (integration blocker); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) §4.4 EX1 |
| **PR** | Draft [#57](https://github.com/deniskotdavydov1991-wq/Test/pull/57) `feat/task-2026-09-17-49d-antares-orchestration-p9`, base `feat/task-2026-09-17-49c-antares-orchestration-p8` @ `38a1b4d…` |
| **Риск** | medium: false P9 without EX1 / cancel Accepted Futures / recreate executor / block PTB loop / second owner |

**Implemented scope for GPT re-review:** production `stop_isolated_job_executor` (Q-EX2) + admission-bound ownership + EX1-gated P9 on the same session-bound drain owner (`run_owner_drain_p4_to_p9`), including live-sender EX1 and non-terminal P9 observe. **TASK-49.D не закрыта**. **49.B и 49.C остаются OPEN**. **49.E не начат**. Ready/merge/retarget/deploy **нет**.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | `bind_job_executor_to_admission` / ownership refuse before shutdown+cache; `stop_isolated_job_executor(admission=…)`; truthful absent (`shutdown_called=False`, `recreate_refused`); EX1 live sender partial → P9 while sender observing; `deadline_executor_threads` non-terminal + same-owner continue; WE/registry EX1 by admission token |
| Подключено к `run_ptb_lifecycle` | **нет** (49.B integration blocker) |
| Выпущено | **нет** |

---

## Q-EX2 (this slice)

**Decision:** public production API name is **`stop_isolated_job_executor(*, admission, timeout=…)`**.

- Ownership identity = exact `id(WorkAdmission)` from `bind_job_executor_to_admission` (not thread prefix, not sender proof).
- Mixed / foreign / unproven → refuse **before** shutdown and **before** cached terminal.
- Absent stop: `shutdown_called=False`, `recreate_refused=True`; identity/`was_absent` preserved after releasing live refs.
- No create-to-stop; no test reset; no `cancel_futures=True`; no PTB-loop `wait=True`.
- Default/foreign TPE untouched.

---

## EX1 (P9 admission)

P9 allowed when proven (admission-bound identity, not joined-tuple equality):

1. admission SEALED  
2. truthful `producers_complete` attested  
3. Accepted Futures empty + continuations empty  
4. WE stop done + `_we_stop_admission_token == id(admission)`  
5. registry wait done/frozen + `_daemon_ops_admission_token == id(admission)`  
6. sender attempted + structured `SenderPhaseSnapshot` (incl. live_observe)

Live structured sender partial **may start P9** under EX1 while sender observation continues on the same owner (sender not declared stopped). Foreign ownership / incomplete WE/registry / missing sender → skip, no side effects. Deadline still gates *starting* P9; already-started stop is observed without a new deadline/second shutdown. Cancel of all waiters does not stop owner observation.

---

## Integration blocker (unchanged from 49.B/49.C)

1. Production entry uses default Queue.  
2. Producer host not installed pre-initialize.  
3. `run_ptb_lifecycle` does not call P4–P9.  

---

## Success Criteria

- [x] Q-EX2 public API + production stop semantics + admission ownership
- [x] EX1-gated P9 incl. live sender partial
- [x] Non-terminal P9 deadline snapshot + same-owner rejoin
- [x] Target regressions on Test SHA `ec9cd63…`
- [ ] GPT re-review
- [ ] lifecycle wiring
- [ ] docs-close / merge/deploy
- [ ] 49.E

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | cwd `C:\Users\sereg\PycharmProjects\Test_antares_orchestration_49d`; clean runtime tree @ **Test SHA** `ec9cd63d2e9650ce980ee4ff8debb06ff8c3885e`; Python **3.12.10** |
| Commands | target `test_job_executor_stop_49d` + `test_antares_shutdown_orchestration_49d` → **23 passed**; set A (49d+49c+49b+sender_full_stop) → **89 passed**; deeper related (49b+WE+registry+49S+49A) → **98 passed**; schedules admission → **31 passed**; isolated ownership → **10 passed** (do not sum; O9 isolation) |
| GPT | pending re-review |

---

## Out Of Scope

Lifecycle/P10 wiring; docs-close 49.B/49.C/49.D; Ready/merge/retarget/deploy; 49.E; SESSION_TERMINAL / full shutdown claim; Move agent root; mutate source Test / prior worktrees.
