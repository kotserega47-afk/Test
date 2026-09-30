# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-48 |
| **Статус** | review (подготовлено; docs-close **не** выполнен) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-47 close `51af50697e783aa735241d8532b7f04e55c0f11f` (accepted runtime `230975c433e6ad9353b6f86485f010e1b89eacb0`, Draft PR #50); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [SENDER_GATES.md](../ops/MODULAR_REORG_ANTARES_SENDER_GATES.md) |
| **PR** | Draft [#51](https://github.com/deniskotdavydov1991-wq/Test/pull/51) `feat/task-2026-09-17-48-antares-sender-full-stop`, base `feat/task-2026-09-17-47-antares-sender-worker-drain` @ `51af506…` |
| **Риск** | medium: incorrect HTTP/loop phase or partial-repeat could leave stuck resources or false STOPPED |

CODE: Telegram sender **full resource shutdown** after TASK-47 worker drain. Completes HTTP → loop.stop → thread join → `STOPPED`. **Не** helper wiring. **Не** docs-close до GPT review.

---

## Goal

Finish sender resource stop only:

`WORKER_STOPPED → HTTP_STOPPING → HTTP_STOPPED → LOOP_STOPPING → STOPPED`

with ownership/PTB fail-closed, structured remainder, and async/non-blocking caller API.

---

## Delivered

| Piece | Location |
|-------|----------|
| Lifecycle extension through `STOPPED` | `integrations/telegram_bot.py` |
| Public API `stop_isolated_sender` | same |
| HTTP close plan + Bot/request best-effort close on sender loop | `_http_close_on_sender_loop` / `_run_http_close_phase` |
| Loop-stop ack (`_loop_stopped`) + non-blocking thread join | `_run_loop_stop_phase` |
| Drain compatibility for post-worker states | `drain_and_stop_sender_worker` / `_snapshot_drain_fields` |
| Tests FS1–FS25 + subprocess wiring | `tests/unit/test_telegram_sender_full_stop.py` |

Final success ⇒ `worker_stopped`, `http_stopped`, `loop_running=False`, `loop_thread_alive=False`, `thread_joined`, `full_resource_stopped=True`, `lifecycle_state=STOPPED`.

---

## Success Criteria

- [x] Ownership first; STOPPED fast-path same/foreign
- [x] Partial-state continuation (no duplicate sentinel/HTTP/loop.stop)
- [x] PTB plan retained; Bot.shutdown + per-request best-effort; fail-closed leftovers
- [x] HTTP incomplete → no loop.stop; cancel leaves repeatable state
- [x] Loop stop only after HTTP_STOPPED; thread join via `asyncio.to_thread`
- [x] Drain after full stop remains truthful/idempotent
- [x] Unit/regressions PASS (Cursor)
- [ ] GPT review
- [ ] docs-close
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

Helper / `run_ptb_lifecycle` wiring; executor shutdown; WorkAdmission/WE/registry orchestration; mixed-stop O10; polling/serve; requirements pin; Railway; deploy; live Telegram; merge of PR #50; TASK-49.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-29 | CODE: full sender resource stop; Draft PR; ожидание GPT review |
