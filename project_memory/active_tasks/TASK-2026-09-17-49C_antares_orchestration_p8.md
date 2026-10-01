# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49C |
| **Статус** | open (implemented scope GPT ACCEPTED; Draft PR; docs-close / merge не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.B open @ `65afd735dfb305f445d3dcf10dfc3497a6392f31` (P4–P7 ACCEPTED; integration blocker); TASK-48 ACCEPTED `7f6b5a8…`; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft [#56](https://github.com/deniskotdavydov1991-wq/Test/pull/56) `feat/task-2026-09-17-49c-antares-orchestration-p8`, base `feat/task-2026-09-17-49b-antares-orchestration-p5-p7` @ `65afd73…` |
| **Риск** | medium: false P8 before P7 / foreign proof / false full sender success / first-send sentinel after deadline |

**GPT ACCEPTED** for the **implemented** P8 + proof plumbing + phase-aware observe scope @ tip `476a98e…` / Test SHA `dd3c558…`. **TASK-49.C не закрыта целиком**: production lifecycle wiring / P9 / SESSION_TERMINAL / full shutdown **не** выполнены. **49.B остаётся OPEN** (integration blocker). **49.D–E не начаты**. Ready/merge/retarget/deploy **нет**.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | `run_owner_drain_p4_to_p8`; proof bind/identity; phase-aware `observe_started_sender_stop` (DRAINING без first-send sentinel); live_observe vs boundary vs final_refuse; structured `SenderPhaseSnapshot`; single owner / waiter detach / rejoin — **GPT ACCEPTED** |
| Подключено к `run_ptb_lifecycle` | **нет** (49.B integration blocker; обход P9 запрещён) |
| Выпущено | **нет** |

---

## Q-OWN1 (this slice)

**ACCEPTED in scope:** orchestrator receives proof as explicit `sender_ownership_proof=` (exact object identity from `claim_antares_sender_ownership` / `AntaresBootPrefix.sender_ownership`). Validated before side effects and before cached result.

**Deferred:** wiring proof from real entry/`_boot_prefix()` / `AntaresBootPrefix` into production lifecycle (still blocked by 49.B integration gap).

---

## Integration blocker (unchanged from 49.B)

1. Production entry uses default Queue.  
2. Producer host not installed pre-initialize.  
3. `run_ptb_lifecycle` does not call P4–P8.  

Required order: pre-init intake/host → session → P4–P7 → **P8** → P9 → cleanup.

---

## Accepted delivered scope

- P8 only after successful P7; initial `stop_isolated_sender(proof, timeout=remaining)`.
- Remaining from `session.shutdown_deadline` only; no new shutdown deadline / no second destructive stop after budget expiry.
- `sender_attempted` set immediately before the TASK-48 call (truthful mid-await snapshot).
- Live partial observation is **phase-aware** via `observe_started_sender_stop` (no new destructive actions after deadline).
- Boundary / live partial / final refusal distinctions; structured immutable `SenderPhaseSnapshot` for EX1/P9.
- One owner; waiter detach and rejoin; P8 ≠ SESSION_TERMINAL.

---

## Success Criteria

- [x] P8 after P7 + proof plumbing
- [x] Phase-aware live partial observe + structured diagnostics + regressions
- [x] Cursor pytest on verified runtime SHA
- [x] GPT ACCEPTED (implemented scope; pytest not run by GPT)
- [ ] lifecycle wiring
- [ ] docs-close / merge/deploy
- [ ] 49.D

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | cwd `C:\Users\sereg\PycharmProjects\Test_antares_orchestration_49c`; clean runtime tree @ **Test SHA** `dd3c558caeee24a7ddf9bba99745d7f6916e3eba`; Python **3.12.10** |
| Commands | `py -3.12 -m pytest tests/unit/test_antares_shutdown_orchestration_49c.py -q --tb=line` → **12 passed**; set A (49c+49b+sender_full_stop) → **66 passed**; related 49b+40/41/43/49A/49S → **113 passed**; isolated `tests/unit/test_antares_sender_ownership.py` → **10 passed** (do not sum; O9 requires isolation from telegram_bot import) |
| GPT | **ACCEPTED** implemented scope @ tip `476a98e36011716b3eebf3629a29e2d380009d23` (code/diff + regression review; **pytest not run**); CI PASS **not** claimed |

---

## Out Of Scope

P9/executor; docs-close 49.B/49.C; Ready/merge/retarget/deploy; lifecycle wire bypassing P9; 49.D–E; re-close 49.B.

---

## История

| Дата | Событие |
|------|---------|
| 2026-10-01 | CODE: P8 + proof plumbing; Test SHA `00e43a4…` (7/120 + ownership 10); Draft PR #56 base 49.B |
| 2026-10-01 | Review-fix: live_observe vs boundary/final_refuse; structured snapshot; real TASK-48 late-complete regression; Test SHA `2a44403…` (8/62/113 + ownership 10) |
| 2026-10-01 | Review-fix: phase-aware `observe_started_sender_stop`; expired-deadline + DRAINING/HTTP_STOPPING regressions; Test SHA `4be8c54…` (10/64/113 + ownership 10) |
| 2026-10-01 | Review-fix: DRAINING observe без first-send sentinel; terminal `unexpected_dead_worker`; Test SHA `dd3c558…` (12/66/113 + ownership 10) |
| 2026-10-01 | GPT **ACCEPTED** implemented scope @ tip `476a98e…` / Test `dd3c558…`; 49.C остаётся open (no lifecycle wire / no docs-close); 49.B blocker сохранён |
