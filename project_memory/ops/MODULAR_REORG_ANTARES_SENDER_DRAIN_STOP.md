# Контракт isolated Telegram sender drain / stop ownership (TASK-44)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт **подготовлен** (этот срез); runtime **нет**; merge/deploy нет |
| **База** | закрытие TASK-43 `c17eab2fc7962f18b7702be73459afcd9d82133f` (accepted runtime `01e0c55dc84b9e6be78ff20f6b1f5b58be017601`, Draft PR #46) |
| **Обследованный SHA** | `c17eab2…` |
| **Drain/stop** | [MODULAR_REORG_ANTARES_DRAIN_STOP.md](MODULAR_REORG_ANTARES_DRAIN_STOP.md) TASK-39 (S1–S3, D27/D28/D30, O3; **O10 не закрывать**) |
| **Registry daemon** | [MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md](MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md) TASK-42/43 — join **до** sender intake seal |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Цель: зафиксировать **ownership**, accounting S1–S3, idle, intake seal и stop lifecycle будущего isolated sender stop **без** runtime в этом PR. Это **не** полный graceful, **не** helper wiring, **не** executor shutdown, **не** mixed-stop (O10).

Имена будущего code PR (не в этом срезе): ориентиры TASK-39 `pending_loop_handoffs` / intake sealed / remainder; точные Python-имена — на code slice.

---

## 0. Жёсткие правила

1. `WorkAdmission.seal()` **сам по себе** **не** даёт права остановить process-global sender.
2. Пустая `asyncio.Queue` / `qsize()==0` / health `queue_depth` **не** доказывают sender idle.
3. `_record_enqueue` — **health/metrics**, **не** correctness S1 accounting (см. § 4).
4. Registry daemon join (**TASK-43**) **до** sender intake seal (D30).
5. Cancel waiter **не** отменяет accepted send и **не** снимает accounting.
6. Deadline при живом S3 / worker / thread → **failure + remainder**, без restart/retry.
7. Delivery/business failure (Telegram API error на message) ≠ resource shutdown failure.
8. PTB `Application` Bot (`modules.antares.application_lifecycle`) и module-level sender `Bot` (`integrations.telegram_bot`) — **разные** ownership; не смешивать close без доказательства.
9. O10 mixed-stop **остаётся открытым**; этот контракт его **не** закрывает.
10. Helper / `run_ptb_lifecycle` **не** подключать в этом и в ближайшем code slice без отдельной задачи.

---

## 1. Обследованные production paths (SHA `c17eab2…`)

### 1.1 Core module — `integrations/telegram_bot.py`

| Факт | Доказательство |
|------|----------------|
| Process-global `HTTPXRequest` + `Bot` | module-level `request = HTTPXRequest(...)`; `bot = Bot(token=..., request=request)` |
| Process-global `asyncio` loop + `Queue` | `loop = asyncio.new_event_loop()`; `queue = asyncio.Queue()` |
| Daemon loop thread при import | `threading.Thread(target=_loop_runner, daemon=True).start()` — **handle не сохраняется** |
| Worker Task при import | `loop.call_soon_threadsafe(loop.create_task, _worker())` — **Task handle не сохраняется** |
| `_worker` | `while True: func, args = await queue.get()` … `await func(*args)` … `queue.task_done()` |
| `send_message_sync` / `send_file_sync` | `loop.call_soon_threadsafe(queue.put_nowait, item)` затем `_record_enqueue(...)`; **не** ждут доставки |
| `send_photo_sync` | прямой `requests.post(.../sendPhoto)` на caller thread; **не** queue; при exception → `send_message_sync` fallback |
| `send_message_direct` | прямой `requests.post` (помечен «тестами» в docstring); вне S1–S3 |

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

Inspect/close HTTPX относится к **PTB Application** `app.bot`, **не** к `integrations.telegram_bot.bot`. Публичные API (python-telegram-bot **22.8** в обследованном env): `HTTPXRequest.initialize` / `shutdown`; `Bot.initialize` / `shutdown`; leftover via `request._client` / `is_closed`. Это **другой** Bot/request graph.

---

## 2. Ownership verdict (главный вопрос)

**Выбор: B — process-global / shared module resource.**

Доказательства:

1. Sender — module globals без привязки к `WorkAdmission`.
2. Один и тот же модуль обслуживает Antares WE/jobs **и** mixed/raccoon/downloader/bakai/conversion/… callers.
3. Import `integrations.telegram_bot` (в т.ч. только health) стартует loop+worker в **любом** процессе.
4. `apps/antares.py` + `enforce_antares_isolated_profile()` доказывают dedicated **process profile** для entrypoint, но **не** делают module sender «owned by admission»; stop API сегодня отсутствует и не проверяет ownership.
5. `WorkAdmission.seal()` не пересекается с sender intake.

**Следствие для isolated Antares stop:**

- Пока нет явного **ownership / dedicated-process gate** в stop API, isolated Antares **REFUSE** останавливать global sender (SND12).
- Молчаливое «admission sealed → stop sender» **запрещено**.
- O10 mixed-stop **не** закрывается этим выбором.
- Будущий code slice **может** добавить gate вида «process is dedicated Antares isolated runtime» (entrypoint/profile attestation) как **dependency** перед stop; до implementation gate stop остаётся unwired / refuse.

Это **не** отменяет TASK-39 O3 для **полного** graceful dedicated process: полный graceful **обязан** уметь stop sender **когда ownership доказан**. До gate — полный graceful **не** заявлять.

---

## 3. S1 / S2 / S3

Сохраняются определения TASK-39 для `send_message_sync` / `send_file_sync`:

| Состояние | Смысл |
|-----------|--------|
| **S1** | `call_soon_threadsafe` **принял** callback, `queue.put_nowait` **ещё не** исполнен на sender loop |
| **S2** | item **в** `asyncio.Queue` (после успешного `put_nowait`) |
| **S3** | `_worker` сделал `queue.get()` и выполняет `await bot.send_*` (до `task_done`) |

Инвариант: `queue.empty()` / `qsize()==0` **не** ⇒ idle (S1 или S3 возможны).

Фактический runtime сегодня: `put_nowait` передаётся **прямо** в `call_soon_threadsafe`; отдельного loop-side wrapper нет. Future code slice должен ввести `_enqueue_item` (или эквивалент) для D27/D28.

---

## 4. Handoff accounting (будущий code)

Process-local correctness accounting (**не** `_record_enqueue`):

1. Под sender lock: `pending_loop_handoffs += 1`, отпустить lock.
2. `loop.call_soon_threadsafe(_enqueue_item, payload)`.
3. Если `call_soon_threadsafe` бросил: под lock `pending_loop_handoffs -= 1` **ровно один раз**; terminal intake failure записан (D27).
4. На owner loop `_enqueue_item`: `try: queue.put_nowait(...)` / `finally` или оба path: закрыть S1 (`pending_loop_handoffs -= 1` ровно раз). Если put бросил — terminal intake failure; handoff не зависает (D28); **не** считать message delivered.

`_record_enqueue` сегодня:

- вызывается **после** успешного `call_soon_threadsafe`;
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

`asyncio.Queue.join()`: ждёт `task_done` для каждого успешно `put` item → покрывает **S2+S3** для поставленных items. **S1 не покрывает** (item ещё не put). Поэтому S1 учитывается отдельно.

Health `queue_depth` **не** idle proof.

---

## 6. Intake seal

Порядок producers до seal:

Accepted executor drain → WE workers stop → **registry daemon join** → **тогда** sender intake seal.

После seal: новый `send_message_sync` / `send_file_sync` → **explicit reject/failure** (не silent enqueue; счётчик S1 не растёт).

D30: registry append warnings после WE `task_done` → `send_message_sync`; seal **раньше** registry join запрещён (SND6).

---

## 7. Stop lifecycle (будущий code; docs only)

Раздельно, не «queue empty»:

| Шаг | Действие |
|-----|----------|
| A | Intake seal |
| B | Wait S1 == 0 |
| C | Wait S2+S3 idle (owner-loop join / unfinished) |
| D | Stop worker (sentinel/control item **или** иной однозначный protocol) |
| E | Close sender Bot / HTTPXRequest HTTP resources **на sender loop** |
| F | Stop event loop (`loop.stop` / эквивалент) |
| G | Join loop thread (нужен сохранённый thread handle) |

Требования к worker stop:

- sentinel/control сам корректно `task_done`;
- не обгоняет accepted S1 (seal + S1==0 до stop);
- worker не остаётся pending навечно на `queue.get` без terminal;
- repeat stop идемпотентен (SND11);
- cancel wait не теряет work (SND8);
- deadline → failure/remainder (SND9);
- worker already dead → explicit failure, не success по empty queue (SND13).

Future ownership state (сохранять handles): loop thread; worker Task; lifecycle (running/stopping/stopped); repeat shutdown semantics.

---

## 8. HTTP lifecycle (sender Bot ≠ PTB Application Bot)

Обследовано (ptb **22.8**):

| Объект | Close/shutdown API |
|--------|-------------------|
| `telegram.request.HTTPXRequest` | `initialize()`, `shutdown()` (async) |
| `telegram.Bot` | `initialize()`, `shutdown()`; Telegram API `close*` — **не** HTTP client close |
| HTTPX client | leftover: client present ∧ `not is_closed` |

Правила будущего sender close:

1. Выполнять на **sender** event loop (тот же, что `_worker`).
2. Не вызывать PTB `application_lifecycle` против module `bot` без доказательства identity (сегодня identity **нет**).
3. Если `Bot._requests_initialized`: `await bot.shutdown()`, затем добить open clients через `request.shutdown` при необходимости (паттерн как в `application_lifecycle._shutdown_bot_http_clients`).
4. Если Bot никогда не `initialize`: всё равно закрыть open `HTTPXRequest` через `shutdown`, не считая no-op `Bot.shutdown` успехом.
5. Timeout/error на HTTP close → shutdown failure в remainder; не silent success.
6. В mixed/shared ownership stop **refuse** → HTTP **не** закрывать (SND12).

---

## 9. `send_photo_sync` verdict

| Вопрос | Ответ на `c17eab2…` |
|--------|---------------------|
| Production Antares callers? | **Не найдены** (только unit sanitization test) |
| Queue S1–S2–S3? | **Нет** — direct `requests.post` |
| Active at shutdown? | Только если кто-то вызовет на caller thread; не видно isolated Antares path |
| Входит в sender queue drain? | **Нет** |
| Fallback | exception → `send_message_sync` → **уже** S1–S3 / intake seal |
| Scope TASK-44/будущий sender stop | **Out of queue drain accounting**; отдельный direct-HTTP resource **не** вводить в этом контракте. SND15: photo out-of-scope; fallback message подчиняется seal/S1–S3. Не считать photo drained из-за queue idle. |

`send_message_direct` — similarly direct HTTP; tests/docs only intent; не часть S1–S3.

---

## 10. Failure / remainder (будущий code)

Минимум diagnostics:

- ownership safe? / gate passed?;
- intake sealed?;
- S1 handoff count;
- queue unfinished count;
- active S3 count;
- worker task state (missing/done/alive);
- loop running?;
- loop thread alive?;
- HTTP client open? (sender request);
- terminal intake failures (D27/D28);
- reason.

Разделение:

| Класс | Пример |
|-------|--------|
| Delivery / business | `bot.send_*` поднял Telegram API error; `task_done` выполнен → resource item terminal; delivery failed |
| Resource shutdown | worker/thread alive после deadline; HTTP open; refuse ownership; S1 stuck |

---

## 11. Shutdown order (пока)

```
Accepted executor drain
→ WE queues/workers stop
→ registry daemon join
→ sender: ownership gate → intake seal → idle → worker → HTTP → loop → thread
→ executor resource shutdown
→ PTB cleanup / helper orchestration
```

Open question: если ownership gate = refuse, helper **не** должен притворяться полным graceful. Порядок выше **не** менять молча без нового review.

---

## 12. Матрица Event/barrier (будущий code; без sleep-as-proof)

| ID | Сценарий | Ожидание |
|----|----------|----------|
| SND1 | S1 callback accepted, put ещё нет; queue empty | idle **FALSE** |
| SND2 | item в queue (S2) | idle **FALSE** |
| SND3 | queue empty; worker в `bot.send_*` (S3) | idle **FALSE** |
| SND4 | `call_soon_threadsafe` throws после +handoff | handoff 0; failure recorded |
| SND5 | loop-side `put_nowait` throws | handoff 0; failure recorded |
| SND6 | registry warning после WE done | sender stop **до** registry join запрещён |
| SND7 | intake sealed | новый enqueue explicit reject |
| SND8 | cancel wait | accepted send продолжается; accounting жив |
| SND9 | deadline при active S3 | failure + remainder; no restart/retry |
| SND10 | successful idle | worker stop → HTTP close → loop stop → thread join |
| SND11 | repeat stop | idempotent; no second sentinel/close |
| SND12 | mixed/shared ownership | **refuse** stop; global sender usable |
| SND13 | worker unexpectedly dead | explicit failure; empty queue ≠ success |
| SND14 | message send failure | task accounting terminal; delivery ≠ shutdown failure |
| SND15 | `send_photo_sync` | out-of-scope queue drain; fallback → S1–S3 rules |

---

## 13. Open questions

1. **Ownership gate shape** для future code: как аттестовать dedicated Antares process (entrypoint-only? profile? explicit process flag?) не принимая seal admission за ownership.
2. Нужен ли отдельный accounting для in-flight `send_photo_sync` / `send_message_direct`, если появятся production callers.
3. Должен ли future stop сохранять worker Task / thread handles при **первом** import refactor, или отдельный lazy-start slice.
4. Взаимодействие refuse-sender-stop с helper «полный graceful» claim (не ослаблять; не wire сейчас).

---

## 14. Out of scope TASK-44

Runtime sender; executor shutdown; `run_ptb_lifecycle` wiring; polling/serve; mixed-stop (O10); deploy; live Telegram; merge/retarget; повторное закрытие TASK-39–43.
