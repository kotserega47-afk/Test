# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49S |
| **Статус** | review (пройден; **ACCEPTED** / **DOCS-CLOSED**; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49 close `e331c677e15ae765db5eb97fe686ec91679c0a6d` (accepted docs `237b20efeb2aed76d24f620a9a1cc2110145344c`, Draft PR #52); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft [#53](https://github.com/deniskotdavydov1991-wq/Test/pull/53) `feat/task-2026-09-17-49s-antares-shutdown-session`, base `docs/task-2026-09-17-49-antares-shutdown-orchestration` @ `e331c677…` |
| **Риск** | medium: read-only snapshot; remainder truth; frozen task-alive-at-publish |

Review **пройден**. GPT **ACCEPTED** review HEAD `f7dd672b879eab847d6a9143be24e0b474ff3d37` (код/diff/regression-тесты; pytest GPT **не** запускал). Cursor pytest Python **3.12.10** на Test SHA `bab805868e37a977b1303774478bc64265d1936d`: shutdown_session **33**; + accepted_executor_work + work_admission **132**; lifecycle unit **19** (наборы **не** суммировать). CI PASS **не** заявлять. Этот docs-коммит — **DOCS-CLOSED** TASK-49.S. Runtime между accepted HEAD и close **не** менялся (docs-only).

Owner shutdown-session primitive **принят**, **не выпущен**. PR #53 остаётся Draft/open. Production wiring в `run_ptb_lifecycle` / stop callers **нет**. Полный shutdown Antares **не** реализован. Q-PTB1 и прочие orchestration gates **открыты**. **49.A** — следующий запланированный этап (**не** начат). 49.B–E **не** автостарт. Merge/Ready/retarget/deploy **нет**. TASK-39–49 повторно **не** закрывать.

---

## Accepted runtime

| Поле | Значение |
|------|----------|
| Accepted review HEAD | `f7dd672b879eab847d6a9143be24e0b474ff3d37` |
| Test SHA (Cursor pytest) | `bab805868e37a977b1303774478bc64265d1936d` |
| GPT verdict | **ACCEPTED** |
| GPT pytest | not run (code/diff/regression tests reviewed) |
| Cursor tests | **33** / **132** / **19** (Python 3.12.10; sets not summed) |
| PR #53 | Draft/open; base TASK-49 docs @ `e331c677…` |
| Module | `modules.antares.shutdown_session` |
| Production wiring | **none** |

---

## Goal

`modules.antares.shutdown_session`: owner-session host, arm paths, snapshot/terminal, cleanup observer — без wiring.

---

## Delivered (accepted)

- Lifecycle-owned `ShutdownSessionHost` / `ShutdownSession` (arm request-stop, cancel-after-OPEN, startup failure).
- Read-only `snapshot()` / host `snapshot()` (no mutation, no Event publish); drain permission from current clock.
- Terminal remainder via `_compute_remainder()` + durable `_error_tags`.
- `owner_task_alive_at_publish` / `cleanup_task_alive_at_publish` frozen at publish (actual Task liveness); `current_task_liveness()` for current state.
- ControllableClock drain/cleanup-observe budgets; Q-PTB1 attestation gate for post-OPEN cleanup.

---

## Success Criteria

- [x] Prior 49.S deliverables
- [x] Review regressions (deadline/loop/cleanup cancel/wait_until/primary)
- [x] Review regressions (read-only snapshot / remainder / task liveness)
- [x] Unit + admission/lifecycle regressions (Cursor 33/132/19 @ `bab80586…`)
- [x] GPT review **ACCEPTED** @ `f7dd672…`; GPT pytest не запускал
- [x] docs-close (этот коммит)
- [ ] merge/deploy (намеренно открыто)
- [ ] production wiring (future; **не** этот PR)
- [ ] 49.A Q-PTB1 (следующий этап; **не** начат)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| GPT | review кода, diff и regression-тестов на accepted HEAD `f7dd672b879eab847d6a9143be24e0b474ff3d37`; **pytest не запускался** |
| Cursor | Python **3.12.10** @ Test SHA `bab805868e37a977b1303774478bc64265d1936d`: shutdown_session **33**; + accepted_executor_work + work_admission = **132**; lifecycle unit **19** (наборы не суммировать); CI PASS не заявлять |

---

## Out Of Scope

Production wiring; full Antares shutdown; Q-PTB1 implementation; P3–P9; WE/registry/sender/executor stop via this primitive; Ready/merge/retarget/deploy; live polling; requirements; 49.A–E auto-start; Q-REC1; повторное закрытие TASK-39–49.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: shutdown-session primitive; Draft PR #53 |
| 2026-09-30 | GPT CHANGES REQUESTED: deadline/snapshot, loop guards, cleanup cancel, wait_until, arm primary |
| 2026-09-30 | GPT CHANGES REQUESTED @ `42b45bf…`: read-only snapshot, true remainder, task_alive_at_publish |
| 2026-09-30 | Review-fix: read-only snapshot / `_compute_remainder` / `current_task_liveness` |
| 2026-09-30 | Fix: `cleanup_task_alive_at_publish` = actual Task liveness at `_set_terminal` (not constant False) |
| 2026-09-30 | GPT **ACCEPTED** `f7dd672…`; docs-close; Test SHA `bab80586…`; 49.A next (not started) |
