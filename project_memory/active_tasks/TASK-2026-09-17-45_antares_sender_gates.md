# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-45 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-44 close `49193bb0d5353d3c528b5a6a382cf5ac22ceb718` (accepted `0e770a38eac570585ba698b30a39ef7162fe10b5`, Draft PR #47); [MODULAR_REORG_ANTARES_SENDER_GATES.md](../ops/MODULAR_REORG_ANTARES_SENDER_GATES.md); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md) |
| **PR** | Draft [#48](https://github.com/deniskotdavydov1991-wq/Test/pull/48) `docs/task-2026-09-17-45-antares-sender-gates`, base `docs/task-2026-09-17-44-antares-sender-drain-stop` |
| **Риск** | medium: wrong ownership proof spoofs shared sender stop; PTB private API misuse |

Docs-only: закрыть ownership-gate shape и PTB compatibility strategy до sender runtime. Runtime **не** менять. Helper не wire. O10 не закрывать. TASK-39–44 повторно не закрывать. Docs-close пока **не** делать.

---

## Goal

Выбрать и зафиксировать:

1. **Ownership:** C+D — immutable process-local attestation after `apps.antares` enforce, passed into stop API; refuse without it.
2. **PTB:** B — runtime capability gate + fail-closed; claimed stop requires sender Bot present (`bot is None` = fail-closed preflight; **no** NO_TOKEN no-op); not version-string-only; pin not required by this contract.
3. Preflight-before-mutation; G1–G15.

---

## Success Criteria

- [x] Survey boot/profile/sender import paths on `49193bb…`
- [x] Ownership alternatives A–D compared; **C+D chosen**; A/B/D-alone rejected
- [x] Claim creation/validation lifecycle; immutable CLAIMED; mixed refuse
- [x] PTB strategy **B** chosen; public vs private; full graph preflight vs resource close
- [x] Claimed isolated stop requires sender Bot present; Bot None = fail-closed (no NO_TOKEN no-op left to code)
- [x] Preflight order; stop API semantics; G1–G15 (G9 includes Bot absent)
- [ ] GPT review
- [ ] runtime (отдельный future slice; не автостарт)
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

Sender implementation; ownership/PTB runtime; `requirements.txt`; executor shutdown; helper; serve; mixed-stop; merge/deploy; live Telegram; повторное закрытие TASK-39–44.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | docs-контракт sender ownership + PTB gates; статус **review (подготовлено)** |
| 2026-09-24 | review blocker: Bot None / NO_TOKEN → fail-closed preflight (не no-op) |
