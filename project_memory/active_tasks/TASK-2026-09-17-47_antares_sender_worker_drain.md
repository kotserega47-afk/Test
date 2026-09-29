# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-47 |
| **Статус** | review (пройден; docs-close выполнен; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-46 close `63cd1f1e94df79369a0d2e2f82b03b4074e7efab` (accepted runtime `0d80bb291114344d13bc3064fa9d4d76e95c7221`, Draft PR #49); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [SENDER_GATES.md](../ops/MODULAR_REORG_ANTARES_SENDER_GATES.md) |
| **PR** | Draft [#50](https://github.com/deniskotdavydov1991-wq/Test/pull/50) `feat/task-2026-09-17-47-antares-sender-worker-drain`, base `feat/task-2026-09-17-46-antares-sender-gates-runtime` @ `63cd1f1…` |
| **Риск** | medium: incorrect S1/S3 or seal race could drop accepted sends or hang drain |

Review **пройден**. GPT accepted runtime HEAD `230975c433e6ad9353b6f86485f010e1b89eacb0` (ACCEPTED). Cursor pytest: **36** / **25** / **27** / **53**. GPT pytest **не** запускал независимо. GitHub Actions на accepted HEAD — нет. Этот docs-коммит — закрытие TASK-47.

Worker-drain runtime **принят**, **не выпущен**. PR #50 остаётся Draft/open. Merge/deploy/helper wiring нет. O10 открыт. TASK-39–46 повторно не закрывать. TASK-48 / full HTTP-loop-thread shutdown — **не** автостарт.

Ownership C+D и PTB fail-closed gates (TASK-46) **не** переинтерпретированы.

---

## Accepted runtime

| Поле | Значение |
|------|----------|
| Accepted runtime HEAD | `230975c433e6ad9353b6f86485f010e1b89eacb0` |
| GPT verdict | **ACCEPTED** |
| GPT pytest | not independently run |
| Cursor tests | **36** worker drain; **25** ownership+gates; **27** health/transport/token; **53** boot+lifecycle (3.12.10) |
| PR #50 | Draft/open; base `feat/task-2026-09-17-46-antares-sender-gates-runtime` @ `63cd1f1…` |
| Lifecycle | `RUNNING → DRAINING → WORKER_STOPPED`; `WORKER_STOPPED ≠` final `STOPPED` |
| Success claim | `worker_stopped=True`, `full_resource_stopped=False`; loop running; loop thread alive; HTTP/Bot/request untouched |

---

## Goal

Реализовать S1/S3 correctness accounting, atomic intake seal, drain accepted work и terminal stop sender worker Task — без Bot/request/HTTP/loop/thread close.

---

## Delivered

| Piece | Location |
|-------|----------|
| Lifecycle `RUNNING→DRAINING→WORKER_STOPPED` | `integrations/telegram_bot.py` |
| Loop thread + worker Task handles; async readiness | `_loop_thread`, `_worker_task`, `_worker_ready` |
| S1 handoff + D27/D28 (intake ≠ control/sentinel) | `_admit_user_send` / `_loop_side_enqueue` |
| S3 active sends + exact-once `task_done` in `finally` | `_worker` |
| Seal + late reject | `TelegramSenderIntakeClosedError` |
| Sentinel protocol | `NOT_SUBMITTED → SCHEDULED → ENQUEUED \| FAILED` |
| Drain API | `async drain_and_stop_sender_worker(ownership_proof, *, timeout=...)` |
| Terminal classification | `WorkerTerminalOutcome` via `_await_worker_terminal` / shared post-sentinel observer |
| Tests T1–T22 + SENT/WT/QJ/SF + WT1–WT6 | `tests/unit/test_telegram_sender_worker_drain.py` |

### Accepted invariants (runtime @ `230975c…`)

- Preflight: ownership → PTB capability → structural before first mutation.
- Async readiness; caller asyncio loop не блокируется.
- Atomic intake seal vs send admission.
- S1 increment before `call_soon_threadsafe`; D27/D28 exact accounting.
- D27/D28 intake failures separate from resource/control/sentinel failures.
- S3 around user send only.
- Every successful `queue.get()` → exact-one `task_done` in `finally`.
- Sentinel: `SCHEDULED` is **not** submitted/enqueued; pending repeat cannot duplicate; `FAILED` safely repeatable; sentinel failure does **not** bump terminal user-intake counter.
- Queue-join waiter is cancellation-clean.
- Caller waiter cancel/timeout does **not** cancel sender worker (`asyncio.shield`).
- `WorkerTerminalOutcome` distinguishes caller cancellation / worker cancellation / worker exception / clean terminal.
- Repeat after `ENQUEUED` cannot infer success from `task.done()` alone.
- `DRAINING → WORKER_STOPPED` requires **both**: (1) sentinel `ENQUEUED`; (2) clean `WorkerTerminalOutcome`.
- Abnormal worker terminal → remain `DRAINING` + structured `unexpected_dead_worker`.

---

## Success Criteria

- [x] Loop thread + worker Task handles; readiness Event
- [x] Preflight ownership → PTB → structural before seal
- [x] Atomic seal vs admit; late send explicit reject
- [x] S1/S3 + D27/D28; exact-once task_done; sentinel after idle
- [x] One overall deadline; cancel leaves sealed/DRAINING
- [x] HTTP/loop/thread remain after success
- [x] Unit/regressions PASS (Cursor 36/25/27/53)
- [x] GPT review ACCEPTED на `230975c…`; GPT pytest не запускал
- [x] docs-close (этот коммит)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | runtime на base `63cd1f1…`; pytest 36/25/27/53 (3.12.10) |
| GPT | review PR #50 / diff на `230975c…`; ACCEPTED; pytest **не** запускал независимо |

---

## Out Of Scope

Bot/request shutdown; complete HTTP request graph close; final `STOPPED`; `loop.stop`; thread.join; helper wiring; executor shutdown; mixed-stop (O10); deploy/live Telegram; TASK-48.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-25 | CODE: worker drain foundation; Draft PR #50; ожидание GPT review |
| 2026-09-29 | GPT blockers fixed (async ready, sentinel ack, shield, join cleanup, intake≠sentinel, WorkerTerminalOutcome, repeat clean-terminal) |
| 2026-09-29 | GPT ACCEPTED `230975c…`; docs-close; runtime unchanged since accepted HEAD |
