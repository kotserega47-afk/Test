# Контракт общей остановки isolated Antares (TASK-49)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт **подготовлен** (GPT CHANGES REQUESTED → правки в этом PR); runtime **нет**; helper **не** wired; merge/deploy нет; TASK-49 **не** закрыт |
| **База** | TASK-48 docs-close `90cda7c92e56df3657c293f2e6de6ee65d2426c0` (accepted runtime `7f6b5a8c211658fba92e2f6b98320b3443935cb6`, Draft PR #51) |
| **Обследованный SHA** | `90cda7c…` (+ worker stop API на том же дереве); stop APIs приняты на `7f6b5a8…` |
| **PR** | Draft [#52](https://github.com/deniskotdavydov1991-wq/Test/pull/52) |
| **PTB helper** | [STARTSTOP.md](MODULAR_REORG_ANTARES_STARTSTOP.md) / `modules.antares.application_lifecycle.run_ptb_lifecycle` |
| **Drain/stop** | [DRAIN_STOP.md](MODULAR_REORG_ANTARES_DRAIN_STOP.md) TASK-39 |
| **WE stop** | TASK-41 `automation.worker.stop_isolated_profile_workers` |
| **Registry** | [REGISTRY_DAEMON.md](MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md) TASK-42/43 |
| **Sender** | [SENDER_DRAIN_STOP.md](MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md) TASK-44–48 |
| **Mixed gate** | [TASK-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |
| **O10** | mixed-stop **открыт** |

Цель: соединить **принятые** stop-механизмы с `run_ptb_lifecycle` в один проверяемый orchestration-контракт. Это **не** реализация, **не** wiring helper, **не** полный выпуск, **не** mixed-stop.

На `90cda7c…` helper после `stop.wait()` делает только defensive `seal()` и PTB `_cleanup_application`. Work drain / WE / registry / sender / executor **не** вызываются. TASK-49 фиксирует **что** должно появиться в будущих code slices.

`Sender STOPPED` (TASK-48) **сам по себе** ≠ общий graceful Antares success.

---

## 0. Жёсткие правила

1. `request_antares_stop`: **`admission.seal()` затем `stop.set()`** на owner loop. Уже Accepted work **продолжает** выполняться; Futures/continuation **не** cancel.
2. `stop.set()` / отсутствие polling / пустая Queue **не** доказывают конец PTB producers.
3. Не freeze registry, пока WE ещё может создать `schedule_registry_append`.
4. Не stop sender intake, пока registry daemon ещё может `send_message_sync` (D30).
5. Не использовать `_reset_job_executor_for_tests` как production shutdown; не `cancel_futures=True` на Accepted.
6. Не звать `ThreadPoolExecutor.shutdown(wait=True)` на PTB loop и **не** после исчерпания shutdown deadline.
7. Не обещать ограниченное время `exit` процесса.
8. Один absolute monotonic **shutdown deadline** на owner shutdown-сессию; фазам — **remaining**, не полный timeout заново.
9. Cancel **waiter** ≠ cancel owner-session / Accepted / WE / registry / sender HTTP session.
10. После `admission.open()` cancel/error **не** имеет права перейти сразу к PTB cleanup, обходя P3–P9 (§1 / §6).
11. Не заявлять, что overall shutdown deadline ограничивает существующий `_await_cleanup` (shielded, без timeout) (§5 / §8).
12. O10 / serve-polling / deploy / live Telegram — **не** этот контракт.
13. `delayed_cleanup` — документированное исключение.
14. Staged PTB cleanup **startup failure** (до OPEN) сохранить отдельным путём; PTB Application Bot ≠ module sender Bot.
15. Модель owner-session / ошибок / structured result **обязана** быть принята **до** wiring (§10); нельзя откладывать целиком в 49.E.
16. Q-PTB1 — design gate: **запрещён** зависимый wiring P4+/P6+/P7+ с `producers_complete=True`, пока primitive не принят.

---

## 1. Owner shutdown-сессия (нормативная модель)

### 1.1 Владелец

**Owner shutdown-session** — одна process-local сессия graceful stop для bound admission + lifecycle helper.

| Поле (семантика) | Смысл |
|------------------|--------|
| Owner | orchestration слой (будущий; внутри/рядом с `run_ptb_lifecycle`) |
| Started when | первое из: успешный `request_antares_stop` **или** cancel/error **после** `admission.open()` при ещё не завершённом graceful |
| Deadline | absolute monotonic, фиксируется в момент start сессии |
| Waiters | любые `await` наблюдатели; cancel waiter **не** отменяет сессию |
| Terminal | structured overall result (success или failure+remainder) |

Caller lifecycle Task может быть отменён; сессия продолжает фазы P3–P9 на owner loop / выделенных Tasks. Исходный `CancelledError` **сохраняется** как `primary` (или в `caller_cancels`), но **не** заменяет обязанность довести/зафиксировать shutdown-сессию.

### 1.2 Два пути до cleanup

| Путь | Условие | Допустимые шаги |
|------|---------|-----------------|
| **Startup failure** | ошибка/reject **до** `admission.open()` (в т.ч. до/во время initialize/start; polling/unsupported reject) | seal (если admission); staged `_cleanup_application`; **без** P3–P9 resource drain; **без** sender/executor stop claim |
| **Post-OPEN shutdown** | `admission` уже OPEN **или** уже SEALED после request stop; stop Event set **или** cancel/error во время `stop.wait` / running body | **обязаны** P3–P9 (насколько предусловия позволяют), затем P10; **запрещён** обход P3–P9 прямым P10 |

Текущий код helper на cancel после OPEN делает seal + `_await_cleanup` без drain — это **дефект относительно контракта**; future wiring обязан заменить поведение на post-OPEN путь.

### 1.3 Повторный stop

Повторный `request_antares_stop` / повторный await наблюдателя **присоединяется** к той же owner-session (если жива) или читает её terminal result. Не стартует вторую параллельную destructive сессию.

---

## 2. Обследованные surfaces @ `90cda7c…`

### 2.1 PTB helper — `modules/antares/application_lifecycle.py`

| API | Факт |
|-----|------|
| `run_ptb_lifecycle(...)` | initialize → start → `admission.open()` → `await stop.wait()` → seal → `_await_cleanup` |
| `_await_cleanup` | `asyncio.shield` на `_cleanup_application`; **нет timeout**; caller CancelledError копится в `ptb_caller_cancels`, cleanup продолжается |
| Startup fail | primary + seal + cleanup |
| Drain wiring | **нет** |

### 2.2 WorkAdmission / Accepted

`request_antares_stop` = seal → set. `wait_accepted_executor_work` ждёт пустой registry Futures; cancel wait ≠ cancel Futures. Seal ≠ sender ownership.

### 2.3 WE stop — `stop_isolated_profile_workers` (факт кода, не менять в этом PR)

| Факт | Доказательство |
|------|----------------|
| Preconditions | bound admission, `producers_complete`, SEALED, no Accepted, no continuation, `unfinished_tasks==0`, threads alive |
| Fast-path | если `_profile_workers_stop_done` → return sorted keys (**только полный успех**) |
| Sentinel | `sentinel_put` / `queue.put(PROFILE_WORKER_STOP)` **до** join |
| Success flag | `_profile_workers_stop_done = True` **только после** всех join |
| Partial mid-join | при cancel/timeout waiter: sentinel уже мог быть put; `stop_done` ещё False; повторный вызов снова требует alive threads → **already-finished thread ⇒ `worker is dead` refuse** |

**Выбор TASK-49:** orchestration обязан держать **одну owner-сессию WE stop**; повторный waiter **наблюдает** её. Текущий TASK-41 success-only fast-path **не** считается поддержкой partial retry. До появления owner-session API (slice 49.S / 49.B) wiring **не** имеет права обещать partial WE retry. Runtime TASK-41 в этом docs PR **не** менять.

### 2.4 Registry / Sender / Executor

Как прежде: registry после WE unfinished==0; sender `stop_isolated_sender` с remaining; executor process-global `get_job_executor()`; production shutdown API **нет**; test reset запрещён.

Boot holder proof: `apps.antares.AntaresBootPrefix.sender_ownership` **уже существует** (claim в `_boot_prefix`). Открыт способ **передачи** proof в helper/orchestrator (Q-OWN1), не наличие holder.

---

## 3. Выбранный порядок фаз (нормативный)

```text
0. Preconditions
1. request_antares_stop: seal() → stop.set()     # или cancel-after-OPEN стартует session без set — тогда seal+set обязаны до P3
2. await stop.wait()  /  observe cancel-after-OPEN → session start
3. Stop update intake (serve only / N/A)
4. Wait PTB producers (Q-PTB1 primitive; не bool)
5. wait_accepted_executor_work + continuation empty + WE unfinished==0
6. WE stop owner-session (observe/join; не success-only partial lie)
7. wait_isolated_registry_daemon_ops
8. stop_isolated_sender(proof, timeout=remaining)
9. Production executor shutdown (см. §4.4 / EX1)
10. PTB _cleanup_application (P10; не подменяет P3–P9)
```

### 3.1 Таблица фаз

| # | Фаза | Предусловия | Действие | Доказательство | Deadline / cancel / error | Следующая |
|---|------|-------------|----------|----------------|---------------------------|-----------|
| P0 | Preconditions | isolated; admission bindable; Application supported; sender proof claimed (`AntaresBootPrefix.sender_ownership` or equivalent) | observe | checks pass | refuse start | P1 |
| P1 | Request stop | P0 **или** post-OPEN cancel path needing explicit seal/set | `request_antares_stop` | SEALED + stop set | idempotent | P2 |
| P2 | Wake / session arm | helper running; admission **OPEN** or already SEALED | `stop.wait` returns **или** cancel/error after OPEN arms shutdown-session | session started; deadline fixed | **Cancel after OPEN → P3 (не P10).** Cancel before OPEN → startup path §8. Waiter cancel ≠ session cancel | P3 |
| P3 | Stop updates | session active | updater intake stop if serve else N/A | no new updates / N/A | failure → remainder; session continues | P4 |
| P4 | PTB producers | SEALED | wait via **accepted** Q-PTB1 primitive | truthful `producers_complete` | cancel waiter ≠ cancel Accepted; deadline → session failure+remainder; **no** WE/registry with false True; **wiring blocked until Q-PTB1** | P5 |
| P5 | Accepted + items | producers complete | wait Accepted + continuation empty + unfinished==0 | drained work | cancel wait ≠ cancel Futures | P6 |
| P6 | WE stop | P5 | **owner-session** stop/observe; sentinel/join per TASK-41 semantics | all joins done **and** future `stop_done`/session terminal | current API: mid-cancel + repeat may hit dead-worker refuse — remainder, not fake success; no duplicate owner-session | P7 |
| P7 | Registry | P6 terminal success (WE joined) | `wait_isolated_registry_daemon_ops` | daemons joined | no freeze before P6; deadline alive → failure | P8 |
| P8 | Sender | P7 | `stop_isolated_sender(proof, remaining)` | STOPPED+full_resource **или** structured sender remainder | partial repeatable; no new loop.stop after deadline | P9 if EX1 allows else mark P9 skipped+remainder → P10 |
| P9 | Executor | EX1 preconditions (§4.4) | production shutdown observe | threads terminal **или** remainder | no test reset; no wait=True after deadline | P10 |
| P10 | PTB cleanup | post-OPEN session reached terminal attempt of P3–P9 **или** startup path | `_cleanup_application` via `_await_cleanup` | actions/leftover/errors | **no overall timeout claim** over shielded cleanup; hung cleanup → leftover/remainder; preserve primary CancelledError | terminal report |

### 3.2 Противоречия (сохранённые решения)

- **TASK-39 §5 vs TASK-42:** helper order = WE → registry → sender (D30).
- **Helper code vs contract:** сегодня post-OPEN cancel → cleanup only; wiring must run P3–P9 first.

---

## 4. Executor

### 4.1–4.3 Ownership / submits / rules

Без изменения: process-global TPE; isolated vs mixed submits; no test reset; no cancel Accepted; no wait=True on PTB loop / after deadline; no process-exit promise.

### 4.4 EX1 — закрыто

**Решение:** P9 (executor shutdown) **допускается после partial sender failure** только если **все** доказаны:

1. admission SEALED;
2. PTB producers complete (truthful);
3. Accepted Futures empty + continuation empty;
4. WE stop owner-session terminal success (`stop_done` / all joins);
5. registry wait terminal success (all STARTED joined / freeze held);
6. sender phase **already attempted** and returned structured result (even if not STOPPED).

Если (2)–(5) не выполнены — **P9 запрещён**; executor threads остаются в remainder; `overall_ok=False`.

Partial sender (например HTTP_STOPPED / LOOP_STOPPING) **не** блокирует P9 при (1)–(6). Sender refuse (foreign proof) / registry still alive / WE partial without owner-session → P9 skip.

`overall_ok` всё равно False, пока sender не `full_resource_stopped` и executor не terminal.

---

## 5. Shutdown deadline и P10

### 5.1 Когда начинается deadline

`shutdown_deadline = monotonic() + timeout` фиксируется **в момент старта owner shutdown-session** (§1.1):

- при `request_antares_stop`, **или**
- при первом cancel/error after OPEN, который армает сессию.

Не начинать этот deadline на boot/initialize. Startup-failure path **не** использует shutdown-session deadline для drain (там только staged cleanup).

### 5.2 Что deadline покрывает

| Покрывает | Не покрывает / не заявлять |
|-----------|----------------------------|
| P3–P9 waits с remaining budget | существование `_await_cleanup` без timeout |
| отказ начинать **новые** destructive mutations после expiry | гарантированное завершение shielded PTB cleanup |
| structured failure+remainder | bounded process exit |

**Запрещено:** рекламировать «один общий ограниченный deadline», который сверху обрезает неограниченный `await` `_await_cleanup`.

### 5.3 После expiry

1. Не стартовать новые destructive фазы (новый WE owner-session, новый registry freeze, новый sender loop.stop, executor `wait=True`).
2. Уже запрошенные owner-sessions **наблюдать** до их native terminal/ack, в пределах documented slice semantics (sender owner HTTP; будущий WE owner-session).
3. Accepted / continuation **не** cancel.
4. PTB producers still live → session failure; **не** ставить `producers_complete=True`; **не** WE/registry/sender intake seal.
5. P10: **разрешён** best-effort `_await_cleanup` после попытки P3–P9 (или сразу на startup path). Если cleanup «завис» — это leftover/`cleanup_errors`/remainder; overall deadline **не** считается нарушенным «таймаутом cleanup», потому что cleanup вне budget; зафиксировать hung cleanup как resource leftover.

### 5.4 PTB cleanup при живых producers

Post-OPEN: **сначала** исчерпать/провалить P3–P9 с remainder; **потом** P10. Не закрывать Application HTTP «поверх» живых producers как способ drain. Startup path: producers ещё не OPEN → staged cleanup ok.

---

## 6. Cancel / repeat / partial (сводка)

### 6.1 Cancel before OPEN

Startup failure path §8: seal if needed; staged cleanup; preserve primary; no P3–P9.

### 6.2 Cancel after OPEN (в т.ч. Accepted Future жив, stop ещё не set)

1. Arm owner shutdown-session (если ещё нет): seal + `stop.set()` (эквивалент request stop).
2. Fix deadline.
3. Продолжить P3–P9; **не** прыгать на P10.
4. Accepted Future / continuation **не** cancel.
5. Отменённый lifecycle waiter: detach; session continues; при финальном raise сохранить исходный `CancelledError` как primary (cleanup/session errors attached), не терять его.

### 6.3 Repeat

Join existing owner-session / read terminal. WE: observe owner-session — **не** вызывать текущий success-only/`dead worker` path как «partial retry». Sender/registry: как TASK-48/43.

### 6.4 Partial WE (текущий API)

Сценарий: sentinel accepted, worker finished, waiter cancelled before `_profile_workers_stop_done`, then repeat → current code may refuse `worker is dead`. Контракт: это **known limitation**; remainder; future WE owner-session must make repeat observe joins without re-demanding alive threads. Не выдавать `_profile_workers_stop_done` fast-path за partial retry.

---

## 7. Result model

| Field | Meaning |
|-------|---------|
| `business_outcomes` | job/AE/registry business errors |
| `work_drained` | Accepted + continuation + WE items terminal |
| `resources_stopped` | WE session terminal, registry joined, sender full_resource, executor terminal |
| `ptb_cleanup` | actions / leftover / errors / hung? |
| `overall_ok` | all mandatory post-OPEN phases succeeded |
| `remainder` | sealed?; futures; WE partial?; registry; sender; executor; PTB leftover |
| `primary` | original CancelledError / Exception preserved |

`sender.full_resource_stopped` alone ≠ `overall_ok`.

---

## 8. Startup failure vs post-OPEN

| Path | Behavior |
|------|----------|
| Reject before initialize | seal if admission; raise; no false cleanup success |
| Fail initialize/start (**before OPEN**) | primary; seal; staged `_cleanup_application`; **no** P3–P9 |
| Cancel/error **after OPEN** | shutdown-session P3–P9 then P10; no skip |
| Graceful request stop | P1–P10 |

---

## 9. Матрица Orc*

| ID | Scenario | Expectation |
|----|----------|-------------|
| Orc1 | late AE worker after seal | final WE list includes worker; join (D24) |
| Orc2 | registry warning after WE task_done | sender after registry join (D30) |
| Orc3 | Accepted Future queued in TPE | no WE freeze yet |
| Orc4 | dead WE worker before get | failure + remainder |
| Orc5 | deadline mid-Accepted wait | failure; Futures not cancelled |
| Orc6 | cancel orchestration waiter then repeat | join same session; no duplicate sentinel/HTTP/loop.stop |
| Orc7 | partial sender then new budget | continue; no second successful Bot.shutdown |
| Orc8 | only test executor reset | production refuse until real API |
| Orc9 | initialize failure | staged PTB cleanup; no sender stop |
| Orc10 | `producers_complete=False` | WE/registry refuse |
| Orc11 | false producers_complete while handler putting | forbidden |
| Orc12 | sender STOPPED, executor alive | overall_ok=False |
| Orc13 | mixed dispatch_job in-process | refuse/remainder (O10) |
| Orc14 | deadline before sender loop.stop | deadline_before_loop_stop |
| Orc15 | registry freeze while WE unfinished>0 | refuse |
| Orc16 | admission OPEN, Accepted Future active, stop unset, lifecycle caller cancelled | arm shutdown-session (seal+set); **do not** jump to P10; Future not cancelled; run P3–P9 |
| Orc17 | deadline while PTB producer still live | producers incomplete; no WE/registry/sender intake; remainder; then P10 best-effort |
| Orc18 | `_await_cleanup` hung / no timeout | leftover/hung recorded; overall deadline **not** claimed to cut shielded cleanup |
| Orc19 | WE: sentinel put, worker exited, waiter cancelled before stop_done, then repeat on **current** API | may refuse dead worker; remainder; **not** success via stop_done fast-path |
| Orc20 | same as Orc19 after WE owner-session API | repeat observes session; no re-require alive; no duplicate sentinel |

---

## 10. Code slices (не автостарт)

Owner-session / cancel / result model — **в ранних slices**, не только в 49.E.

| Slice | Content | Depends on | Gate |
|-------|---------|------------|------|
| **49.S** | Shutdown-session primitive: arm on request-stop **and** cancel-after-OPEN; waiter detach; primary CancelledError; structured skeleton result | TASK-24 helper shape | **before** any P3–P9 wiring |
| **49.A** | Q-PTB1 producer-wait primitive + truthful producers_complete | 49.S | **blocks** 49.B+ until accepted |
| **49.B** | Wire P5–P7; WE **owner-session** observe (may extend TASK-41 API in **that** future code PR, not this docs PR) | 49.A accepted; TASK-40/41/43 | no wiring with false producers |
| **49.C** | P8 sender + proof plumbing from `AntaresBootPrefix.sender_ownership` | 49.B; TASK-48 | |
| **49.D** | Production executor shutdown + EX1 | 49.C | |
| **49.E** | Full Orc* suite + result polish | 49.S–D | |

Do **not** start automatically.

---

## 11. Open / closed questions

| ID | Status | Decision / remainder |
|----|--------|----------------------|
| **EX1** | **CLOSED** | P9 after partial sender only if SEALED + producers + Accepted/continuation empty + WE session success + registry success + sender attempted (§4.4) |
| Q-PTB1 | OPEN (design gate) | concrete producer-wait API; **no dependent wiring** until accepted |
| Q-EX2 | OPEN | public name of production executor shutdown |
| Q-OWN1 | OPEN (narrowed) | holder exists: `AntaresBootPrefix.sender_ownership`; open = how helper receives proof |
| Q-HLP1 | OPEN | orchestrator inside helper vs sibling |
| O10 | OPEN | mixed-stop |

---

## 12. Out of scope

Runtime/tests/requirements этого PR; helper wiring; serve/`enable_polling=True`; mixed-stop O10; merge/Ready/deploy/Railway; live Telegram; изменение исходного Test; Move agent root; повторное закрытие TASK-39–48; автостарт slices; заявление «глобальный graceful готов»; изменение runtime TASK-41 в этом PR.
