# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49C |
| **Статус** | review (CODE; Draft PR; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.B open @ `65afd735dfb305f445d3dcf10dfc3497a6392f31` (P4–P7 ACCEPTED; integration blocker); TASK-48 ACCEPTED `7f6b5a8…`; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) |
| **PR** | Draft base `feat/task-2026-09-17-49b-antares-orchestration-p5-p7` @ `65afd73…` |
| **Риск** | medium: false P8 before P7 / foreign proof / false full sender success |

CODE: P8 after successful P7 + explicit sender ownership proof plumbing. **P9 / production lifecycle wiring / SESSION_TERMINAL / full shutdown не заявлять**. **49.B не закрывать**. **49.D–E не начаты**.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | `run_owner_drain_p4_to_p8`; proof bind/identity; P8 remainder for EX1 |
| Подключено к `run_ptb_lifecycle` | **нет** (49.B integration blocker; обход P9 запрещён) |
| Выпущено | **нет** |

---

## Q-OWN1 (this slice)

**Closed in scope:** orchestrator receives proof as explicit `sender_ownership_proof=` (exact object identity from `claim_antares_sender_ownership` / `AntaresBootPrefix.sender_ownership`). Validated before side effects and before cached result.

**Deferred:** wiring proof from real entry/`_boot_prefix()` into lifecycle (still blocked by 49.B integration gap).

---

## Integration blocker (unchanged from 49.B)

1. Production entry uses default Queue.  
2. Producer host not installed pre-initialize.  
3. `run_ptb_lifecycle` does not call P4–P8.  

Required order: pre-init intake/host → session → P4–P7 → **P8** → P9 → cleanup.

---

## Delivered

- P8 only after successful P7; `stop_isolated_sender(proof, timeout=remaining)`.
- Remaining from `session.shutdown_deadline` only.
- After expiry: no new destructive P8; started P8 observed.
- Missing/foreign proof refuse without sender stop.
- One owner; proof identity on cache; partial vs full sender outcome; P8 ≠ SESSION_TERMINAL.

---

## Success Criteria

- [x] P8 after P7 + proof plumbing
- [x] Cursor pytest on verified runtime SHA
- [ ] GPT review
- [ ] lifecycle wiring
- [ ] merge/deploy
- [ ] 49.D

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | cwd `C:\Users\sereg\PycharmProjects\Test_antares_orchestration_49c`; clean runtime tree @ **Test SHA** `00e43a4db1e4e9b2927d9f719b8223d53f09c306`; Python **3.12.10** |
| Commands | `py -3.12 -m pytest tests/unit/test_antares_shutdown_orchestration_49c.py -q --tb=line` → **7 passed**; related 49b+40/41/43/49A/49S → **120 passed**; isolated `tests/unit/test_antares_sender_ownership.py` → **10 passed** (do not sum; O9 requires isolation from telegram_bot import) |
| GPT | pending |

---

## Out Of Scope

P9/executor; docs-close 49.B/49.C; Ready/merge/retarget/deploy; lifecycle wire bypassing P9; 49.D–E.

---

## История

| Дата | Событие |
|------|---------|
| 2026-10-01 | CODE: P8 + proof plumbing; Test SHA `00e43a4…` (7/120 + ownership 10); Draft PR base 49.B |
