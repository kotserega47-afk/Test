# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49 |
| **Статус** | review (подготовлено; GPT CHANGES REQUESTED → правки docs; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-48 close `90cda7c92e56df3657c293f2e6de6ee65d2426c0` (accepted runtime `7f6b5a8c211658fba92e2f6b98320b3443935cb6`, Draft PR #51); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md); [REGISTRY_DAEMON.md](../ops/MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft [#52](https://github.com/deniskotdavydov1991-wq/Test/pull/52) `docs/task-2026-09-17-49-antares-shutdown-orchestration`, base `feat/task-2026-09-17-48-antares-sender-full-stop` @ `90cda7c…` |
| **Риск** | high: post-OPEN cancel; shutdown deadline vs shielded cleanup; WE partial owner-session; Q-PTB1 gate; O10 открыт |

CODE: **нет**. Docs-only. Runtime/`tests`/`requirements` не менять. TASK-49 **не** закрывать до GPT accept. TASK-39–48 повторно не закрывать. Code slices не автостарт.

Обследованный SHA: `90cda7c…` (worker stop API в том же дереве).

---

## Goal

Контракт общей остановки isolated Antares со стыком `run_ptb_lifecycle`: owner shutdown-session, post-OPEN cancel ≠ cleanup-only, deadline/P10 policy, WE partial semantics, EX1, Q-PTB1 gate.

---

## Success Criteria

- [x] Таблица фаз + owner-session model
- [x] Cancel after OPEN → P3–P9, не прямой P10; Accepted не cancel
- [x] Shutdown deadline start; P10 vs shielded `_await_cleanup`; EX1 closed
- [x] WE: sentinel/stop_done facts; owner-session choice; current API ≠ partial retry
- [x] Orc16–Orc20; slices 49.S first; Q-PTB1 blocks wiring; Q-OWN1 narrowed to proof plumbing
- [ ] GPT re-review
- [ ] runtime/helper wiring (future)
- [ ] merge/deploy (открыто)

---

## Out Of Scope

Runtime/tests/requirements; helper wiring; serve/polling; O10; merge/Ready/deploy; Move agent root; исходное Test; изменение TASK-41 runtime в этом PR.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | docs-контракт; Draft PR #52 |
| 2026-09-30 | GPT CHANGES REQUESTED: cancel-after-OPEN, deadline/P10, WE partial, plan sync |
