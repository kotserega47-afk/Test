# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49A |
| **Статус** | review (CODE; GPT CHANGES → 2nd review-fix; Q-PTB1 **не** закрыт; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.S ACCEPTED/docs-close `1f078eb7704c3f3d6cfd90a59b5f16ce9dc0b6c5`; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft [#54](https://github.com/deniskotdavydov1991-wq/Test/pull/54) `feat/task-2026-09-17-49a-antares-producer-wait`, base `feat/task-2026-09-17-49s-antares-shutdown-session` @ `1f078eb…` |
| **Риск** | medium: false producers_complete / seal races / foreign attestation |

CODE: Q-PTB1 producer-wait primitive. **Production wiring отсутствует**. **Q-PTB1 остаётся OPEN** до GPT acceptance. 49.A **не** закрывать. 49.B–E **не** начаты.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | intake seal, owner observation, issuer-bound attestation, process_update+create_task entry gates after COMPLETE, install preconditions, unit tests on PTB 22.8 |
| Подключено | **нет** |
| Выпущено | **нет** |

---

## PTB survey (installed **22.8**)

| Вопрос | Ответ |
|--------|-------|
| Producers | queue → fetcher; `__process_update_wrapper` / unfinished; wrapped `process_update`; gated `Application.create_task`; processor concurrent count |
| New producers stop | seal intake (allow `_STOP_SIGNAL`); updater must not run; **after proven COMPLETE** new `process_update` / `Application.create_task` **refused** until `enter_cleanup_phase` |
| Completion | sealed idle epoch stable across `call_soon` barrier (not sleep-as-proof) |
| Supported | lifecycle graph + Antares intake queue; `concurrent_updates` True/False; `block=False` via `Application.create_task` |
| Rejected | plain queue; unsupported processor/persistence/job_queue; updater running; forged/stale/`for_tests` when Application bound (either bind order); host install while producers already in flight |
| Constraint / blocker | untracked raw `asyncio.create_task` outside `Application.create_task` is **out of supported graph** — wiring that needs it cannot claim truthful attestation from this primitive |

---

## Review-fix (post `014a38c…` / prior `718592be…`)

1. Post-COMPLETE entry close: `process_update` and `Application.create_task` refused until `enter_cleanup_phase` (session `start_cleanup`); already-accepted in-flight work continues; PTB regressions prove late entry does not run under active proof.
2. `for_tests` forbidden whenever Application is bound — both `bind_application → for_tests` and `for_tests → bind_application` / `bind_producer_wait`; unbound harness only.
3. Tracker install: refuse if queue unfinished / live create_tasks / concurrent / non-empty queue (no infinite wait on untracked sequential work); install-after-drain OK.
4. Two-producer owner wait with deadline: observe → release first → second not cancelled / no proof → release second → COMPLETE.
5. Preserved: helpers-only cancel; one owner + procedure deadline; late complete after incomplete; outcome/snapshot consistency; issuer-bound attestation; `_STOP_SIGNAL` after seal.

Closed-set for **observed** PTB entries (`process_update` / `Application.create_task` + seal) is **proven by regression** at Test SHA below. Raw `asyncio.create_task` remains **unsupported** (design constraint, not claimed closed). **Q-PTB1 still OPEN**.

---

## Success Criteria

- [x] Prior review-fix (cancel / attestation / owner / outcome)
- [x] Post-COMPLETE entry gates + PTB regressions
- [x] for_tests bind-order refuse
- [x] Install-before-in-flight refuse
- [x] Two-producer owner-wait regression
- [ ] GPT re-review / Q-PTB1 acceptance
- [ ] lifecycle wiring (49.B+)
- [ ] merge/deploy

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10** @ Test SHA `94fb8f2b4854d037746fc2658d570a546f8d68b7`: producer_wait **13**; + shutdown + admission = **145**; lifecycle **19** (не суммировать) |
| GPT | re-review pending |

---

## Out Of Scope

P5–P9 wiring; 49.B–E; Ready/merge/retarget/deploy; closing Q-PTB1 / 49.A before GPT accept.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: producer-wait primitive; Draft PR #54 |
| 2026-09-30 | GPT CHANGES @ `f1b2204…`: cancel/attestation/closed-set/owner-deadline/outcome |
| 2026-09-30 | Review-fix @ `718592be…`; docs `014a38c…` |
| 2026-09-30 | GPT CHANGES @ `014a38c…`: entry close / for_tests / install / two-producer |
| 2026-09-30 | 2nd review-fix @ Test SHA `94fb8f2b…` |
