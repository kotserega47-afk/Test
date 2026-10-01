# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49D |
| **Статус** | open (implemented; awaiting GPT review; Draft PR; docs-close / merge не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.C open / implemented ACCEPTED @ `38a1b4d…` (P8); TASK-49.B OPEN (integration blocker); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) §4.4 EX1 |
| **PR** | Draft (pending create) `feat/task-2026-09-17-49d-antares-orchestration-p9`, base `feat/task-2026-09-17-49c-antares-orchestration-p8` @ `38a1b4d…` |
| **Риск** | medium: false P9 without EX1 / cancel Accepted Futures / recreate executor / block PTB loop / second owner |

**Implemented scope for GPT review:** production `stop_isolated_job_executor` (Q-EX2) + EX1-gated P9 on the same session-bound drain owner (`run_owner_drain_p4_to_p9`). **TASK-49.D не закрыта**: production lifecycle/P10 wiring / SESSION_TERMINAL / full shutdown **не** выполнены. **49.B и 49.C остаются OPEN** (integration blocker сохранён). **49.E не начат**. Ready/merge/retarget/deploy **нет**.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | `stop_isolated_job_executor` (Q-EX2); `JobExecutorStopResult`; permanent stop / no recreate; no `cancel_futures=True`; async thread observe; EX1 gates; `run_owner_drain_p4_to_p9`; partial sender → P9; foreign ownership → P9 skip; sender remainder preserved; P9 ≠ SESSION_TERMINAL |
| Подключено к `run_ptb_lifecycle` | **нет** (49.B integration blocker) |
| Выпущено | **нет** |

---

## Q-EX2 (this slice)

**Decision:** public production API name is **`stop_isolated_job_executor`**.

- Targets the process-global Antares pool (`thread_name_prefix="job-worker"`) only.
- Does not create an executor only to shut it down.
- Does not use `_reset_job_executor_for_tests`.
- Does not cancel Accepted Futures (`cancel_futures=True` forbidden).
- Does not block the PTB loop with `shutdown(wait=True)` / thread join on the caller loop.
- After stop, `get_job_executor` refuses recreate (`JobExecutorStoppedError`).
- Does not touch the default/foreign executor; mixed gate unchanged.
- Ownership: isolated Antares job-worker pool only — no claim over a general/default TPE.

---

## EX1 (P9 admission)

P9 allowed only when all proven (phase results / identity, not empty tuples alone):

1. admission SEALED  
2. truthful `producers_complete` attested  
3. Accepted Futures empty + continuations empty  
4. WE stop owner-session success (`_profile_workers_stop_done` + result identity)  
5. registry wait success (`_daemon_ops_wait_done` + freeze + joined identity)  
6. sender attempted + structured `SenderPhaseSnapshot`  

Partial sender (structured boundary) **allows** P9 under EX1. Foreign ownership refuse / incomplete WE/registry / missing sender outcome → P9 skipped, no executor side effects. EX1 does **not** override the session deadline gate for *starting* P9; already-started executor stop is observed separately.

Same owner continues past P8 settle into P9 — no second owner; live sender observe preserved until settle; sender not declared complete merely to unlock P9.

---

## Integration blocker (unchanged from 49.B/49.C)

1. Production entry uses default Queue.  
2. Producer host not installed pre-initialize.  
3. `run_ptb_lifecycle` does not call P4–P9.  

Required order: pre-init intake/host → session → P4–P7 → P8 → **P9** → cleanup (P10 = 49.E / later).

---

## Success Criteria

- [x] Q-EX2 public API + production stop semantics
- [x] EX1-gated P9 on session-bound owner
- [x] Target regressions (full EX1, missing gates, partial/foreign, deadline, concurrent, absent, no-resurrect)
- [x] Cursor pytest on verified runtime SHA `95d083a…`
- [ ] GPT review
- [ ] lifecycle wiring
- [ ] docs-close / merge/deploy
- [ ] 49.E

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | cwd `C:\Users\sereg\PycharmProjects\Test_antares_orchestration_49d`; clean runtime tree @ **Test SHA** `95d083a038efd5f3a9b84603ff973dae04d4420e`; Python **3.12.10** |
| Commands | `py -3.12 -m pytest tests/unit/test_job_executor_stop_49d.py tests/unit/test_antares_shutdown_orchestration_49d.py -q --tb=line` → **19 passed**; set A (49d+49c+49b+sender_full_stop) → **85 passed**; isolated `tests/unit/test_antares_sender_ownership.py` → **10 passed** (do not sum; O9 requires isolation from telegram_bot import) |
| GPT | pending review |

---

## Out Of Scope

Lifecycle/P10 wiring; docs-close 49.B/49.C/49.D; Ready/merge/retarget/deploy; 49.E; SESSION_TERMINAL / full shutdown claim; Move agent root; mutate source Test / prior worktrees.
