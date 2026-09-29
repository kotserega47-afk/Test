# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-47 |
| **Статус** | review (подготовлено; docs-close **не** выполнен) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-46 close `63cd1f1e94df79369a0d2e2f82b03b4074e7efab` (accepted runtime `0d80bb291114344d13bc3064fa9d4d76e95c7221`, Draft PR #49); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [SENDER_GATES.md](../ops/MODULAR_REORG_ANTARES_SENDER_GATES.md) |
| **PR** | Draft [#50](https://github.com/deniskotdavydov1991-wq/Test/pull/50) `feat/task-2026-09-17-47-antares-sender-worker-drain`, base `feat/task-2026-09-17-46-antares-sender-gates-runtime` @ `63cd1f1…` |
| **Риск** | medium: incorrect S1/S3 or seal race could drop accepted sends or hang drain |

CODE: Telegram sender **intake accounting + drain + worker stop**. **Не** full resource shutdown (HTTP/loop/thread remain). **Не** docs-close до GPT review.

---

## Goal

Реализовать S1/S2/S3 correctness accounting, atomic intake seal, drain accepted work и terminal stop sender worker — без Bot/request/HTTP/loop/thread close.

---

## Delivered

| Piece | Location |
|-------|----------|
| Lifecycle `RUNNING→DRAINING→WORKER_STOPPED` | `integrations/telegram_bot.py` |
| S1 handoff + D27/D28 | `_admit_user_send` / `_loop_side_enqueue` |
| S3 active sends + exact-once `task_done` | `_worker` |
| Seal + late reject | `TelegramSenderIntakeClosedError` |
| Drain API | `async drain_and_stop_sender_worker(ownership_proof, *, timeout=...)` |
| Tests T1–T22 | `tests/unit/test_telegram_sender_worker_drain.py` |

Lifecycle **не** достигает final `STOPPED`. Success ⇒ `worker_stopped=True`, `full_resource_stopped=False`.

---

## Success Criteria

- [x] Loop thread + worker Task handles; readiness Event
- [x] Preflight ownership → PTB → structural before seal
- [x] Atomic seal vs admit; late send explicit reject
- [x] S1/S3 + D27/D28; exact-once task_done; sentinel after idle
- [x] One overall deadline; cancel leaves sealed/DRAINING
- [x] HTTP/loop/thread remain after success
- [x] Unit/regressions PASS (Cursor)
- [ ] GPT review
- [ ] docs-close
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

Bot/request shutdown; HTTPX close; final STOPPED; loop.stop; thread.join; helper wiring; executor; mixed-stop; deploy/live Telegram; TASK-48.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-25 | CODE: worker drain foundation; Draft PR; ожидание GPT review |
