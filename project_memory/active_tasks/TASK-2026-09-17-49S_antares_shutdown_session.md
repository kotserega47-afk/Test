# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49S |
| **Статус** | review (GPT CHANGES REQUESTED → правки snapshot/remainder/liveness; merge/deploy не выполнены; 49.S **не** закрыт) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49 close `e331c677e15ae765db5eb97fe686ec91679c0a6d` (accepted docs `237b20efeb2aed76d24f620a9a1cc2110145344c`, Draft PR #52); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft [#53](https://github.com/deniskotdavydov1991-wq/Test/pull/53) `feat/task-2026-09-17-49s-antares-shutdown-session`, base `docs/task-2026-09-17-49-antares-shutdown-orchestration` @ `e331c677…` |
| **Риск** | medium: read-only snapshot; remainder truth; frozen task-alive-at-publish |

CODE: owner shutdown-session primitive. **Production wiring отсутствует**. WE/registry/sender/executor **не** останавливаются. Q-PTB1 **открыт** (не реализован / не закрыт). 49.A–E **не** автостарт. TASK-39–49 повторно не закрывать. 49.S **не** закрывать до GPT accept.

---

## Goal

`modules.antares.shutdown_session`: owner-session host, arm paths, snapshot/terminal, cleanup observer — без wiring.

---

## Review fixes (post `86d467b…`)

1. **Deadline / terminal snapshot** — `may_start_new_destructive_phases` uses monotonic deadline + terminal + cleanup-started; watcher not sole source; stored terminal snapshot is authoritative for later `snapshot()`.
2. **Owner loop / context** — mutating/async APIs require owner loop; admission/stop/loop identity frozen on host/session.
3. **Cleanup CancelledError** — owned cleanup abort/cancel reaches SESSION_TERMINAL with remainder; HTTP not claimed cleaned; owner does not hang.
4. **wait_until** — if already terminal and predicate false, return terminal snapshot (no hang).
5. **Repeat arm / primary** — compatible join keeps deadline; cancel/error after request-stop recorded as primary/`caller_causes`; waiter cancel ≠ session primary; incompatible startup/post-OPEN refused; startup seals bound admission and refuses actual OPEN.

---

## Review fixes (post `42b45bf…`)

1. **Read-only `snapshot()`** — no mutation / no Event publish; drain permission/`DRAIN_SNAPSHOT` derived from current clock; `_note_drain_expired` only on owner loop (watcher). `ShutdownSessionHost.snapshot()` delegates.
2. **Terminal remainder** — `_compute_remainder()` from proven current state + durable `_error_tags`; cleared leftovers not restated; `drain_deadline_exceeded` kept as historical diagnostic; cleanup error/cancel keeps `application_http_open`.
3. **Task liveness** — frozen fields renamed `owner_task_alive_at_publish` / `cleanup_task_alive_at_publish`; current liveness via `current_task_liveness()`; `snapshot()` and `terminal_result.snapshot` stay identical.

---

## Success Criteria

- [x] Prior 49.S deliverables
- [x] Review regressions for items 1–5 (post-86d467b)
- [x] Review regressions for snapshot/remainder/liveness (post-42b45bf)
- [x] Unit + admission/lifecycle regressions
- [ ] GPT re-review
- [ ] merge/deploy (открыто)
- [ ] production wiring (future)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10** @ `15ce6a8f74105bfda7c9f347536c0b07f016afc7`: shutdown_session **33**; + accepted_executor_work + work_admission = **132**; lifecycle unit **19** (наборы не суммировать) |
| GPT | re-review pending |

---

## Out Of Scope

Production wiring; Q-PTB1; P3–P9; WE/registry/sender/executor stop; Ready/merge/retarget/deploy; live polling; requirements; 49.A–E auto-start; Q-REC1.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: shutdown-session primitive; Draft PR #53 |
| 2026-09-30 | GPT CHANGES REQUESTED: deadline/snapshot, loop guards, cleanup cancel, wait_until, arm primary |
| 2026-09-30 | GPT CHANGES REQUESTED @ `42b45bf…`: read-only snapshot, true remainder, task_alive_at_publish |
| 2026-09-30 | Review-fix: read-only snapshot / `_compute_remainder` / `current_task_liveness` |
