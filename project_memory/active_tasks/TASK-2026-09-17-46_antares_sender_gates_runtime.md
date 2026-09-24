# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-46 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-45 close `873c0a91397ff3ba79938907aa2ff6384ed136cd` (accepted review `a3b95990b09a52848498d7518d2bc234a8f19800`, Draft PR #48); [MODULAR_REORG_ANTARES_SENDER_GATES.md](../ops/MODULAR_REORG_ANTARES_SENDER_GATES.md); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md) |
| **PR** | Draft [#49](https://github.com/deniskotdavydov1991-wq/Test/pull/49) `feat/task-2026-09-17-46-antares-sender-gates-runtime`, base `docs/task-2026-09-17-45-antares-sender-gates` @ `873c0a9…` |
| **Риск** | medium: wrong ownership identity / PTB graph guess would undermine future sender stop |

Review **пройден**. GPT accepted runtime HEAD `0d80bb291114344d13bc3064fa9d4d76e95c7221` (ACCEPTED). Cursor pytest: **25** / **64** / **19** / **14**. GPT pytest **не** запускал независимо. GitHub Actions на accepted HEAD — нет. Этот docs-коммит — закрытие TASK-46.

Runtime foundation ownership + read-only PTB gates **принят**, **не выпущен**. PR #49 остаётся Draft/open на correct layered base после authorized retarget (HEAD при retarget не менялся). Merge/deploy/helper wiring нет. O10 открыт. TASK-39–45 повторно не закрывать. TASK-47 / sender-stop slice — **не** автостарт.

Контракт TASK-45 **не** переинтерпретирован: Ownership **C+D**; PTB = runtime capability gate / fail-closed.

---

## Accepted runtime

| Поле | Значение |
|------|----------|
| Accepted runtime HEAD | `0d80bb291114344d13bc3064fa9d4d76e95c7221` |
| GPT verdict | **ACCEPTED** |
| GPT pytest | not independently run |
| Cursor tests | **25** ownership+gates; **64** boot+profile; **19** lifecycle; **14** sender health+transport |
| PR #49 | Draft/open; base `docs/task-2026-09-17-45-antares-sender-gates` @ `873c0a9…` after authorized retarget |
| Retarget | authorized by user and completed; HEAD unchanged |

---

## Goal

Реализовать immutable Antares process ownership claim + wiring в isolated Antares boot + read-only PTB compatibility/request-graph inspector, чтобы следующий sender-stop slice **не** выбирал архитектуру заново.

---

## Delivered

| Piece | Location |
|-------|----------|
| Ownership claim/validate | `core/antares_sender_ownership.py` |
| Boot claim + proof holder | `apps/antares.py` → `AntaresBootPrefix.sender_ownership` |
| PTB inspector (read-only) | `integrations/telegram_sender_gates.py` |
| Tests | `tests/unit/test_antares_sender_ownership.py` (O1–O10); `tests/unit/test_telegram_sender_gates.py` (P1–P12+blockers); boot harness claim order |

### Ownership API

- `AntaresSenderOwnershipAttestation` — frozen; exact `is` identity = proof
- `claim_antares_sender_ownership()` — UNCLAIMED→CLAIMED_ANTARES; idempotent same object; corrupted state → `AntaresSenderOwnershipError` (no replace)
- `validate_antares_sender_ownership(proof)` → `AntaresSenderOwnershipValidation(ok, reason)`
- `_reset_antares_sender_ownership_for_tests()` — tests only

### Boot order

`enforce_antares_isolated_profile()` → `claim_antares_sender_ownership()` → dotenv / assemble / Application.

Proof stored on `AntaresBootPrefix.sender_ownership` for future lifecycle holder. No ambient `get_current_proof` getter. Claim does **not** import `integrations.telegram_bot`.

### PTB inspector

`inspect_sender_ptb_compatibility(*, bot, expected_general_request) → SenderPtbCompatibilityResult`

- `supported` + explicit `reason` on REFUSE
- `roles` (get_updates_request / request) + `close_targets` (deduped by object identity)
- Recognized graph = `bot._request` tuple **EXACTLY** length 2; public-only / longer tuple / contradiction → fail-closed
- Raising property probes → fail-closed (no crash)
- Never calls shutdown / initialize / queue mutation

Fail-closed reasons: `sender_bot_unavailable`, `bot_shutdown_unavailable`, `request_graph_unavailable`, `request_graph_ambiguous`, `general_request_mismatch`, `request_shutdown_unavailable`, `leftover_diagnostic_unavailable`.

---

## Success Criteria

- [x] Claim side-effect-free; only after successful isolated profile enforce
- [x] Same-process claim immutable/idempotent; exact identity validation
- [x] Mixed/env/admission do not create proof
- [x] `apps.antares` preserves proof for future holder
- [x] PTB inspection read-only; Bot None fail-closed; EXACTLY-2 graph + leftover diagnostic
- [x] `integrations.telegram_bot` delivery runtime unchanged
- [x] Unit/boot/lifecycle regressions PASS (Cursor 25/64/19/14)
- [x] GPT review ACCEPTED на `0d80bb2…`; GPT pytest не запускал
- [x] docs-close (этот коммит)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | runtime на base `873c0a9…`; pytest 25/64/19/14 (3.12.10) |
| GPT | review PR #49 / diff на `0d80bb2…`; ACCEPTED; pytest **не** запускал независимо; retarget base authorized/completed |

---

## Out Of Scope

`stop_isolated_sender`; intake seal; S1–S3 mutation; worker sentinel; Bot/request close; loop.stop; thread.join; lifecycle STOPPED fast-path runtime; helper wiring; requirements pin; mixed-stop; deploy/live Telegram; TASK-47.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | CODE: ownership + PTB inspector + boot claim wiring; Draft PR #49; ожидание GPT review |
| 2026-09-24 | GPT review BLOCKED: (1) PR base=`test_main` — **не** retarget без явного разрешения; (2) tuple `len>=2` → fix EXACTLY-2; (3) raising property probes → fail-closed |
| 2026-09-25 | GPT ACCEPTED `0d80bb2…`; authorized retarget base → `docs/task-2026-09-17-45-antares-sender-gates`; docs-close; runtime unchanged since accepted HEAD |
