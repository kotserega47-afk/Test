# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49 |
| **Статус** | review (пройден; docs-close выполнен; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-48 close `90cda7c92e56df3657c293f2e6de6ee65d2426c0` (accepted runtime `7f6b5a8c211658fba92e2f6b98320b3443935cb6`, Draft PR #51); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md); [REGISTRY_DAEMON.md](../ops/MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft [#52](https://github.com/deniskotdavydov1991-wq/Test/pull/52) `docs/task-2026-09-17-49-antares-shutdown-orchestration`, base `feat/task-2026-09-17-48-antares-sender-full-stop` @ `90cda7c92e56df3657c293f2e6de6ee65d2426c0` |
| **Риск** | high: future wiring must honor owner-session / P10 gate / cleanup observer; Q-PTB1; O10 открыт |

Review **пройден**. GPT ACCEPTED docs HEAD `237b20efeb2aed76d24f620a9a1cc2110145344c`. GPT смотрел документы/diff и соответствие ранее обследованным API; pytest **не** запускал (docs-only). CI PASS **не** заявлять: на accepted HEAD найденных check-runs/statuses нет. Этот docs-коммит — закрытие TASK-49. Runtime между accepted HEAD и close **не** менялся (его и не было).

Docs-контракт **принят**, **не выпущен**. PR #52 остаётся Draft/open. Merge/Ready/retarget/deploy/helper wiring **нет**. Открыты: Q-PTB1, Q-EX2, Q-OWN1, Q-HLP1, Q-REC1, O10. TASK-39–48 повторно не закрывать. Следующий этап: **49.S** (ещё **не** начат); автостарт slices **нет**.

---

## Accepted docs

| Поле | Значение |
|------|----------|
| Accepted review HEAD | `237b20efeb2aed76d24f620a9a1cc2110145344c` |
| GPT verdict | **ACCEPTED** |
| GPT pytest | not run (docs-only; docs/diff + surveyed APIs) |
| CI | no check-runs/statuses on accepted HEAD; **do not claim CI PASS** |
| PR #52 | Draft/open; base `feat/task-2026-09-17-48-antares-sender-full-stop` @ `90cda7c…` |
| Runtime | **нет**; helper **не** wired |

---

## Goal

Контракт общей остановки isolated Antares со стыком `run_ptb_lifecycle`: owner shutdown-session, snapshot vs SESSION_TERMINAL, P10 gate на `producers_complete`, cleanup observe budget, repeat без нового deadline после terminal.

---

## Success Criteria

- [x] Таблица фаз + owner-session model
- [x] Cancel after OPEN → P3–P9; Accepted не cancel
- [x] Cleanup observer: snapshot ≠ terminal; hung ≠ stopped; no SESSION_TERMINAL while cleanup Task alive
- [x] Full P10 только с producers proof; deadline ≠ proof; Application HTTP open; Orc17/22
- [x] Repeat: join / read terminal; no new deadline; no new destructive session; Orc7 = TASK-48 sender API; Orc21
- [x] EX1; WE partial ≠ success-only; Q-PTB1 blocks wiring; 49.S first in MIGRATION
- [x] GPT review ACCEPTED на `237b20e…`; GPT pytest не запускал; CI PASS не заявлен
- [x] docs-close (этот коммит)
- [ ] 49.S / helper wiring (future; **не** автостарт)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| GPT | review PR #52 / docs+diff на `237b20e…`; соответствие ранее обследованным API; **ACCEPTED**; pytest **не** запускал (docs-only) |
| CI | check-runs/statuses на accepted HEAD **не** найдены → CI PASS **не** заявлять |

---

## Открытые gates (не закрывать этим docs-close)

Q-PTB1, Q-EX2, Q-OWN1, Q-HLP1, Q-REC1, O10.

---

## Out Of Scope

Runtime/tests/requirements; helper wiring; 49.S–E автостарт; serve/polling; O10; merge/Ready/retarget/deploy; Move agent root; исходное Test; изменение TASK-41 runtime; bounded process exit; повторное закрытие TASK-39–48.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | docs-контракт; Draft PR #52 |
| 2026-09-30 | GPT review: cancel-after-OPEN, deadline/P10, WE partial, plan sync |
| 2026-09-30 | GPT review: cleanup snapshot/terminal, P10 vs live producers, repeat after terminal, 49.S in MIGRATION |
| 2026-09-30 | GPT ACCEPTED `237b20e…`; docs-close; runtime unchanged (none); 49.S не начат |
