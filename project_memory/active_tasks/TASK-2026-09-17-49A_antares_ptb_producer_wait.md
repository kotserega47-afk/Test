# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49A |
| **Статус** | review (пройден; **ACCEPTED** / **DOCS-CLOSED**; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.S ACCEPTED/docs-close `1f078eb7704c3f3d6cfd90a59b5f16ce9dc0b6c5`; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft [#54](https://github.com/deniskotdavydov1991-wq/Test/pull/54) `feat/task-2026-09-17-49a-antares-producer-wait`, base `feat/task-2026-09-17-49s-antares-shutdown-session` @ `1f078eb…` |
| **Риск** | medium: false producers_complete / seal races / foreign attestation |

Review **пройден**. GPT **ACCEPTED** review HEAD `2f1435b1976eea3c7a1d9a3ea3f8544d1288c5c3` (код/diff/regression-тесты; pytest GPT **не** запускал). Cursor pytest Python **3.12.10** на Test SHA `6ab0775743dfb6cd0e1a4f71f5a243ef37939f43`: producer_wait **14**; + shutdown_session + accepted_executor_work + work_admission **146**; lifecycle unit **19** (наборы **не** суммировать). CI PASS **не** заявлять. Этот docs-коммит — **DOCS-CLOSED** TASK-49.A. Runtime между accepted HEAD и close **не** менялся (docs-only).

Q-PTB1 producer-wait primitive **принят** для документированного поддерживаемого режима, **не выпущен**. PR #54 остаётся Draft/open. Production / `run_ptb_lifecycle` wiring **нет**. Полный shutdown Antares и production readiness **не** заявлять. Реальный lifecycle graph должен быть сверен с условиями ниже при wiring (**49.B** — следующий запланированный этап, **не** начат). 49.C–E **не** автостарт. Merge/Ready/retarget/deploy **нет**. TASK-49.S и предыдущие задачи повторно **не** закрывать.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | intake seal, owner observation, issuer-bound attestation, permanent post-COMPLETE entry gates, pre-initialize install, unit tests on PTB 22.8 |
| Подключено | **нет** |
| Выпущено | **нет** |

---

## Accepted runtime

| Поле | Значение |
|------|----------|
| Accepted review HEAD | `2f1435b1976eea3c7a1d9a3ea3f8544d1288c5c3` |
| Test SHA (Cursor pytest) | `6ab0775743dfb6cd0e1a4f71f5a243ef37939f43` |
| GPT verdict | **ACCEPTED** |
| GPT pytest | not run (code/diff/regression tests reviewed) |
| Cursor tests | **14** / **146** / **19** (Python 3.12.10; sets not summed) |
| PR #54 | Draft/open; base TASK-49.S docs @ `1f078eb…` |
| Modules | `modules.antares.ptb_producer_wait`, `modules.antares.ptb_update_intake` |
| Production wiring | **none** |

---

## Q-PTB1 accepted (supported mode)

Accepted as the producer-wait primitive for the **documented supported mode** only.

**Mandatory conditions for future wiring (49.B+):**

1. Single host install **before** `Application.initialize()` / `start()` (no reinstall; no post-init wrap).
2. Antares intake queue + supported PTB 22.8 graph (`SimpleUpdateProcessor`, no unsupported updater/job_queue/persistence paths claimed here).
3. No untracked background producers (raw `asyncio.create_task` outside `Application.create_task` remains out of observed set).
4. Application / issuer binding for attestation (no `for_tests` on a bound Application).
5. Permanent refuse of new `process_update` / `Application.create_task` after COMPLETE (through cleanup and after terminal); seal still allows `_STOP_SIGNAL` for staff `app.stop`.

Lifecycle wiring must **re-verify** that the real Antares graph matches these conditions. Full shutdown / production readiness are **out of scope** for this close.

---

## Delivered (accepted)

- Sealed `AntaresUpdateIntakeQueue` + owner `PtbProducerWaitHost` observation on real PTB 22.8.
- Issuer/secret `ProducersCompleteAttestation`; one owner procedure + fixed deadline; late complete after incomplete.
- Post-COMPLETE entry gates permanent; PTB stop needs only sealed `_STOP_SIGNAL`.
- Install-before-initialize; refuse reinstall and post-initialize install under live direct `process_update`.
- Helpers-only cancel; outcome/snapshot consistency; `_STOP_SIGNAL` after seal.

---

## Success Criteria

- [x] Prior review-fix deliverables (cancel / attestation / owner / outcome / entry gates / install)
- [x] Unit + admission/lifecycle regressions (Cursor 14/146/19 @ `6ab07757…`)
- [x] GPT review **ACCEPTED** @ `2f1435b…`; GPT pytest не запускал
- [x] Q-PTB1 accepted as supported-mode producer-wait primitive (wiring conditions recorded)
- [x] docs-close (этот коммит)
- [ ] merge/deploy (намеренно открыто)
- [ ] production / lifecycle wiring (49.B+; **не** этот PR)
- [ ] 49.B wire P5–P7 (следующий этап; **не** начат)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| GPT | review кода, diff и regression-тестов на accepted HEAD `2f1435b1976eea3c7a1d9a3ea3f8544d1288c5c3`; **pytest не запускался** |
| Cursor | Python **3.12.10** @ Test SHA `6ab0775743dfb6cd0e1a4f71f5a243ef37939f43`: producer_wait **14**; + shutdown + admission = **146**; lifecycle unit **19** (наборы не суммировать); CI PASS не заявлять |

---

## Out Of Scope

P5–P9 / lifecycle wiring; 49.B–E implementation; Ready/merge/retarget/deploy; full Antares shutdown; production readiness; повторное закрытие TASK-49.S и предыдущих задач.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: producer-wait primitive; Draft PR #54 |
| 2026-09-30 | GPT CHANGES @ `f1b2204…`: cancel/attestation/closed-set/owner-deadline/outcome |
| 2026-09-30 | Review-fix @ `718592be…`; docs `014a38c…` |
| 2026-09-30 | GPT CHANGES @ `014a38c…`: entry close / for_tests / install / two-producer |
| 2026-09-30 | 2nd review-fix @ `94fb8f2b…`; docs `d058d0eb…` |
| 2026-09-30 | GPT CHANGES @ `d058d0eb…`: cleanup reopen + install-before-init |
| 2026-09-30 | 3rd review-fix @ Test SHA `6ab07757…`; docs `2f1435b…` |
| 2026-09-30 | GPT **ACCEPTED** @ `2f1435b…`; **DOCS-CLOSED** (этот коммит) |
