# Контракт isolated Telegram sender drain / stop ownership (TASK-44) + TASK-47/48 runtime notes

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт TASK-44 **принят** (`0e770a38eac570585ba698b30a39ef7162fe10b5`); close `49193bb…`; ownership/PTB → [SENDER_GATES.md](MODULAR_REORG_ANTARES_SENDER_GATES.md) TASK-45/`a3b9599…`; foundation → TASK-46/`0d80bb2…`; **worker drain runtime → TASK-47 ACCEPTED `230975c433e6ad9353b6f86485f010e1b89eacb0`**, docs-close on Draft PR #50; **sender full resource stop → TASK-48 ACCEPTED `7f6b5a8c211658fba92e2f6b98320b3443935cb6`**, docs-close on Draft PR #51. Orchestration helper docs — [SHUTDOWN_ORCHESTRATION.md](MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md) TASK-49 GPT ACCEPTED `237b20e…` (**не** wired); **49.S ACCEPTED** `f7dd672…` (primitive **не** wired; **49.A** next). O10 / deploy **не** закрыты |
| **База TASK-44** | закрытие TASK-43 `c17eab2fc7962f18b7702be73459afcd9d82133f` (accepted runtime `01e0c55dc84b9e6be78ff20f6b1f5b58be017601`, Draft PR #46) |
| **Обследованный SHA (исторический survey)** | `c17eab2…` — § 1 описывает **pre-TASK-47** sender runtime |
| **Accepted worker-drain runtime** | TASK-47 `230975c…` — § 1A; lifecycle до `WORKER_STOPPED` only (исторический срез: HTTP/loop/thread тогда не закрывались) |
| **Accepted sender resource stop** | TASK-48 `7f6b5a8…` — § 1B; HTTP → loop → thread → `STOPPED`. **Не** глобальный graceful |
| **Drain/stop** | [MODULAR_REORG_ANTARES_DRAIN_STOP.md](MODULAR_REORG_ANTARES_DRAIN_STOP.md) TASK-39 (S1–S3, D27/D28/D30, O3; **O10 не закрывать**) |
| **Registry daemon** | [MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md](MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md) TASK-42/43 — join **до** sender intake seal |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Цель TASK-44: зафиксировать **ownership**, accounting S1–S3, idle, intake seal и stop lifecycle будущего isolated sender stop.  
Цель TASK-47 (принят): реализовать intake seal + S1/S3 drain + worker Task stop до `WORKER_STOPPED` **без** Bot/HTTP/loop/thread close.  
Цель TASK-48 (принят `7f6b5a8…`): довести **сам** Telegram sender до `STOPPED` (HTTP request graph, `loop.stop`, ack `_loop_stopped`, join потока). Это **не** helper и **не** полный graceful Antares.

Это **не** полный graceful, **не** helper wiring, **не** executor shutdown, **не** mixed-stop (O10). Orchestration docs TASK-49 **приняты** (`237b20e…`); helper **не** wired; **49.S ACCEPTED** `f7dd672…` (**не** wired; **49.A** next).

Имена code API (TASK-47): `drain_and_stop_sender_worker`, `SenderWorkerDrainResult`, `WorkerTerminalOutcome`, sentinel state machine — см. [TASK-47](../active_tasks/TASK-2026-09-17-47_antares_sender_worker_drain.md).

---

## 0. Жёсткие правила

1. `WorkAdmission.seal()` **сам по себе** **не** даёт права остановить process-global sender.
2. Пустая `asyncio.Queue` / `qsize()==0` / health `queue_depth` **не** доказывают sender idle.
3. `_record_enqueue` — **health/metrics**, **не** correctness S1 accounting (см. § 4).
4. Registry daemon join (**TASK-43**) **до** sender intake seal (D30).
5. Cancel waiter **не** отменяет accepted send и **не** снимает accounting.
6. Deadline при живом S3 / worker / thread → **failure + remainder**, без restart/retry.
7. Delivery/business failure (Telegram API error на message; terminal intake D27/D28) ≠ resource shutdown failure. Resource stop может быть успешен при зафиксированной недоставке/отказе intake (см. § 4).
8. PTB `Application` Bot (`modules.antares.application_lifecycle`) и module-level sender `Bot` (`integrations.telegram_bot`) — **разные** ownership; не смешивать close без доказательства.
9. O10 mixed-stop **остаётся открытым**; этот контракт его **не** закрывает.
10. Helper / `run_ptb_lifecycle` **не** подключать в этом и в ближайшем code slice без отдельной задачи.
11. Для **каждого** успешного `queue.get()` worker обязан вызвать `queue.task_done()` **ровно один раз** в `finally` — независимо от send/health/logger/diagnostics (см. § 5, SND14). То же для stop sentinel/control item. **TASK-47 accepted runtime выполняет этот invariant.**
12. Repo pin: `requirements.txt` → `python-telegram-bot>=20.7`. Обследованный env **22.8** — **не** repo contract. Sender HTTP close (TASK-48) использует public API + capability gate / fail-closed TASK-45/46 (см. § 8), без pin 22.8.

---

## 1. Обследованные production paths (исторический survey SHA `c17eab2…`)

> **Исторический контекст:** таблица ниже — **pre-TASK-47** surveyed runtime на `c17eab2…`.  
> **Не** читать как current accepted runtime. Worker-drain truth → § 1A / TASK-47 `230975c…`. Full sender resource stop → § 1B / TASK-48 `7f6b5a8…`.

### 1.1 Core module — `integrations/telegram_bot.py` (survey `c17eab2…`)

| Факт (на survey SHA) | Доказательство |
|------|----------------|
| Process-global `HTTPXRequest` + `Bot` | module-level `request = HTTPXRequest(...)`; `bot = Bot(token=..., request=request)` |
| Process-global `asyncio` loop + `Queue` | `loop = asyncio.new_event_loop()`; `queue = asyncio.Queue()` |
| Daemon loop thread при import | `threading.Thread(target=_loop_runner, daemon=True).start()` — **на survey SHA handle не сохранялся** |
| Worker Task при import | `loop.call_soon_threadsafe(loop.create_task, _worker())` — **на survey SHA Task handle не сохранялся** |
| `_worker` | `while True: func, args = await queue.get()` … try send/health … `queue.task_done()` **не** в unconditional `finally` **на survey SHA** |
| `send_message_sync` / `send_file_sync` | **на survey SHA** прямо `loop.call_soon_threadsafe(queue.put_nowait, item)` затем `_record_enqueue(...)`; **не** ждали доставки |
| `send_photo_sync` | прямой `requests.post(.../sendPhoto)` на caller thread; **не** queue; при exception → `send_message_sync` fallback |
| `send_message_direct` | прямой `requests.post` (помечен «тестами» в docstring); вне S1–S3 |

### 1A. Accepted TASK-47 runtime (`230975c…`) — worker drain only

Исторический срез: HTTP / `loop.stop` / join / final `STOPPED` здесь **ещё не** сделаны. Текущая правда после TASK-48 — § 1B.

| Факт (TASK-47) | Статус |
|----------------|--------|
| Loop thread handle `_loop_thread` | **хранится**; thread остаётся alive после `WORKER_STOPPED` |
| Worker Task handle `_worker_task` | **хранится**; readiness `_worker_ready` async-waitable |
| Lifecycle | `RUNNING → DRAINING → WORKER_STOPPED` (`WORKER_STOPPED ≠` final `STOPPED`) |
| Intake seal | atomic vs admit; late send → `TelegramSenderIntakeClosedError` |
| S1 | increment **before** `call_soon_threadsafe(_loop_side_enqueue)`; D27/D28 exact; intake ≠ control/sentinel failures |
| S3 | around user send only |
| `task_done` | **ровно один раз** в `finally` после каждого успешного `get` (включая sentinel) |
| Sentinel | `NOT_SUBMITTED → SCHEDULED → ENQUEUED \| FAILED`; `SCHEDULED` ≠ submitted; pending not duplicated; FAILED repeatable |
| Terminal observe | `WorkerTerminalOutcome` + `asyncio.shield`; caller cancel ≠ worker cancel |
| `DRAINING → WORKER_STOPPED` | требует **sentinel ENQUEUED** + **clean** `WorkerTerminalOutcome`; `task.done()` alone insufficient |
| Drain API | `drain_and_stop_sender_worker(ownership_proof, …)` after ownership → PTB → structural |
| HTTP / Bot / request | **untouched** на этом срезе |
| `loop.stop` / thread.join | **не** реализованы на этом срезе |
| `full_resource_stopped` | всегда `False` в TASK-47 |

### 1B. Accepted TASK-48 runtime (`7f6b5a8…`) — sender resource stop

| Факт (current) | Статус |
|----------------|--------|
| Lifecycle | `RUNNING → DRAINING → WORKER_STOPPED → HTTP_STOPPING → HTTP_STOPPED → LOOP_STOPPING → STOPPED` |
| Worker drain | reused TASK-47; clean stop обязателен до HTTP mutation |
| HTTP | один owner close session на sender loop; absolute deadline; phase ack; successful `Bot.shutdown` не повторяется; partial retry repeatable |
| Caller cancel | снимает только waiter; start сессии caller не отменяет; нет caller-thread `Task.done()` / `Task.cancel()` |
| `loop.stop` | только после `HTTP_STOPPED`; **новый** `loop.stop` не ставится, если общий deadline уже исчерпан (`deadline_before_loop_stop`) |
| Ack / join | `_loop_stopped`; `asyncio.to_thread` join |
| `STOPPED` | только когда loop не running и thread не alive; same-proof fast-path; foreign/no proof = refuse; restart-after-STOPPED нет |
| `full_resource_stopped` | `True` только на настоящем terminal resource stop |
| API | `stop_isolated_sender` |
| Всё ещё не wired | helper, `run_ptb_lifecycle`, executor shutdown, WorkAdmission/WE/registry в один graceful path, O10, polling/serve, deploy/live Telegram; orchestration contract → TASK-49 |

### 1.2 Transport — `transport/telegram_transport.py`

| API | Делегирует |
|-----|------------|
| `send_text` | `send_message_sync` |
| `send_document` | `send_file_sync` |

### 1.3 Production callers (неполный список по import graph)

| Источник | Путь |
|----------|------|
| WE worker | `automation/worker.py` → `transport.send_text` / `send_document` |
| Registry warnings | `integrations/wallet_editor_registry.py` → `_send_*_warning` → `send_message_sync` (slow/timeout/rev/otlezka) |
| Registry refresh | `integrations/wallet_editor_registry_refresh.py` → `send_message_sync` |
| Auto-Enable | `integrations/wallet_editor_auto_enable.py` → `send_message_sync` |
| Routes | `integrations/telegram_routes.py` → `send_message_to_route` / `send_file_to_route` |
| Antares jobs path | `modules/antares/jobs.py` / hourly wrapper → routes → sender |
| Mixed / raccoon / download / bakai / conversion / payout / script_jobs / `main.py` | прямые `send_message_sync` / `send_file_sync` |
| Mixed scheduler | `scheduler.py` → `log_telegram_health_if_due` (import модуля поднимает loop/thread) |
| Antares handlers | health snapshot only (`get_telegram_sender_health_snapshot`) — **тот же** module import surface |
| `send_photo_sync` | **нет** production callers вне tests (`tests/unit/test_telegram_token_sanitization.py`) |

### 1.4 PTB lifecycle — `modules/antares/application_lifecycle.py`

Inspect/close HTTPX относится к **PTB Application** `app.bot`, **не** к `integrations.telegram_bot.bot`. Смысл request graph: `Bot._request` как tuple (getUpdates request, general API request); `_iter_bot_requests` / `_close_open_http_clients` закрывают **каждый** open request best-effort независимо. Это **другой** Bot; future sender stop **не** вызывает `application_lifecycle` на module sender Bot, но **перенимает semantics** полного request graph (см. § 8).

Обследованный local env имел python-telegram-bot **22.8**; repo `requirements.txt` pins только `>=20.7` — см. § 8 dependency.

---

## 2. Ownership verdict (главный вопрос)

**Выбор: B — process-global / shared module resource.**

Доказательства:

1. Sender — module globals без привязки к `WorkAdmission`.
2. Один и тот же модуль обслуживает Antares WE/jobs **и** mixed/raccoon/downloader/bakai/conversion/… callers.
3. Import `integrations.telegram_bot` (в т.ч. только health) стартует loop+worker в **любом** процессе.
4. `apps/antares.py` + `enforce_antares_isolated_profile()` доказывают dedicated **process profile** для entrypoint, но **не** делают module sender «owned by admission».
5. `WorkAdmission.seal()` не пересекается с sender intake.

**Следствие для isolated Antares stop:**

- Пока нет явного **ownership / dedicated-process gate** в stop API, isolated Antares **REFUSE** останавливать global sender (SND12).
- Молчаливое «admission sealed → stop sender» **запрещено**.
- O10 mixed-stop **не** закрывается этим выбором.
- **TASK-46** реализовал process-local immutable ownership claim + proof; **TASK-47** drain API принимает этот proof перед seal. Full resource stop всё ещё отказывает без будущих slices.

Это **не** отменяет TASK-39 O3 для **полного** graceful dedicated process: полный graceful **обязан** уметь stop sender **когда ownership доказан**. До полного HTTP/loop/thread slice — полный graceful **не** заявлять.

---

## 3. S1 / S2 / S3

Сохраняются определения TASK-39 для `send_message_sync` / `send_file_sync`:

| Состояние | Смысл |
|-----------|--------|
| **S1** | `call_soon_threadsafe` **принял** callback, `queue.put_nowait` **ещё не** исполнен на sender loop |
| **S2** | item **в** `asyncio.Queue` (после успешного `put_nowait`) |
| **S3** | `_worker` сделал `queue.get()` и выполняет `await bot.send_*` (до `task_done`) |

Инвариант: `queue.empty()` / `qsize()==0` **не** ⇒ idle (S1 или S3 возможны).

**Исторический survey (`c17eab2…`):** `put_nowait` передавался **прямо** в `call_soon_threadsafe`; отдельного loop-side wrapper не было.

**Accepted TASK-47 (`230975c…`):** `_admit_user_send` → S1 increment → `call_soon_threadsafe(_loop_side_enqueue)` → owner-loop `put_nowait` + D27/D28 close. Это **current** correctness path, не «future code».

---

## 4. Handoff accounting

Process-local correctness accounting (**не** `_record_enqueue`):

1. Под sender lock: `pending_loop_handoffs += 1`, отпустить lock.
2. `loop.call_soon_threadsafe(_enqueue_item, payload)`.
3. Если `call_soon_threadsafe` бросил: под lock `pending_loop_handoffs -= 1` **ровно один раз**; terminal intake failure записан (D27).
4. На owner loop `_enqueue_item`: `try: queue.put_nowait(...)` / `finally` или оба path: закрыть S1 (`pending_loop_handoffs -= 1` ровно раз). Если put бросил — terminal intake failure; handoff не зависает (D28); **не** считать message delivered.

**TASK-47 accepted:** этот accounting **реализован** для user intake. Sentinel/control failures **не** инкрементируют terminal user-intake counter.

**D27/D28 vs resource shutdown:** terminal intake/delivery failure **не** означает, что sender resource shutdown обязан зависнуть или считаться failed. Если handoff accounting уже terminal (S1==0), queue drained, worker/HTTP/loop/thread корректно остановлены — **resource shutdown может быть успешным**. Потерянная/непринятая delivery остаётся видна **отдельно** в result/diagnostics. Failure list ≠ «resource still alive».

`_record_enqueue` сегодня:

- вызывается **после** успешного handoff/put path (health only);
- крутит health `queue_depth` / `total_enqueued`;
- **не** откатывается при loop-side put failure;
- зависит от порядка с `logger.info` (не correctness).

**Запрещено** считать `_record_enqueue` доказательством S1 без отдельного redesign.

Наблюдение queue unfinished / join — **только** на owner loop или через thread-safe bridge (не читать `asyncio.Queue` небезопасным cross-thread способом для correctness).

---

## 5. Idle condition

Sender **idle** только если одновременно:

1. intake уже sealed (или seal+idle atomic under same lock — TASK-39 § 3);
2. `pending_loop_handoffs == 0` (S1);
3. queue unfinished work == 0 (S2+S3 для успешно put items);
4. active S3 == 0 (worker не между `get` и `task_done` для user payload).

`asyncio.Queue.join()`: ждёт `task_done` для каждого успешно `put` item → покрывает **S2+S3** для поставленных items **только если** каждый успешный `get` гарантированно делает ровно один `task_done`. **S1 не покрывает** (item ещё не put). Поэтому S1 учитывается отдельно.

**Жёсткий invariant:** для **каждого** успешного `queue.get()` — `queue.task_done()` вызывается **ровно один раз** в `finally`, независимо от:

- `bot.send_*` success/failure;
- delivery metrics / health bookkeeping;
- `logger` / diagnostic exceptions.

То же для stop sentinel/control item: ровно один `task_done`.

**Исторический survey:** `_worker` держал `task_done()` **вне** unconditional `finally`.  
**Accepted TASK-47:** `task_done` в `finally` — current runtime; иначе `Queue.join` зависает (SND14).

Health `queue_depth` **не** idle proof.

---

## 6. Intake seal

Порядок producers до seal:

Accepted executor drain → WE workers stop → **registry daemon join** → **тогда** sender intake seal.

После seal: новый `send_message_sync` / `send_file_sync` → **explicit reject/failure** (не silent enqueue; счётчик S1 не растёт).

**TASK-47 accepted:** seal реализован в `drain_and_stop_sender_worker` (после ownership/PTB/structural). Helper orchestration / registry-before-seal ordering **не** wired.

D30: registry warning после WE `task_done` → `send_message_sync`; seal **раньше** registry join запрещён (SND6) — orchestration still future.

---

## 7. Stop lifecycle

Раздельно, не «queue empty»:

| Шаг | Действие | TASK-47 (исторически) | Current @ TASK-48 `7f6b5a8…` |
|-----|----------|----------------------|------------------------------|
| A | Intake seal | **done** | **done** (reuse) |
| B | Wait S1 == 0 | **done** | **done** (reuse) |
| C | Wait S2+S3 idle (owner-loop join / unfinished) | **done** | **done** (reuse) |
| D | Stop worker (sentinel protocol + clean terminal) | **done** → `WORKER_STOPPED` | **done** (reuse; clean stop до HTTP) |
| E | Close **entire sender Bot request graph** HTTP resources **на sender loop** (см. § 8) | **not started** | **done** — owner session, deadline, phase ack |
| F | Stop event loop (`loop.stop` / эквивалент) | **not started** | **done** после `HTTP_STOPPED`; новый schedule после исчерпанного deadline **нет** |
| G | Join loop thread | **not started** (handle already stored) | **done** — ack `_loop_stopped` + `asyncio.to_thread` |

Требования к worker stop (accepted @ `230975c…`):

- sentinel/control сам корректно `task_done` (**ровно один раз**, § 5);
- не обгоняет accepted S1 (seal + S1==0 до stop);
- worker не остаётся pending навечно на `queue.get` без terminal;
- repeat stop идемпотентен после clean `WORKER_STOPPED` (SND11); abnormal terminal stays `DRAINING`;
- cancel wait не теряет work (SND8); caller cancel does not cancel worker;
- deadline → failure/remainder (SND9);
- worker unexpectedly dead / unclean terminal → explicit failure, не success по empty queue / `task.done()` alone (SND13);
- `DRAINING → WORKER_STOPPED` только при sentinel `ENQUEUED` + clean `WorkerTerminalOutcome`.

Ownership state (TASK-47): loop thread handle; worker Task handle; lifecycle `RUNNING|DRAINING|WORKER_STOPPED`; repeat shutdown semantics for worker slice.

**До TASK-48 было future:** HTTP close, `loop.stop`, thread join, final `STOPPED`.

**Current @ `7f6b5a8…`:** эти четыре пункта реализованы для самого Telegram sender (§ 1B). Same-proof `STOPPED` fast-path реализован.

**Всё ещё future / не wired:** helper orchestration, `run_ptb_lifecycle`, executor resource shutdown, WorkAdmission/WE/registry в один graceful path, mixed-stop O10, polling/serve, deploy/live Telegram. Порядок orchestration зафиксирован в TASK-49 docs; code **не** wired. Полный graceful Antares из одного sender `STOPPED` **не** следует.

---

## 8. HTTP lifecycle (sender Bot ≠ PTB Application Bot)

### 8.1 Repo dependency vs surveyed env

| Source | Version |
|--------|---------|
| `requirements.txt` (repo contract) | `python-telegram-bot>=20.7` — **не** pin 22.8 |
| Local survey env at docs time | **22.8** (observation only) |

Future sender shutdown **не** строит молча на предположении «production всегда 22.8».

**Dependency / PTB gate:** закрыто TASK-45/46 — runtime capability gate + fail-closed; см. [SENDER_GATES.md](MODULAR_REORG_ANTARES_SENDER_GATES.md). Surveyed 22.8 observation only; `requirements.txt` `>=20.7` unchanged by TASK-44/45/46/47.

### 8.2 Public lifecycle API vs private diagnostics

**Public API** (использовать только после проверки фактического runtime/API):

- `Bot.shutdown()`
- `BaseRequest` / `HTTPXRequest.shutdown()` (async)

Telegram API methods `Bot.close*` (forum topics и т.п.) — **не** HTTP client close.

**Private / defensive only** (не стабильный контракт от `>=20.7` до произвольной версии):

- `Bot._requests_initialized`
- `Bot._request` (часто tuple: getUpdates request + general API request)
- `request._client` / `is_closed`

Private допустимы только как compatibility/diagnostic path. Contract **не** считает их stable API.

### 8.3 Ownership = entire sender Bot request graph

Module-level `request = HTTPXRequest(...)` — general sender request, переданный в `Bot(..., request=request)`. Это **не** автоматически единственный HTTP client Bot.

В PTB Bot request graph обычно включает **более одного** request object (смысл как в `application_lifecycle._iter_bot_requests`: getUpdates + general). Контракт shutdown (TASK-47 этого **не** делал; TASK-48 `7f6b5a8…` **делает**):

1. На **sender** event loop.
2. **Не** вызывать `modules.antares.application_lifecycle` на module sender Bot (другая ownership).
3. Перенять **semantics** `_iter_bot_requests` / `_close_open_http_clients`: инспектировать/закрывать **все** request objects, принадлежащие **этому** sender Bot, которые реально существуют и open.
4. **Недостаточно** считать shutdown успешным после одного `await request.shutdown()` на module-level `request`.
5. Каждый open request закрывается **best-effort независимо**: failure одного **не** отменяет попытку закрыть остальные.
6. Если public `Bot.shutdown` / `request.shutdown` недоступны или graph unavailable → **fail-closed** shutdown failure (не silent success).
7. Timeout/error на любом request close → отразить в remainder; не silent success.
8. Mixed/shared ownership stop **refuse** → HTTP **не** закрывать (SND12).

**TASK-47 explicitly does not** call Bot/request shutdown or close HTTP. **TASK-48 does**, on the sender loop, with the semantics above.

### 8.4 Remainder HTTP diagnostics (вместо одного флага)

Примеры полей (точные Python-имена не фиксировать):

- `sender_request.open` (general API request);
- `sender_get_updates_request.open` (если существует в graph);
- `request_graph.unavailable`;
- `shutdown_api.unavailable`;
- per-request leftover после best-effort close.

---

## 9. `send_photo_sync` verdict

| Вопрос | Ответ на `c17eab2…` / unchanged by TASK-47 |
|--------|---------------------|
| Production Antares callers? | **Не найдены** (только unit sanitization test) |
| Queue S1–S2–S3? | **Нет** — direct `requests.post` |
| Active at shutdown? | Только если кто-то вызовет на caller thread; не видно isolated Antares path |
| Входит в sender queue drain? | **Нет** |
| Fallback | exception → `send_message_sync` → **уже** S1–S3 / intake seal |
| Scope TASK-44/47/будущий sender stop | **Out of queue drain accounting**; отдельный direct-HTTP resource **не** вводить в worker-drain slice. SND15: photo out-of-scope; fallback message подчиняется seal/S1–S3. Не считать photo drained из-за queue idle. |

`send_message_direct` — similarly direct HTTP; tests/docs only intent; не часть S1–S3.

---

## 10. Failure / remainder

Минимум diagnostics (TASK-47 `SenderWorkerDrainResult` covers worker-drain fields):

- ownership safe? / gate passed?;
- intake sealed?;
- S1 handoff count;
- queue unfinished / drained?;
- active S3 count;
- worker task state (missing/done/alive) + terminal outcome class;
- loop running?;
- loop thread alive?;
- sentinel submitted / state;
- sender Bot **request graph** leftovers — structured diagnostics full stop (§ 8.4; реализовано TASK-48);
- terminal intake failures (D27/D28) — **отдельно** от resource outcome;
- reason.

Разделение:

| Класс | Пример |
|-------|--------|
| Delivery / business / intake | `bot.send_*` API error; D27/D28 terminal intake; `task_done` выполнен → queue item terminal; delivery/intake failed **видно в diagnostics** |
| Worker-drain resource | unclean `WorkerTerminalOutcome`; worker cancelled/exception after ENQUEUED; join hang; deadline |
| Full resource shutdown (sender, TASK-48) | any request graph leftover open; loop still required-stopped; thread join fail; refuse ownership; `deadline_before_loop_stop` не ставит новый `loop.stop` |

D27/D28 в failure list **не** автоматически ⇒ resource still alive (§ 4).

---

## 11. Shutdown order (пока)

```
Accepted executor drain
→ WE queues/workers stop
→ registry daemon join
→ sender: ownership gate → intake seal → idle → worker (TASK-47 done @ WORKER_STOPPED)
→ HTTP → loop → thread → STOPPED   ← TASK-48 done @ `7f6b5a8…` (сам sender; **не** helper)
→ executor resource shutdown          ← не wired
→ PTB cleanup / helper orchestration  ← не wired
```

Open question: если ownership gate = refuse, helper **не** должен притворяться полным graceful. Порядок выше **не** менять молча без нового review.

---

## 12. Матрица Event/barrier (без sleep-as-proof)

| ID | Сценарий | Ожидание |
|----|----------|----------|
| SND1 | S1 callback accepted, put ещё нет; queue empty | idle **FALSE** |
| SND2 | item в queue (S2) | idle **FALSE** |
| SND3 | queue empty; worker в `bot.send_*` (S3) | idle **FALSE** |
| SND4 | `call_soon_threadsafe` throws после +handoff | handoff 0; intake failure recorded; resource stop всё ещё возможен если idle (§ 4) |
| SND5 | loop-side `put_nowait` throws | handoff 0; intake failure recorded; resource stop всё ещё возможен если idle (§ 4) |
| SND6 | registry warning после WE done | sender stop **до** registry join запрещён |
| SND7 | intake sealed | новый enqueue explicit reject |
| SND8 | cancel wait | accepted send продолжается; accounting жив; worker **не** cancelled |
| SND9 | deadline при active S3 / terminal wait | failure + remainder; no restart/retry |
| SND10 | successful idle + clean terminal | worker → `WORKER_STOPPED`; drain API оставляет HTTP/loop/thread живыми. Full stop (`stop_isolated_sender`) продолжается до `STOPPED` (TASK-48) |
| SND11 | repeat stop after clean `WORKER_STOPPED` | idempotent success |
| SND12 | mixed/shared ownership | **refuse** stop; global sender usable |
| SND13 | worker unexpectedly dead / unclean terminal | explicit failure; empty queue / `task.done()` ≠ success |
| SND14 | send / logging / health failure после `queue.get` | `task_done` **ровно один раз**; unfinished accounting terminal; delivery/diagnostic failure отдельно; `Queue.join` **не** висит; ≠ resource still alive |
| SND15 | `send_photo_sync` | out-of-scope queue drain; fallback → S1–S3 rules |

---

## 13. Open questions / follow-ons

**Закрыты TASK-45/46** (см. [SENDER_GATES.md](MODULAR_REORG_ANTARES_SENDER_GATES.md)):

1. Ownership gate shape → **C+D** process-local immutable attestation + explicit proof to stop API.
2. PTB strategy → **B** runtime capability gate + fail-closed (pin not required by contract).

**Закрыты TASK-47 (worker-drain slice):**

- S1/S3 accounting + seal + sentinel + clean terminal classification to `WORKER_STOPPED`.
- Handles for loop thread / worker Task stored.

Остаются (не GATE design; **не** автостарт TASK-49):

3. Нужен ли отдельный accounting для in-flight `send_photo_sync` / `send_message_direct`, если появятся production callers.
4. Full HTTP request-graph close + `loop.stop` + thread join + final `STOPPED` — **закрыто TASK-48** `7f6b5a8…` для самого sender. До этого среза пункт был future.
5. Взаимодействие refuse-sender-stop с helper «полный graceful» claim (не ослаблять; не wire сейчас). Helper / executor / WE/registry orchestration / O10 / deploy остаются открытыми.

---

## 14. Out of scope

### TASK-44 (docs contract)
Runtime sender (at contract time); `requirements.txt` / PTB pin change; executor shutdown; `run_ptb_lifecycle` wiring; polling/serve; mixed-stop (O10); deploy; live Telegram; merge/retarget; повторное закрытие TASK-39–43.

### TASK-47 (accepted worker drain — historical out of that slice)
Bot.shutdown / request shutdown; complete HTTP request graph close; `loop.stop`; loop thread join; final `STOPPED`; orchestration/helper wiring; executor shutdown; mixed-stop resolution; deploy/live Telegram; TASK-48.

На момент TASK-47 эти sender-resource пункты были будущими. TASK-48 их закрыл для самого sender. Helper, executor, O10, deploy и TASK-49 этим списком **не** закрыты.

### TASK-48 (accepted sender resource stop — still out)
Helper / `run_ptb_lifecycle` wiring; executor resource shutdown; WorkAdmission/WE/registry в один graceful path; mixed-stop O10; polling/serve; deploy; live Telegram; TASK-49. `STOPPED` sender-а **не** объявляет глобальный graceful завершённым.
