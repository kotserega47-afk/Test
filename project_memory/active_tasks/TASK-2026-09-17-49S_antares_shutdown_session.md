# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49S |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49 close `e331c677e15ae765db5eb97fe686ec91679c0a6d` (accepted docs `237b20efeb2aed76d24f620a9a1cc2110145344c`, Draft PR #52); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft (этот PR) `feat/task-2026-09-17-49s-antares-shutdown-session`, base `docs/task-2026-09-17-49-antares-shutdown-orchestration` @ `e331c677…` |
| **Риск** | medium: owner-session semantics; false overall_ok; premature cleanup without Q-PTB1 |

CODE: owner shutdown-session primitive. **Production wiring отсутствует** (`run_ptb_lifecycle` / `request_antares_stop` callers / boot / handlers **не** подключены). WE/registry/sender/executor этим PR **не** останавливаются. Полный graceful Antares **не** завершён. Q-PTB1 **открыт** и блокирует зависимое подключение full cleanup. 49.A–E **не** автостарт. TASK-39–49 повторно не закрывать. 49.S **не** закрывать до GPT review.

---

## Goal

Реализовать `modules.antares.shutdown_session`: одна owner-session на lifecycle host, arm request-stop / cancel-after-OPEN / startup-failure, snapshot vs SESSION_TERMINAL, cleanup observer + observe budget, без wiring.

---

## Delivered

| Piece | Location |
|-------|----------|
| Host + session | `modules/antares/shutdown_session.py` |
| Arm APIs | `arm_request_stop` / `arm_cancel_after_open` / `arm_startup_failure` |
| Cleanup gate | `ProducersCompleteAttestation` + `accept_producers_complete` / `start_cleanup` |
| Controllable clock | `ControllableClock` for deterministic deadlines |
| Tests | `tests/unit/test_antares_shutdown_session.py` |

### Ownership / Tasks

- `ShutdownSessionHost` owned by future lifecycle context (no global completed-session registry).
- First arm creates one owner `asyncio.Task`; repeats join the same session.
- Strong refs: `session.owner_task`, `session.cleanup_task`.
- Host documents: caller must keep owner loop alive; primitive does not close/start loops or promise process exit.

### Semantics (this slice)

- Post-OPEN arm: `seal()` → `stop.set()` on owner loop; `had_open` required (SEALED alone insufficient).
- Startup failure: separate path; no post-OPEN drain; staged cleanup via `start_cleanup` without producers attestation.
- Waiter cancel ≠ owner/cleanup cancel; does not cancel Accepted/continuation (not touched here).
- Drain deadline ≠ cleanup observe deadline; observe exceeded → snapshot only; SESSION_TERMINAL only after cleanup Task finishes.
- `overall_ok` never True without mandatory phase results (None on clean terminal; False on cleanup error).
- After SESSION_TERMINAL: repeat returns stored result; no new deadline; no recovery (Q-REC1).

---

## Success Criteria

- [x] One session / one owner Task; concurrent arm join
- [x] Deadline fixed once
- [x] Cancel waiter isolation
- [x] Cancel-after-OPEN seal→set; startup ≠ post-OPEN
- [x] Foreign loop refused before side effects
- [x] Cleanup once; observe timeout snapshot; late terminal; cleanup error
- [x] No recovery after terminal; no false overall_ok
- [x] Unit tests + admission/lifecycle regressions
- [ ] GPT review
- [ ] merge/deploy (открыто)
- [ ] production wiring (future slices)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10**; `tests/unit/test_antares_shutdown_session.py` + `tests/test_antares_accepted_executor_work.py` + `tests/unit/test_antares_work_admission.py` + `tests/unit/test_antares_lifecycle.py` (+ lifecycle helper smoke) — see PR report SHA |
| GPT | pending review |

---

## Out Of Scope

Production wiring; Q-PTB1 implementation; P3–P9 phases; WE/registry/sender/executor stop; Ready/merge/retarget/deploy; live polling; requirements; mixed behavior; 49.A–E auto-start; Q-REC1 recovery.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: shutdown-session primitive; Draft PR; ожидание GPT review |
