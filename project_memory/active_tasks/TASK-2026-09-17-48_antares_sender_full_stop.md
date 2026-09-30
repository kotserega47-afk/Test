# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-48 |
| **Статус** | review (пройден; docs-close выполнен; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-47 close `51af50697e783aa735241d8532b7f04e55c0f11f` (accepted runtime `230975c433e6ad9353b6f86485f010e1b89eacb0`, Draft PR #50); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [SENDER_GATES.md](../ops/MODULAR_REORG_ANTARES_SENDER_GATES.md) |
| **PR** | Draft [#51](https://github.com/deniskotdavydov1991-wq/Test/pull/51) `feat/task-2026-09-17-48-antares-sender-full-stop`, base `feat/task-2026-09-17-47-antares-sender-worker-drain` @ `51af50697e783aa735241d8532b7f04e55c0f11f` |
| **Риск** | medium: incorrect HTTP/loop phase or partial-repeat could leave stuck resources or false STOPPED |

Review **пройден**. GPT accepted runtime HEAD `7f6b5a8c211658fba92e2f6b98320b3443935cb6` (ACCEPTED). Cursor pytest (Python 3.12): full stop **36**; worker drain **36**; gates+ownership **25**; health/transport/token **27**; boot+lifecycle **53**. GPT смотрел код/diff/тесты и pytest **не** запускал. GitHub Actions и комментариев на accepted HEAD нет. Этот docs-коммит — закрытие TASK-48. Runtime между accepted HEAD и close не менялся.

Sender full resource stop **принят**, **не выпущен**. PR #51 остаётся Draft/open. Merge/deploy/helper wiring нет. O10 открыт. TASK-39–47 повторно не закрывать. TASK-49 **не** стартовал. Остановка самого Telegram sender **не** означает полный graceful Antares.

Ownership C+D и PTB fail-closed gates (TASK-45/46) и worker drain (TASK-47) **не** переинтерпретированы.

`test_o9_claim_does_not_start_thread_or_network` проходит в предписанном изолированном прогоне gates+ownership (**25 passed**). Исторический сбой того же теста в процессе, который уже импортировал `integrations.telegram_bot`, в TASK-48 **не** менялся.

---

## Accepted runtime

| Поле | Значение |
|------|----------|
| Accepted runtime HEAD | `7f6b5a8c211658fba92e2f6b98320b3443935cb6` |
| GPT verdict | **ACCEPTED** |
| GPT pytest | not independently run (code/diff/tests reviewed) |
| Cursor tests | **36** full stop; **36** worker drain; **25** gates+ownership; **27** health/transport/token; **53** boot+lifecycle (Python 3.12) |
| PR #51 | Draft/open; base `feat/task-2026-09-17-47-antares-sender-worker-drain` @ `51af50697e783aa735241d8532b7f04e55c0f11f`; mergeable на момент accept |
| Lifecycle | `RUNNING → DRAINING → WORKER_STOPPED → HTTP_STOPPING → HTTP_STOPPED → LOOP_STOPPING → STOPPED` |
| Success claim | `worker_stopped`, `http_stopped`, `loop_running=False`, `loop_thread_alive=False`, `thread_joined`, `full_resource_stopped=True`, `lifecycle_state=STOPPED` |

До TASK-48 HTTP close, `loop.stop`, join потока loop и финальный `STOPPED` были будущим срезом. На `7f6b5a8…` этот срез sender-а реализован. Глобальный helper, `run_ptb_lifecycle`, executor shutdown, порядок WorkAdmission/WE/registry, O10, polling/serve и deploy остаются **не** подключены.

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
| One owner HTTP close session; absolute deadline; phase ack | `_owner_http_close_session` / `_run_http_close_phase` |
| HTTP close plan + Bot/request close on sender loop | `_http_close_on_sender_loop` |
| Loop-stop ack (`_loop_stopped`) + non-blocking thread join | `_run_loop_stop_phase` |
| No new `loop.stop` after exhausted overall deadline | `deadline_before_loop_stop` |
| Drain compatibility for post-worker states | `drain_and_stop_sender_worker` / `_snapshot_drain_fields` |
| Tests FS1–FS25 + HC1–HC5 + HD1–HD3 + subprocess wiring | `tests/unit/test_telegram_sender_full_stop.py` |

### Accepted invariants (runtime @ `7f6b5a8…`)

- Exact Antares ownership proof first.
- `STOPPED` + same proof = idempotent fast-path; `STOPPED` + foreign/no proof = refuse.
- TASK-47 worker drain reused, not reimplemented; clean worker stop required before HTTP mutation.
- Exact PTB request graph stored before destructive HTTP mutation.
- `Bot.shutdown` + request shutdown run on the sender loop.
- Successful `Bot.shutdown` is not repeated on partial retry; already-closed request targets are not unnecessarily reclosed.
- Partial HTTP failure remains repeatable; Bot/request failures are safe structured diagnostics.
- HTTP control failures do not contaminate D27/D28 user-intake counters.
- One owner HTTP close session; caller cancellation detaches the waiter only; session start is not cancelled by the caller.
- Owner session owns its absolute deadline; no extra +30s; no caller-thread `Task.done()` / `Task.cancel()` correctness access.
- HTTP terminal publication/ack is owned by the sender-loop session.
- `loop.stop` only after `HTTP_STOPPED`; a **new** `loop.stop` is not scheduled after the overall deadline is exhausted.
- Loop-stop return is acknowledged by `_loop_stopped`; thread join is non-blocking via `asyncio.to_thread`.
- `STOPPED` only after the loop is not running and the thread is no longer alive; no production restart-after-STOPPED.
- Drain API remains truthful after full `STOPPED`.
- `full_resource_stopped=True` only at genuine terminal resource stop.

---

## Success Criteria

- [x] Ownership first; STOPPED fast-path same/foreign
- [x] Partial-state continuation (no duplicate sentinel/HTTP/loop.stop)
- [x] PTB plan retained; Bot.shutdown + per-request best-effort; fail-closed leftovers
- [x] HTTP incomplete → no loop.stop; cancel leaves repeatable state
- [x] Loop stop only after HTTP_STOPPED; no new loop.stop after exhausted deadline; thread join via `asyncio.to_thread`
- [x] Drain after full stop remains truthful/idempotent
- [x] Unit/regressions PASS (Cursor 36/36/25/27/53)
- [x] GPT review ACCEPTED на `7f6b5a8…`; GPT pytest не запускал
- [x] docs-close (этот коммит)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | runtime на base `51af506…`; pytest 36 / 36 / 25 / 27 / 53 (Python 3.12) |
| GPT | review PR #51 / diff на `7f6b5a8…`; ACCEPTED; pytest **не** запускал |

---

## Out Of Scope

Helper / `run_ptb_lifecycle` wiring; executor shutdown; WorkAdmission/WE/registry orchestration; mixed-stop O10; polling/serve; requirements pin; Railway; deploy; live Telegram; merge of PR #50; TASK-49. Глобальный graceful Antares этим срезом **не** закрыт.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-29 | CODE: full sender resource stop; Draft PR #51; ожидание GPT review |
| 2026-09-30 | GPT fixes: owner HTTP deadline, no +30s, cancel-safe session start, no caller-thread Task.done/cancel, `deadline_before_loop_stop` |
| 2026-09-30 | GPT ACCEPTED `7f6b5a8…`; docs-close; runtime unchanged since accepted HEAD |
