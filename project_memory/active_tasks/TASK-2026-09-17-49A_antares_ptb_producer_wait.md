# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49A |
| **Статус** | review (CODE; GPT CHANGES → 3rd review-fix; Q-PTB1 **не** закрыт; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-49.S ACCEPTED/docs-close `1f078eb7704c3f3d6cfd90a59b5f16ce9dc0b6c5`; [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft [#54](https://github.com/deniskotdavydov1991-wq/Test/pull/54) `feat/task-2026-09-17-49a-antares-producer-wait`, base `feat/task-2026-09-17-49s-antares-shutdown-session` @ `1f078eb…` |
| **Риск** | medium: false producers_complete / seal races / foreign attestation |

CODE: Q-PTB1 producer-wait primitive. **Production wiring отсутствует**. **Q-PTB1 остаётся OPEN** до GPT acceptance. 49.A **не** закрывать. 49.B–E **не** начаты.

### Разграничение

| Слой | Состояние |
|------|-----------|
| Реализовано | intake seal, owner observation, issuer-bound attestation, permanent post-COMPLETE entry gates, pre-initialize install, unit tests on PTB 22.8 |
| Подключено | **нет** |
| Выпущено | **нет** |

---

## PTB survey (installed **22.8**)

| Вопрос | Ответ |
|--------|-------|
| Producers | queue → fetcher; `__process_update_wrapper` / unfinished; wrapped `process_update`; gated `Application.create_task`; processor concurrent count |
| New producers stop | seal intake (allow `_STOP_SIGNAL`); updater must not run; **after proven COMPLETE** new `process_update` / `Application.create_task` **remain refused** through cleanup and after terminal |
| PTB stop/shutdown | `stop` puts `_STOP_SIGNAL` then joins fetcher / gathers **existing** create_task set — **no** new business `process_update`/`create_task`; `shutdown` does not either |
| Completion | sealed idle epoch stable across `call_soon` barrier (not sleep-as-proof) |
| Install | **once**, **before** `Application.initialize()` / `start()`; reinstall refused; counters alone cannot prove absence of direct in-flight `process_update` |
| Supported | lifecycle graph + Antares intake queue; `concurrent_updates` True/False; `block=False` via `Application.create_task` |
| Rejected | plain queue; unsupported processor/persistence/job_queue; updater running; forged/stale/`for_tests` when Application bound; install after initialize/start or reinstall |
| Constraint | untracked raw `asyncio.create_task` outside `Application.create_task` is **out of supported graph** — not claimed covered by closed-set |

---

## Review-fix (post `d058d0eb…`)

1. Removed global reopen via `enter_cleanup_phase`. After COMPLETE, entry gates stay closed through cleanup and after terminal. PTB 22.8 stop needs only sealed `_STOP_SIGNAL`.
2. Install only before `initialize()`; refuse reinstall; refuse post-initialize install even when queue/create_task/processor counters are zero under a live direct `process_update`.
3. Preserved: helpers-only cancel; one owner + procedure deadline; late complete after incomplete; outcome/snapshot; issuer-bound attestation; `_STOP_SIGNAL`.

**Closed-set claim:** not asserted as fully proven. Observed entry gates for wrapped `process_update` / `Application.create_task` + seal are regression-covered for the scenarios above; raw `asyncio.create_task` remains unsupported. **Q-PTB1 still OPEN**.

---

## Success Criteria

- [x] Prior review-fix (cancel / attestation / owner / outcome / post-COMPLETE refuse / for_tests orders)
- [x] Entries stay closed during cleanup and after terminal; stop/shutdown without reopening
- [x] Pre-initialize install + refuse direct in-flight / reinstall
- [ ] GPT re-review / Q-PTB1 acceptance
- [ ] lifecycle wiring (49.B+)
- [ ] merge/deploy

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Python **3.12.10** @ Test SHA `6ab0775743dfb6cd0e1a4f71f5a243ef37939f43`: producer_wait **14**; + shutdown + admission = **146**; lifecycle **19** (не суммировать) |
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
| 2026-09-30 | 2nd review-fix @ `94fb8f2b…`; docs `d058d0eb…` |
| 2026-09-30 | GPT CHANGES @ `d058d0eb…`: cleanup reopen + install-before-init |
| 2026-09-30 | 3rd review-fix @ Test SHA `6ab07757…` |
