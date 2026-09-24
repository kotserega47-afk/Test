# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-46 |
| **Статус** | review (подготовлено; docs-close **не** выполнен) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-45 close `873c0a91397ff3ba79938907aa2ff6384ed136cd` (accepted review `a3b95990b09a52848498d7518d2bc234a8f19800`, Draft PR #48); [MODULAR_REORG_ANTARES_SENDER_GATES.md](../ops/MODULAR_REORG_ANTARES_SENDER_GATES.md); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md) |
| **PR** | Draft [#49](https://github.com/deniskotdavydov1991-wq/Test/pull/49) `feat/task-2026-09-17-46-antares-sender-gates-runtime`, base `873c0a9…` |
| **Риск** | medium: wrong ownership identity / PTB graph guess would undermine future sender stop |

Реализация **runtime foundation** принятых TASK-45 gate primitives. **Не** full sender drain/stop. **Не** docs-close до GPT review. Merge/deploy/helper wiring нет.

Контракт TASK-45 **не** переинтерпретирован: Ownership **C+D**; PTB = runtime capability gate / fail-closed.

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
| Tests | `tests/unit/test_antares_sender_ownership.py` (O1–O10); `tests/unit/test_telegram_sender_gates.py` (P1–P12); boot harness claim order |

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
- Recognized graph = `bot._request` tuple pair; public-only `bot.request` → fail-closed
- Never calls shutdown / initialize / queue mutation

Fail-closed reasons: `sender_bot_unavailable`, `bot_shutdown_unavailable`, `request_graph_unavailable`, `request_graph_ambiguous`, `general_request_mismatch`, `request_shutdown_unavailable`, `leftover_diagnostic_unavailable`.

---

## Success Criteria

- [x] Claim side-effect-free; only after successful isolated profile enforce
- [x] Same-process claim immutable/idempotent; exact identity validation
- [x] Mixed/env/admission do not create proof
- [x] `apps.antares` preserves proof for future holder
- [x] PTB inspection read-only; Bot None fail-closed; full graph + leftover diagnostic
- [x] `integrations.telegram_bot` delivery runtime unchanged
- [x] Unit/boot regressions PASS (Cursor)
- [ ] GPT review
- [ ] docs-close (после accept)
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

`stop_isolated_sender`; intake seal; S1–S3 mutation; worker sentinel; Bot/request close; loop.stop; thread.join; lifecycle STOPPED fast-path runtime; helper wiring; requirements pin; mixed-stop; deploy/live Telegram; TASK-47.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | CODE: ownership + PTB inspector + boot claim wiring; Draft PR #49; ожидание GPT review |
| 2026-09-24 | GPT review BLOCKED: (1) PR base=`test_main` — **не** retarget без явного разрешения; (2) tuple `len>=2` → fix EXACTLY-2; (3) raising property probes → fail-closed |
