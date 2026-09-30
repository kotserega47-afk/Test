# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49S |
| **Статус** | review (GPT CHANGES REQUESTED → правки; merge/deploy не выполнены; 49.S **не** закрыт) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49 close `e331c677e15ae765db5eb97fe686ec91679c0a6d` (accepted docs `237b20efeb2aed76d24f620a9a1cc2110145344c`, Draft PR #52); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft [#53](https://github.com/deniskotdavydov1991-wq/Test/pull/53) `feat/task-2026-09-17-49s-antares-shutdown-session`, base `docs/task-2026-09-17-49-antares-shutdown-orchestration` @ `e331c677…` |
| **Риск** | medium: deadline/snapshot truth; foreign-loop guards; cleanup cancel terminal; arm primary merge |

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

## Success Criteria

- [x] Prior 49.S deliverables
- [x] Review regressions for items 1–5
- [x] Unit + admission/lifecycle regressions
- [ ] GPT re-review
- [ ] merge/deploy (открыто)
- [ ] production wiring (future)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | см. отчёт PR после push (Python 3.12.10; наборы не суммировать) |
| GPT | ACCEPTED pending re-review |

---

## Out Of Scope

Production wiring; Q-PTB1; P3–P9; WE/registry/sender/executor stop; Ready/merge/retarget/deploy; live polling; requirements; 49.A–E auto-start; Q-REC1.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: shutdown-session primitive; Draft PR #53 |
| 2026-09-30 | GPT CHANGES REQUESTED: deadline/snapshot, loop guards, cleanup cancel, wait_until, arm primary |
