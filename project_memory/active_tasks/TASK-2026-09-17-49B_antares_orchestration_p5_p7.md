# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49B |
| **Статус** | review (CODE; Draft PR; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.A ACCEPTED/docs-close `e80aa6cdecc681a0e94e7fbbbebd17302f668496`; TASK-40/41/43; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft [#55](https://github.com/deniskotdavydov1991-wq/Test/pull/55) `feat/task-2026-09-17-49b-antares-orchestration-p5-p7`, base `feat/task-2026-09-17-49a-antares-producer-wait` @ `e80aa6c…` |
| **Риск** | medium: false producers_complete / WE partial / registry freeze before WE |

CODE: orchestration P4–P7 under owner shutdown-session + WE stop owner-session. **P8/P9 / full P10 / production readiness не заявлять**. **49.C–E не начаты**. TASK-49.A повторно **не** закрывать.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | `shutdown_orchestration` P4→P7; WE lifecycle-owned stop session; regressions |
| Подключено | drain API module (не полный `run_ptb_lifecycle` P8–P10) |
| Выпущено | **нет** |

---

## Graph survey (49.B)

| Вопрос | Ответ |
|--------|-------|
| Application create | Caller builds Application; `run_ptb_lifecycle` initialize/start |
| Producer host install | **Must** be before `initialize()` (TASK-49.A); orchestration requires bound host on session |
| Producer sources | Antares intake + wrapped `process_update` / `Application.create_task`; raw `asyncio.create_task` unsupported |
| Supported mode | Pass when host pre-installed + Antares queue + issuer binding + post-COMPLETE entry refuse |
| Ownership | `ShutdownSession` (49.S) = orchestration owner; WE stop = process-local owner Task; registry wait = TASK-43 API |
| Q-HLP1 | **Separate module** `modules.antares.shutdown_orchestration` — does not duplicate ShutdownSession; lifecycle helper remains shell until later full wire |

### Known entry-path gap (not closed in 49.B)

- `apps/antares.py` / default sandbox build still use default `asyncio.Queue` — **not** `AntaresUpdateIntakeQueue`; Q-PTB1 host cannot attest on that graph until build injects Antares intake + pre-initialize install.
- `run_ptb_lifecycle` still seals → `_await_cleanup` after `stop.wait()` and does **not** yet call `run_owner_drain_p4_to_p7` (partial boundary: drain API exists; full helper wire deferred).
- Do **not** pass invented `producers_complete=True` into WE/registry on the unwired entry path.

### Wiring conditions retained

1. Single producer-host install before initialize/start  
2. Antares intake + supported PTB graph  
3. No untracked background producers  
4. Application/issuer binding  
5. Permanent entry refuse after COMPLETE  

---

## Delivered

- `run_owner_drain_p4_to_p7`: P4 attestation → P5 Accepted/continuation/WE unfinished==0 → P6 WE owner-session → P7 registry (order fixed).
- WE `stop_isolated_profile_workers`: one owner; waiter cancel ≠ owner cancel; no second sentinel; dead-before-sentinel ≠ success.
- Partial boundary: P7 success ≠ SESSION_TERMINAL / full shutdown; P8/P9 not wired.
- Tests: live producer blocks drain; unfinished blocks WE stop; cancel/repeat WE session; Accepted survives seal+waiter cancel; concurrent WE waiters.

---

## Success Criteria

- [x] P5–P7 order + real APIs
- [x] WE owner-session semantics
- [x] Q-HLP1 module layout
- [x] Cursor pytest (see provenance)
- [ ] GPT review
- [ ] merge/deploy
- [ ] 49.C sender wiring

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10** @ Test SHA `0867b48c52be1229782386ce0e4ae543aaae449b`: orchestration+WE **19**; related 40/41/43/49A/49S = **102** (do not sum) |
| GPT | review pending |

---

## Out Of Scope

49.C–E; sender/executor shutdown; Ready/merge/retarget/deploy; full shutdown claim; closing open gates outside P5–P7; re-close 49.A/49.S.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: P5–P7 + WE owner-session; Draft PR #55; Test SHA `0867b48…` |
