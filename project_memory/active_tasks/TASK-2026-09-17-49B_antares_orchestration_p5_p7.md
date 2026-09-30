# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49B |
| **Статус** | open (implemented scope **GPT ACCEPTED**; integration blocker; Draft PR; **не** docs-closed; merge/deploy нет) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.A ACCEPTED/docs-close `e80aa6cdecc681a0e94e7fbbbebd17302f668496`; TASK-40/41/43; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft [#55](https://github.com/deniskotdavydov1991-wq/Test/pull/55) `feat/task-2026-09-17-49b-antares-orchestration-p5-p7`, base `feat/task-2026-09-17-49a-antares-producer-wait` @ `e80aa6c…` |
| **Риск** | medium: false producers_complete / WE partial / registry freeze before WE |

**GPT ACCEPTED** для реализованного объёма: P4–P7 API / session-bound owner / WE owner-session. Reviewed HEAD `e02314ad38391fc5122b93a6b95db41b88ef9f5e`. Verified Test SHA `5b62d6bf31ed219cc5fac64b33b162a0a46eb999`.

**TASK-49.B остаётся открытой** из‑за integration blocker (lifecycle не подключён). Docs-close всей 49.B **не** выполнять. **P8/P9 / full P10 / production readiness не заявлять**. **49.C–E не начаты**. TASK-49.A повторно **не** закрывать.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | session-bound P4–P7 owner; WE create/join; deadline waiter partial; public progress snapshot — **GPT ACCEPTED** |
| Подключено к `run_ptb_lifecycle` | **нет** — **integration blocker** (см. ниже) |
| Выпущено | **нет** |

---

## Integration blocker (keeps 49.B open)

1. Production entry (`apps/antares.py`) uses **default Queue** (not `AntaresUpdateIntakeQueue`).
2. Producer host is **not** installed before `Application.initialize()`.
3. `run_ptb_lifecycle` does **not** call P4–P7 (`run_owner_drain_p4_to_p7`); still seals → `_await_cleanup`.

**Required future wire order** (do not bypass):

```
pre-init Antares intake + producer host
→ arm ShutdownSession
→ P4–P7 owner
→ P8 sender (49.C) → P9 executor (49.D)
→ only then PTB cleanup / SESSION_TERMINAL
```

Inserting P4–P7 then jumping to today’s cleanup **bypasses P8/P9**. Unsafe path is **not** enabled. Lifecycle wiring is **not** marked done.

---

## Accepted scope (implemented)

- One P4–P7 owner Task per `ShutdownSession`; join/observe; cancel waiter ≠ phase interrupt.
- Deadline snapshot ≠ owner completion; public `observe_drain_orchestration`.
- WE create vs join; identity before cached result; owner exception after detach.
- Partial boundary: P7 success ≠ SESSION_TERMINAL / full shutdown.

---

## Success Criteria

- [x] P5–P7 API + session-bound owner + WE owner-session (GPT ACCEPTED)
- [x] Cursor pytest on verified runtime SHA (see provenance)
- [x] GPT code/diff review (pytest not run by GPT)
- [ ] lifecycle wiring (blocked — integration blocker)
- [ ] docs-close of entire TASK-49.B
- [ ] merge/deploy
- [ ] 49.C sender wiring

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | cwd `Test_antares_orchestration_49b`; Python **3.12.10**; Test SHA `5b62d6bf31ed219cc5fac64b33b162a0a46eb999`: orchestration **18**; related **113** (do not sum) |
| GPT | **ACCEPTED** reviewed HEAD `e02314ad38391fc5122b93a6b95db41b88ef9f5e` (code/diff); pytest **not** run; CI PASS **not** claimed |

---

## Out Of Scope

Docs-close всей 49.B; lifecycle wiring; Ready/merge/retarget/deploy; 49.C–E without separate task; full shutdown claim; re-close 49.A/49.S.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: P5–P7 + WE owner-session; Draft PR #55 |
| 2026-09-30 | Review-fix: session-bound owner + WE create/join + design blocker |
| 2026-09-30 | Review-fix: deadline snapshot ≠ owner terminal; Test SHA `5b62d6b…` (18/113) |
| 2026-10-01 | GPT **ACCEPTED** implemented scope @ `e02314a…` / Test `5b62d6b…`; 49.B stays **open** (integration blocker); no docs-close |
