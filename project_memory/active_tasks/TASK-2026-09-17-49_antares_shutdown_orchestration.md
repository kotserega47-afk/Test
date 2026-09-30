# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49 |
| **Статус** | review (docs; GPT CHANGES REQUESTED → failure/cleanup/repeat уточнения; merge/deploy нет) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-48 close `90cda7c92e56df3657c293f2e6de6ee65d2426c0` (accepted runtime `7f6b5a8c211658fba92e2f6b98320b3443935cb6`, Draft PR #51); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md); [REGISTRY_DAEMON.md](../ops/MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft [#52](https://github.com/deniskotdavydov1991-wq/Test/pull/52) `docs/task-2026-09-17-49-antares-shutdown-orchestration`, base `feat/task-2026-09-17-48-antares-sender-full-stop` @ `90cda7c…` |
| **Риск** | high: cleanup observer vs terminal; P10 gate vs live producers; repeat after SESSION_TERMINAL; Q-PTB1; O10 открыт |

CODE: **нет**. Docs-only. Runtime/`tests`/`requirements` не менять. TASK-49 **не** закрывать до GPT accept. TASK-39–48 повторно не закрывать. Code slices не автостарт. Следующий code указатель: **49.S → 49.A–E**.

Обследованный SHA: `90cda7c…` (worker stop API в том же дереве).

---

## Goal

Контракт общей остановки isolated Antares: owner shutdown-session, snapshot vs SESSION_TERMINAL, P10 gate на `producers_complete`, cleanup observe budget, repeat без нового deadline после terminal.

---

## Success Criteria

- [x] Таблица фаз + owner-session model
- [x] Cancel after OPEN → P3–P9; Accepted не cancel
- [x] Cleanup observer: snapshot ≠ terminal; hung ≠ stopped; no SESSION_TERMINAL while cleanup Task alive
- [x] Full P10 только с producers proof; deadline ≠ proof; Application HTTP open; Orc17/22
- [x] Repeat: join / read terminal; no new deadline; no new destructive session; Orc7 = TASK-48 sender API; Orc21
- [x] EX1; WE partial ≠ success-only; Q-PTB1 blocks wiring; 49.S first in MIGRATION
- [ ] GPT re-review
- [ ] runtime/helper wiring (future)
- [ ] merge/deploy (открыто)

---

## Out Of Scope

Runtime/tests/requirements; helper wiring; serve/polling; O10; merge/Ready/deploy; Move agent root; исходное Test; изменение TASK-41 runtime; bounded process exit; Q-REC1 recovery.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | docs-контракт; Draft PR #52 |
| 2026-09-30 | GPT review: cancel-after-OPEN, deadline/P10, WE partial, plan sync |
| 2026-09-30 | GPT review: cleanup snapshot/terminal, P10 vs live producers, repeat after terminal, 49.S in MIGRATION |
