# Контракт общей остановки isolated Antares (TASK-49)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт **принят** GPT ACCEPTED `237b20efeb2aed76d24f620a9a1cc2110145344c`; docs-close на Draft PR #52 @ `e331c677…`; slice **49.S** GPT ACCEPTED `f7dd672…` / docs-close Draft PR #53; slice **49.A** GPT ACCEPTED `2f1435b…` / docs-close Draft PR #54 (`ptb_producer_wait`; Q-PTB1 accepted supported-mode; **не** wired); production wiring **нет**; полный shutdown **не** реализован |
| **База** | TASK-48 docs-close `90cda7c92e56df3657c293f2e6de6ee65d2426c0` (accepted runtime `7f6b5a8c211658fba92e2f6b98320b3443935cb6`, Draft PR #51) |
| **Принятый docs SHA** | `237b20efeb2aed76d24f620a9a1cc2110145344c` |
| **Обследованный SHA** | `90cda7c…` (+ worker stop API на том же дереве); stop APIs приняты на `7f6b5a8…` |
| **PR** | Draft [#52](https://github.com/deniskotdavydov1991-wq/Test/pull/52) |
| **Проверка** | GPT — документы/diff + соответствие обследованным API; pytest GPT не запускал (docs-only); CI PASS не заявлять (runs/statuses не найдены) |
| **Открытые gates** | Q-EX2, Q-OWN1, Q-HLP1, Q-REC1, O10 |
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
11. Не заявлять, что overall shutdown deadline ограничивает существующий `_await_cleanup` (shielded, без timeout) (§5 / §5.5).
12. Истечение shutdown deadline **не** доказательство `producers_complete` и **не** разрешение на полный PTB Application cleanup (§5.4).
13. Живой / незавершённый cleanup **не** называть остановленным ресурсом; без terminal cleanup нет SESSION_TERMINAL (§5.5 / §7).
14. O10 / serve-polling / deploy / live Telegram — **не** этот контракт.
15. `delayed_cleanup` — документированное исключение.
16. Staged PTB cleanup **startup failure** (до OPEN) сохранить отдельным путём; PTB Application Bot ≠ module sender Bot.
17. Модель owner-session / ошибок / structured result **обязана** быть принята **до** wiring (§10); нельзя откладывать целиком в 49.E.
18. Q-PTB1 — supported-mode producer-wait primitive **принят** (`2f1435b…` / PR #54); зависимый wiring P4+/P5+ **обязан** соблюдать условия install/seal/binding/entry-gate и сверять реальный lifecycle graph (не заявлять полный shutdown).
19. После SESSION_TERMINAL (success или failure) повтор **не** стартует новую destructive session и **не** выдаёт новый shutdown deadline (§1.3).

---

## 1. Owner shutdown-сессия (нормативная модель)

### 1.1 Владелец

**Owner shutdown-session** — одна process-local сессия graceful stop для bound admission + lifecycle helper.

| Поле (семантика) | Смысл |
|------------------|--------|
| Owner | orchestration слой (будущий; внутри/рядом с `run_ptb_lifecycle`) |
| Started when | первое из: успешный `request_antares_stop` **или** cancel/error **после** `admission.open()` при ещё не завершённом graceful |
| Deadline | absolute monotonic, фиксируется **один раз** в момент start сессии; **не** обновляется на repeat |
| Waiters | любые `await` наблюдатели; cancel waiter **не** отменяет сессию |
| Owner loop / tasks | после detach waiter **owner loop и session Tasks остаются живыми**, пока сессия не SESSION_TERMINAL (или процесс не убит) |
| Snapshot | промежуточная публикация состояния (drain deadline / cleanup observe exceeded); **не** terminal |
| Terminal | `SESSION_TERMINAL` structured result **только** после завершения cleanup path (full P10 done **или** startup staged cleanup done). Пока cleanup Task жива или full P10 не стартовал при живых producers — session **incomplete**; доступны snapshots, не terminal (§5.4–5.5) |

Caller lifecycle Task может быть отменён; сессия продолжает фазы на owner loop / выделенных Tasks. Исходный `CancelledError` **сохраняется** как `primary` (или в `caller_cancels`), но **не** заменяет обязанность вести сессию.

### 1.2 Два пути до cleanup

| Путь | Условие | Допустимые шаги |
|------|---------|-----------------|
| **Startup failure** | ошибка/reject **до** `admission.open()` (в т.ч. до/во время initialize/start; polling/unsupported reject) | seal (если admission); staged `_cleanup_application`; **без** P3–P9 resource drain; **без** sender/executor stop claim |
| **Post-OPEN shutdown** | `admission` уже OPEN **или** уже SEALED после request stop; stop Event set **или** cancel/error во время `stop.wait` / running body | **обязаны** P3–P9 (насколько предусловия позволяют); полный P10 **только** при доказанном `producers_complete` (§5.4); **запрещён** обход P3–P9 прямым P10 |

Текущий код helper на cancel после OPEN делает seal + `_await_cleanup` без drain — это **дефект относительно контракта**; future wiring обязан заменить поведение на post-OPEN путь.

### 1.3 Повторный stop

Пока сессия **жива** (не `SESSION_TERMINAL`): повторный `request_antares_stop` / await **присоединяется** к той же session; **не** выдаёт новый deadline; **не** стартует вторую destructive session.

После `SESSION_TERMINAL` (success **или** failure): повтор **только читает** сохранённый terminal result. **Не** новый deadline. **Не** новая destructive orchestration-session. Возобновление всей orchestration после terminal failure **не** обещано без отдельного принятого recovery-контракта.

Самостоятельный `stop_isolated_sender` с новым budget (TASK-48) — **не** то же самое, что recovery orchestration; см. Orc7.

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
| P8 | Sender | P7 | `stop_isolated_sender(proof, remaining)` | STOPPED+full_resource **или** structured sender remainder | partial repeatable **на sender API**; no new loop.stop after deadline; orchestration after SESSION_TERMINAL — §1.3 | P9 if EX1 allows else mark P9 skipped+remainder → P10 gate |
| P9 | Executor | EX1 preconditions (§4.4) | production shutdown observe | threads terminal **или** remainder | no test reset; no wait=True after deadline | P10 gate (§5.4) |
| P10 | PTB cleanup | **full:** truthful `producers_complete` **или** startup path; иначе full cleanup **запрещён** | start cleanup Task via `_await_cleanup` only if gate ok; else leave Application HTTP open | cleanup Task done → actions/leftover/errors; observe budget exceeded → **snapshot only** (§5.5) | overall deadline **не** режет shielded cleanup; hung ≠ stopped resource; no SESSION_TERMINAL while cleanup Task alive | SESSION_TERMINAL **или** incomplete+snapshot |

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

`shutdown_deadline = monotonic() + timeout` фиксируется **один раз** **в момент старта owner shutdown-session** (§1.1):

- при `request_antares_stop`, **или**
- при первом cancel/error after OPEN, который армает сессию.

Не начинать этот deadline на boot/initialize. Startup-failure path **не** использует shutdown-session deadline для drain (там только staged cleanup). Repeat **не** обновляет deadline.

### 5.2 Что deadline покрывает

| Покрывает | Не покрывает / не заявлять |
|-----------|----------------------------|
| P3–P9 waits с remaining budget | существование `_await_cleanup` без timeout |
| отказ начинать **новые** destructive mutations после expiry | гарантированное завершение shielded PTB cleanup |
| drain-phase failure / snapshot | bounded process exit |
| | `producers_complete` (expiry ≠ proof) |

**Запрещено:** рекламировать «один общий ограниченный deadline», который сверху обрезает неограниченный `await` `_await_cleanup`.

### 5.3 После expiry (drain budget)

1. Не стартовать новые destructive фазы (новый WE owner-session, новый registry freeze, новый sender loop.stop, executor `wait=True`).
2. Уже запрошенные owner-sessions **наблюдать** до их native terminal/ack, в пределах documented slice semantics (sender owner HTTP; будущий WE owner-session).
3. Accepted / continuation **не** cancel.
4. PTB producers still live → **§5.4** (не full P10; Application HTTP open; snapshot/remainder).
5. Если `producers_complete` уже доказан и P3–P9 исчерпаны → P10 gate (§5.4) / cleanup observer (§5.5).

### 5.4 Полный PTB cleanup vs живые producers

| | |
|--|--|
| **Доказательство для полного P10** | truthful `producers_complete` (принятый Q-PTB1 primitive) **или** startup-failure path (admission никогда не OPEN) |
| **Без доказательства** | полный `_cleanup_application` / закрытие Application HTTP / stop Application Bot **запрещены**. Допустимо: seal admission (если ещё нет), stop update intake (P3), отказ WE/registry/sender intake, remainder fields, snapshots |
| **Application HTTP** | остаётся **открытым**, пока нет доказательства §5.4 для полного cleanup |
| **Deadline + producers live** | опубликовать **drain_deadline_snapshot** (не SESSION_TERMINAL): `producers_incomplete=true`, `ptb_cleanup=not_started`, `application_http=open`, `overall_ok` недоступен как success; session **остаётся incomplete**; waiters видят snapshot; owner loop продолжает наблюдать producers |
| **Поздний full cleanup** | когда та же **ещё не terminal** session позже получит `producers_complete`, она продолжает P5–P9 (remaining/native semantics) затем P10; владелец — **та же** owner shutdown-session; **без** нового deadline. Если сессию уже закрыли как `SESSION_TERMINAL` drain-failure без cleanup — поздний full cleanup **не** обещан без отдельного recovery-контракта |
| **Startup** | отдельный путь: staged cleanup без P3–P9 |

**Запрещено:** использовать expiry deadline как `producers_complete=True` или как право закрыть Application HTTP «поверх» живых producers.

### 5.5 Cleanup observer, snapshot и SESSION_TERMINAL (согласование Orc18)

Существующий `_await_cleanup` — shielded, **без timeout**. Он **не может** одновременно бесконечно ждать и вернуть `SESSION_TERMINAL` с «hung cleanup completed».

**Выбранная модель:**

1. **Кто наблюдает:** owner shutdown-session держит **cleanup Task** на owner loop (`_await_cleanup` / `_cleanup_application`). Waiters **не** владеют cleanup.
2. **Cleanup observe budget:** отдельный monotonic budget `cleanup_observe_deadline`, стартует когда full P10 **начат** (после gate §5.4). Это **не** overall shutdown deadline и **не** timeout внутри `_await_cleanup` (shield не режется).
3. Пока cleanup Task **жива** и observe budget **не** истёк: waiters могут получать snapshot `ptb_cleanup.status=in_progress`; session **incomplete**; **terminal report недоступен**.
4. Если observe budget истёк, а cleanup Task ещё жива: опубликовать snapshot `ptb_cleanup.status=in_progress_observe_exceeded`, `resources_stopped` **не** включает PTB Application; session **остаётся незавершённой**; `SESSION_TERMINAL` **нет**. Не называть cleanup завершённым ресурсом.
5. Когда cleanup Task завершается (успех/ошибки/leftover actions): session публикует `SESSION_TERMINAL` с финальным `ptb_cleanup`; поздние waiters читают terminal. Primary `CancelledError` сохраняется при raise waiter-ам.
6. Bounded process exit **не** требуется и **не** обещается.

Без отдельного observer (текущий helper до 49.S): контрактно считать session **incomplete**, terminal report **недоступен**, пока shielded cleanup не вернулся — не выдумывать hung-as-terminal.

---

## 6. Cancel / repeat / partial (сводка)

### 6.1 Cancel before OPEN

Startup failure path §8: seal if needed; staged cleanup; preserve primary; no P3–P9.

### 6.2 Cancel after OPEN (в т.ч. Accepted Future жив, stop ещё не set)

1. Arm owner shutdown-session (если ещё нет): seal + `stop.set()` (эквивалент request stop).
2. Fix deadline **один раз**.
3. Продолжить P3–P9; **не** прыгать на full P10 без §5.4.
4. Accepted Future / continuation **не** cancel.
5. Отменённый lifecycle waiter: detach; owner loop + session Tasks живут; при eventual terminal raise сохранить исходный `CancelledError` как primary.

### 6.3 Repeat

| Состояние session | Поведение |
|-------------------|-----------|
| Жива (не SESSION_TERMINAL) | join / observe; **тот же** deadline; нет второй destructive session |
| SESSION_TERMINAL | только read terminal; нет нового deadline; нет новой orchestration destructive session |

WE: observe owner-session — **не** success-only/`dead worker` как «partial retry». Sender API repeat с новым budget = **TASK-48** (Orc7), не orchestration recovery. Полный restart orchestration после terminal failure — только отдельный recovery-контракт.

### 6.4 Partial WE (текущий API)

Сценарий: sentinel accepted, worker finished, waiter cancelled before `_profile_workers_stop_done`, then repeat → current code may refuse `worker is dead`. Контракт: это **known limitation**; remainder; future WE owner-session must make repeat observe joins without re-demanding alive threads. Не выдавать `_profile_workers_stop_done` fast-path за partial retry.

---

## 7. Result model

| Field | Meaning |
|-------|---------|
| `session_state` | `RUNNING` / `DRAIN_SNAPSHOT` / `CLEANUP_IN_PROGRESS` / `SESSION_TERMINAL` |
| `business_outcomes` | job/AE/registry business errors |
| `work_drained` | Accepted + continuation + WE items terminal |
| `resources_stopped` | только **доказанно** stopped (WE session, registry, sender full_resource, executor); **живой cleanup сюда не входит** |
| `ptb_cleanup` | `not_started` / `in_progress` / `in_progress_observe_exceeded` / `done`(+actions/leftover/errors) |
| `application_http` | `open` / `cleanup_done` / `startup_cleaned` |
| `overall_ok` | Meaningful **только** при `SESSION_TERMINAL`; иначе unset/false-unavailable |
| `remainder` | sealed?; futures; producers_incomplete?; WE partial?; registry; sender; executor; cleanup incomplete? |
| `primary` | original CancelledError / Exception preserved |
| `snapshot_vs_terminal` | snapshot ≠ terminal; terminal недоступен while cleanup Task alive without completion |

`sender.full_resource_stopped` alone ≠ `overall_ok`. Hung/in-progress cleanup ≠ stopped resource.

---

## 8. Startup failure vs post-OPEN

| Path | Behavior |
|------|----------|
| Reject before initialize | seal if admission; raise; no false cleanup success |
| Fail initialize/start (**before OPEN**) | primary; seal; staged `_cleanup_application`; **no** P3–P9 |
| Cancel/error **after OPEN** | shutdown-session P3–P9; full P10 only with §5.4 proof |
| Graceful request stop | P1–P9 then P10 gate |
| Deadline, producers live | §5.4 snapshot; Application HTTP open; no full P10 |

---

## 9. Матрица Orc*

| ID | Scenario | Expectation |
|----|----------|-------------|
| Orc1 | late AE worker after seal | final WE list includes worker; join (D24) |
| Orc2 | registry warning after WE task_done | sender after registry join (D30) |
| Orc3 | Accepted Future queued in TPE | no WE freeze yet |
| Orc4 | dead WE worker before get | failure + remainder |
| Orc5 | deadline mid-Accepted wait | failure/snapshot; Futures not cancelled |
| Orc6 | cancel orchestration waiter then repeat | join same session; same deadline; no duplicate sentinel/HTTP/loop.stop |
| Orc7 | **TASK-48 sender API:** partial sender then new budget on `stop_isolated_sender` | sender continues; no second successful Bot.shutdown — **не** orchestration recovery после SESSION_TERMINAL |
| Orc8 | only test executor reset | production refuse until real API |
| Orc9 | initialize failure | staged PTB cleanup; no sender stop |
| Orc10 | `producers_complete=False` | WE/registry refuse |
| Orc11 | false producers_complete while handler putting | forbidden |
| Orc12 | sender STOPPED, executor alive | overall_ok=False at SESSION_TERMINAL |
| Orc13 | mixed dispatch_job in-process | refuse/remainder (O10) |
| Orc14 | deadline before sender loop.stop | deadline_before_loop_stop |
| Orc15 | registry freeze while WE unfinished>0 | refuse |
| Orc16 | admission OPEN, Accepted Future active, stop unset, lifecycle caller cancelled | arm shutdown-session (seal+set); **do not** jump to full P10; Future not cancelled; run P3–P9 |
| Orc17 | deadline while PTB producer still live | **no** `producers_complete`; **no** full P10; Application HTTP **open**; `drain_deadline_snapshot`; session incomplete; late full cleanup only if same non-terminal session later proves producers (§5.4) |
| Orc18 | `_await_cleanup` still running past cleanup observe budget | snapshot `in_progress_observe_exceeded`; **no** SESSION_TERMINAL; cleanup **not** in `resources_stopped`; owner loop+Task stay alive; late completion → then SESSION_TERMINAL |
| Orc19 | WE: sentinel put, worker exited, waiter cancelled before stop_done, then repeat on **current** API | may refuse dead worker; remainder; **not** success via stop_done fast-path |
| Orc20 | same as Orc19 after WE owner-session API | repeat observes session; no re-require alive; no duplicate sentinel |
| Orc21 | repeat stop/await after orchestration SESSION_TERMINAL failure | read terminal only; **no** new deadline; **no** new destructive orchestration session |
| Orc22 | producers later complete while session still non-terminal after Orc17 snapshot | same session resumes P5+ then full P10; no new deadline |

---

## 10. Code slices (не автостарт)

Owner-session / cancel / result / cleanup-observer model — **в 49.S**, не только в 49.E.

| Slice | Content | Depends on | Gate |
|-------|---------|------------|------|
| **49.S** | Shutdown-session primitive: arm on request-stop **and** cancel-after-OPEN; waiter detach; primary CancelledError; snapshot vs SESSION_TERMINAL; cleanup observer + observe budget; structured skeleton result — **ACCEPTED** `f7dd672…` / Draft PR #53 (**не** wired) | TASK-24 helper shape | **before** any P3–P9 wiring |
| **49.A** | Q-PTB1 producer-wait primitive + truthful producers_complete — **ACCEPTED** `2f1435b…` / Draft PR #54 (supported mode; **не** wired; lifecycle graph must match install/seal/binding/entry-gate conditions at wiring) | 49.S | **unblocks** 49.B planning; wiring still future |
| **49.B** | P5–P7 drain API + WE owner-session — **GPT ACCEPTED** implemented scope `e02314a…` / Test `5b62d6b…` (Draft PR #55); **TASK remains open**: integration blocker (default Queue; no pre-init host; `run_ptb_lifecycle` does not call P4–P7); wire order pre-init→session→P4–P7→P8→P9→cleanup; **не** docs-closed; **не** full shutdown | 49.A accepted; TASK-40/41/43 | clear integration blocker before claiming lifecycle wire |
| **49.C** | P8 sender + proof plumbing — **CODE in review** (Draft PR base 49.B; `run_owner_drain_p4_to_p8` + explicit `sender_ownership_proof`; Q-OWN1 closed for proof **argument** only; entry-path boot wiring deferred; **не** P9; **не** lifecycle wire) | 49.B implemented ACCEPTED; TASK-48 | keep P9 before cleanup; do not bypass via seal→cleanup |
| **49.D** | Production executor shutdown + EX1 | 49.C | |
| **49.E** | Full Orc* suite + result polish | 49.S–D | |

Do **not** start automatically.

---

## 11. Open / closed questions

| ID | Status | Decision / remainder |
|----|--------|----------------------|
| **EX1** | **CLOSED** | P9 after partial sender only if SEALED + producers + Accepted/continuation empty + WE session success + registry success + sender attempted (§4.4) |
| Q-PTB1 | **ACCEPTED** (supported-mode primitive @ `2f1435b…` / Test SHA `6ab07757…`; Draft PR #54) | `ptb_producer_wait` + intake seal; wiring must keep: single pre-initialize install; Antares intake + supported PTB graph; no untracked background producers; Application/issuer binding; permanent post-COMPLETE entry refuse. Lifecycle **not** wired; full shutdown / production readiness **not** claimed |
| Q-EX2 | OPEN | public name of production executor shutdown |
| Q-OWN1 | **NARROWED / partial close (49.C)** | Orchestrator receives proof via explicit `sender_ownership_proof=` (exact identity; validate before side effects/cache). Holder remains `AntaresBootPrefix.sender_ownership` / `claim_antares_sender_ownership`. **Deferred:** injecting proof from real entry/`_boot_prefix` into lifecycle (blocked with 49.B integration gap) |
| Q-HLP1 | OPEN | orchestrator inside helper vs sibling |
| Q-REC1 | OPEN | recovery / new session after SESSION_TERMINAL failure — **not** in this contract |
| O10 | OPEN | mixed-stop |

---

## 12. Out of scope

Runtime/tests/requirements этого PR; helper wiring; serve/`enable_polling=True`; mixed-stop O10; merge/Ready/deploy/Railway; live Telegram; изменение исходного Test; Move agent root; повторное закрытие TASK-39–48; автостарт slices; заявление «глобальный graceful готов»; изменение runtime TASK-41 в этом PR; bounded process exit; orchestration recovery после SESSION_TERMINAL без Q-REC1.
