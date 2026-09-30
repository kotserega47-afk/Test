# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49A |
| **Статус** | review (CODE; GPT CHANGES → review-fix; Q-PTB1 **не** закрыт; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.S ACCEPTED/docs-close `1f078eb7704c3f3d6cfd90a59b5f16ce9dc0b6c5`; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft [#54](https://github.com/deniskotdavydov1991-wq/Test/pull/54) `feat/task-2026-09-17-49a-antares-producer-wait`, base `feat/task-2026-09-17-49s-antares-shutdown-session` @ `1f078eb…` |
| **Риск** | medium: false producers_complete / seal races / foreign attestation |

CODE: Q-PTB1 producer-wait primitive. **Production wiring отсутствует**. **Q-PTB1 остаётся OPEN** до GPT acceptance. 49.B–E **не** начаты.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | intake seal, owner observation, issuer-bound attestation, process_update tracking, unit tests on PTB 22.8 |
| Подключено | **нет** |
| Выпущено | **нет** |

---

## PTB survey (installed **22.8**)

| Вопрос | Ответ |
|--------|-------|
| Producers | queue → fetcher; `__process_update_wrapper` / unfinished; wrapped `process_update`; `Application.create_task` set; processor concurrent count |
| New producers stop | seal intake (allow `_STOP_SIGNAL`); updater must not run |
| Completion | sealed idle epoch stable across `call_soon` barrier (not sleep-as-proof) |
| Supported | lifecycle graph + Antares intake queue; `concurrent_updates` True/False; `block=False` via `Application.create_task` |
| Rejected | plain queue; unsupported processor/persistence/job_queue; updater running; forged/stale/`for_tests` on bound issuer |
| Constraint / blocker | untracked raw `asyncio.create_task` outside `Application.create_task` is **out of supported graph** — wiring that needs it cannot claim truthful attestation from this primitive |

---

## Review-fix (post `f1b2204…`)

1. Never cancel producer Tasks; only helper pulses cancelled; two-producer + deadline regressions.
2. Attestation issued only by completed owner procedure (secret/issuer/procedure/seal); validate on accept; bind_producer_wait owner-loop + no bind after test attest.
3. Closed set: intake seal + process_update wrap + create_task set + concurrent + unfinished; raw asyncio.create_task documented as unsupported.
4. One owner procedure; caller deadline ≠ ending observation; late complete same owner; procedure_deadline not refreshed.
5. COMPLETE + attestation ⇒ `snapshot.producers_complete=True`; repeat/host.snapshot agree.

---

## Success Criteria

- [x] Review-fix items 1–5
- [x] Real PTB scenarios (block=False, create_task, sequential)
- [ ] GPT re-review / Q-PTB1 acceptance
- [ ] lifecycle wiring (49.B+)
- [ ] merge/deploy

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10** @ Test SHA `718592bec45be82b31e0cab037303f9475f13e91`: producer_wait **10**; + shutdown + admission = **142**; lifecycle **19** (не суммировать) |
| GPT | re-review pending |

---

## Out Of Scope

P5–P9 wiring; 49.B–E; Ready/merge/retarget/deploy; closing Q-PTB1 before GPT accept.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | CODE: producer-wait primitive; Draft PR #54 |
| 2026-09-30 | GPT CHANGES @ `f1b2204…`: cancel/attestation/closed-set/owner-deadline/outcome |
| 2026-09-30 | Review-fix for items above |
