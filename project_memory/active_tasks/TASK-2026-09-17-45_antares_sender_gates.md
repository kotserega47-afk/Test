# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-45 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-44 close `49193bb0d5353d3c528b5a6a382cf5ac22ceb718` (accepted `0e770a38eac570585ba698b30a39ef7162fe10b5`, Draft PR #47); [MODULAR_REORG_ANTARES_SENDER_GATES.md](../ops/MODULAR_REORG_ANTARES_SENDER_GATES.md); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md) |
| **PR** | Draft [#48](https://github.com/deniskotdavydov1991-wq/Test/pull/48) `docs/task-2026-09-17-45-antares-sender-gates`, base `docs/task-2026-09-17-44-antares-sender-drain-stop` |
| **Риск** | medium: wrong ownership proof spoofs shared sender stop; PTB private API misuse |

Review **пройден**. GPT проверил PR #48 / контракт / карточку / diff на полном SHA `a3b95990b09a52848498d7518d2bc234a8f19800`. Runtime changes отсутствуют (только `project_memory/**`). GPT pytest **не** запускал / not applicable (docs-only). Этот docs-коммит — закрытие TASK-45.

Контракт ownership + PTB gates **принят**, **не выпущен**. Sender runtime / ownership claim runtime / PTB probe — отдельная будущая задача (**не** автостарт). PR #48 остаётся Draft. Merge/deploy/retarget/helper wiring нет. O10 открыт. TASK-39–44 повторно не закрывать.

## Accepted decisions (closed)

**Ownership C+D:** successful isolated Antares profile enforce → side-effect-free immutable process claim (must **not** import/start `integrations.telegram_bot`) → exact proof passed to sender stop. Env / `WorkAdmission.seal` ≠ proof. Mixed/UNCLAIMED → REFUSE before mutation.

**Lifecycle decision:** ownership first → read lifecycle → if STOPPED: same proof → idempotent success; foreign/no proof → refuse → if NOT STOPPED: PTB capability preflight → structural preflight → mutation.

**PTB:** runtime capability gate + fail-closed; version string alone не gate; `requirements.txt` не менялся.

**Bot unavailable:** fail-closed before mutation.

**O10:** remains open.

---

## Goal

Выбрать и зафиксировать ownership C+D, PTB strategy B, STOPPED fast-path, Bot None fail-closed, claim side-effect-free path, G1–G15 — **до** sender runtime.

---

## Success Criteria

- [x] Survey boot/profile/sender import paths on `49193bb…`
- [x] Ownership **C+D**; claim immutable; side-effect-free claim path
- [x] PTB strategy **B**; Bot None fail-closed; full request graph preflight
- [x] STOPPED fast-path; G13/G14; G1–G15
- [x] GPT review на `a3b9599…`; pytest GPT не запускал (docs-only)
- [ ] runtime (отдельная future задача; не этот close)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | docs-only на base `49193bb…`; runtime нет |
| GPT | review PR #48 / контракт / diff на `a3b9599…`; pytest **не** запускал / not applicable |

---

## Out Of Scope

Sender implementation; ownership/PTB runtime; `requirements.txt`; executor shutdown; helper; serve; mixed-stop; merge/deploy; live Telegram; повторное закрытие TASK-39–44; автостарт следующего code task.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | docs-контракт sender ownership + PTB gates; статус **review (подготовлено)** |
| 2026-09-24 | review blocker: Bot None / NO_TOKEN → fail-closed preflight (не no-op) |
| 2026-09-24 | review blocker: STOPPED idempotent fast-path; claim path side-effect-free |
| 2026-09-24 | GPT review `a3b9599…`; закрытие docs; runtime нет; O10 открыт |
