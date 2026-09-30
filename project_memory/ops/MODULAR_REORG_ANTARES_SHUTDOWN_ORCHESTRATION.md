# Контракт общей остановки isolated Antares (TASK-49)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт **подготовлен** (ожидание GPT review); runtime **нет**; helper **не** wired; merge/deploy нет; TASK-49 **не** закрыт |
| **База** | TASK-48 docs-close `90cda7c92e56df3657c293f2e6de6ee65d2426c0` (accepted runtime `7f6b5a8c211658fba92e2f6b98320b3443935cb6`, Draft PR #51) |
| **Обследованный SHA** | `90cda7c…` (docs-close = worktree HEAD); stop APIs приняты на `7f6b5a8…` |
| **PTB helper** | [STARTSTOP.md](MODULAR_REORG_ANTARES_STARTSTOP.md) / `modules.antares.application_lifecycle.run_ptb_lifecycle` |
| **Drain/stop** | [DRAIN_STOP.md](MODULAR_REORG_ANTARES_DRAIN_STOP.md) TASK-39 |
| **WE stop** | TASK-41 `stop_isolated_profile_workers` |
| **Registry** | [REGISTRY_DAEMON.md](MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md) TASK-42/43 `wait_isolated_registry_daemon_ops` |
| **Sender** | [SENDER_DRAIN_STOP.md](MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md) / [SENDER_GATES.md](MODULAR_REORG_ANTARES_SENDER_GATES.md) TASK-44–48 `stop_isolated_sender` |
| **Mixed gate** | [TASK-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |
| **O10** | mixed-stop **открыт** |

Цель: соединить **принятые** stop-механизмы с `run_ptb_lifecycle` в один проверяемый orchestration-контракт. Это **не** реализация, **не** wiring helper, **не** полный выпуск, **не** mixed-stop.

На `90cda7c…` helper после `stop.wait()` делает только defensive `seal()` и PTB `_cleanup_application`. Work drain / WE / registry / sender / executor **не** вызываются (явно покрыто тестами helper). TASK-49 фиксирует **что** должно появиться в будущих code slices.

`Sender STOPPED` (TASK-48) **сам по себе** ≠ общий graceful Antares success.

---

## 0. Жёсткие правила

1. `request_antares_stop`: **`admission.seal()` затем `stop.set()`** на owner loop (`modules.antares.work_admission.request_antares_stop`). Уже Accepted work **продолжает** выполняться.
2. `stop.set()` / отсутствие polling / пустая Queue **не** доказывают конец PTB producers.
3. Не freeze registry, пока WE ещё может создать `schedule_registry_append`.
4. Не stop sender intake, пока registry daemon ещё может `send_message_sync` (D30).
5. Не cancel Accepted Futures; не использовать `_reset_job_executor_for_tests` как production shutdown.
6. Не звать блокирующий `ThreadPoolExecutor.shutdown(wait=True)` на PTB loop и **не** после исчерпания overall deadline.
7. Не обещать ограниченное время `exit` процесса.
8. Один overall абсолютный monotonic deadline на весь orchestration call; фазам передавать **remaining**, не полный исходный timeout заново.
9. Cancel waiter / повторный stop **не** дублирует sentinel / HTTP close / `loop.stop`, если фаза уже запрошена корректно.
10. O10 / serve-polling cutover / deploy / live Telegram — **не** этот контракт.
11. `delayed_cleanup` остаётся документированным исключением (не ждать, не join, не remainder registry).
12. Существующий staged PTB cleanup при ошибках `initialize`/`start` **сохранить**; PTB Application Bot ≠ module sender Bot.

---

## 1. Обследованные surfaces @ `90cda7c…`

### 1.1 PTB helper — `modules/antares/application_lifecycle.py`

| API | Факт |
|-----|------|
| `run_ptb_lifecycle(app, *, stop, enable_polling=False, admission=None)` | initialize → start → `admission.open()` → `await stop.wait()` → seal → `_await_cleanup` |
| `_cleanup_application` | updater.stop (если running) → app.stop → app.shutdown / staged HTTP; shielded от caller cancel |
| Startup fail | primary exception сохраняется; seal + cleanup всё равно; leftover → failure |
| Drain wiring | **нет** |

### 1.2 WorkAdmission — `modules/antares/work_admission.py`

| API | Факт |
|-----|------|
| `request_antares_stop(stop, admission, *, loop=None)` | seal → set (same loop) или `call_soon_threadsafe(stop.set)` |
| `wait_accepted_executor_work()` | ждёт пустой `_accepted_executor_futures`; **не** полный drain |
| `submit_*_if_open` | OPEN-check + register Future under lock |
| Seal | не cancel Accepted/Queued; ≠ sender ownership proof |

### 1.3 WE stop — `automation/worker.py::stop_isolated_profile_workers`

Требует: bound admission, `producers_complete=True`, SEALED, нет Accepted Futures, нет AE continuation, `unfinished_tasks==0` → freeze → sentinel → `asyncio.to_thread(join)`.

`producers_complete` — **caller attestation**; код сам PTB callbacks не считает.

### 1.4 Registry — `integrations/wallet_editor_registry_async.py`

| API | Факт |
|-----|------|
| `RegistryDaemonLifecycle` | REGISTERED → STARTED → TERMINAL |
| `wait_isolated_registry_daemon_ops(..., producers_complete=True)` | те же producers/SEALED/Accepted/continuation + `_we_profiles_unfinished()==[]` → freeze → join STARTED |

D30: registry join **до** sender intake seal. Warnings из append идут в `send_message_sync`.

### 1.5 Sender — `integrations/telegram_bot.py`

| API | Факт |
|-----|------|
| Ownership | `claim_antares_sender_ownership` / `validate_antares_sender_ownership` |
| `drain_and_stop_sender_worker` | до `WORKER_STOPPED` |
| `stop_isolated_sender(proof, *, timeout=30)` | full path → `STOPPED`; один overall deadline; owner HTTP session владеет captured absolute deadline; `deadline_before_loop_stop` |

### 1.6 Job executor — `core/job_dispatch.py`

| Факт | Доказательство |
|------|----------------|
| Owner | process-global `_JOB_EXECUTOR` via `get_job_executor()` (`thread_name_prefix=job-worker`) |
| Isolated submits | `WorkAdmission.submit_job_if_open` / `submit_if_open` / `submit_auto_enable_run_if_open` |
| Mixed / bypass | `dispatch_job_background` / `dispatch_job_sync` / `dispatch_job_async` — **не** в admission registry |
| Production shutdown | **нет** |
| Test only | `_reset_job_executor_for_tests` → `shutdown(wait=False, cancel_futures=True)` — **запрещён** на Accepted path |

---

## 2. Выбранный порядок фаз (нормативный)

Владелец orchestration: будущий слой вокруг / внутри `run_ptb_lifecycle` после успешного `stop.wait()` (имя API code slice уточнит; семантика — нет). Caller владеет `Application`, loop, `stop` Event, `WorkAdmission`, sender ownership proof.

```text
0. Preconditions (bound admission, supported Application graph, claimed sender proof available)
1. request_antares_stop: seal() → stop.set()
2. await stop.wait()  # уже в helper; будит фазу остановки
3. Stop update intake (serve only; sandbox/polling=False → N/A)
4. Wait PTB producers (in-flight handlers/callbacks) — NEW truth, не голый bool
5. wait_accepted_executor_work + continuation empty + WE unfinished_tasks==0
6. stop_isolated_profile_workers(producers_complete=True)
7. wait_isolated_registry_daemon_ops(producers_complete=True)
8. stop_isolated_sender(ownership_proof, timeout=remaining)
9. Production executor shutdown (NEW API; не test reset)
10. PTB _cleanup_application (существующий staged cleanup)
```

### 2.1 Таблица фаз

| # | Фаза | Предусловия | Действие | Доказательство завершения | Deadline / cancel / error | Следующая |
|---|------|-------------|----------|---------------------------|---------------------------|-----------|
| P0 | Preconditions | isolated Antares process; admission bound or will bind; Application passes `unsupported_application_reasons` empty; sender ownership proof **claimed** for this process | observe only | checks pass | refuse orchestration start; no mutation | P1 |
| P1 | Request stop | P0 | `request_antares_stop` | SEALED + `stop.is_set()` | idempotent seal; set on owner loop | P2 |
| P2 | Wake helper | helper running | `await stop.wait()` returns | wait returned | cancel before wake → startup/running cleanup path only | P3 |
| P3 | Stop updates | serve+updater running **или** N/A | updater intake stop when applicable | no new updates admitted **или** N/A attested | failure → remainder; do not skip later resource rules blindly | P4 |
| P4 | PTB producers | SEALED | wait in-flight Application handlers / concurrent update work that can still `ensure_profile_*` / `put_nowait` | **attested** `producers_complete=True` backed by real wait, not empty queue / no polling alone | cancel wait ≠ cancel Accepted; deadline → failure+remainder; **запрещено** звать WE/registry stop с ложным True | P5 |
| P5 | Accepted + WE items | producers complete | `wait_accepted_executor_work`; continuation map empty; profile `unfinished_tasks==0` | registry Futures empty; no AE continuation; unfinished==0 | cancel wait ≠ cancel Futures; deadline → failure+remainder; WE workers may still be alive | P6 |
| P6 | WE resource stop | P5 + SEALED + producers True | `stop_isolated_profile_workers` | threads joined / frozen list terminal per TASK-41 | foreign admission refuse; deadline alive thread → failure; no duplicate sentinel on repeat success path | P7 |
| P7 | Registry join | P6 (WE unfinished already 0) | `wait_isolated_registry_daemon_ops` | STARTED joined/TERMINAL; freeze held | **не** freeze раньше P6; deadline alive daemon → failure (D29); no sender yet | P8 |
| P8 | Sender full stop | P7; ownership proof | `stop_isolated_sender(proof, timeout=remaining)` | `lifecycle_state=STOPPED` + `full_resource_stopped=True` **или** structured remainder | foreign refuse; partial HTTP/loop remain repeatable; no new `loop.stop` after exhausted deadline; sender STOPPED ≠ overall success alone | P9 |
| P9 | Executor shutdown | P8 success **or** documented partial policy (Q-EX1); Accepted empty | production shutdown API (future): stop **new** submits; join/observe worker threads without `cancel_futures` on Accepted | no new isolated submit; threads terminal **or** explicit remainder (no process-exit promise) | **запрет** `_reset_job_executor_for_tests`; **запрет** `wait=True` after deadline; **запрет** на PTB loop blocking join of TPE | P10 |
| P10 | PTB cleanup | always reachable after P2 (even on earlier failure — see §6) | existing `_cleanup_application` | updater/app stopped; HTTP leftovers closed or leftover reported | preserve primary exception; startup-fail cleanup path unchanged | terminal report |

### 2.2 Contradiction: TASK-39 §5 vs TASK-42/41

| Source | Stated order |
|--------|--------------|
| TASK-39 §5 (historical numbering) | Accepted drain → **registry join** → WE sentinel → sender |
| TASK-42 §5 + intended helper | **`stop_isolated_profile_workers` → `wait_isolated_registry_daemon_ops` → sender** |
| Code preconditions | Registry wait requires WE `unfinished_tasks==0`; WE stop API already joins workers |

**Resolution (accepted for TASK-49):** keep **WE stop → registry → sender**. Invariant D30 preserved. TASK-39 numbering is pre-TASK-41 API era; do **not** reintroduce registry freeze while WE can still `schedule_registry_append`. Do **not** rewrite TASK-39 history; this document supersedes helper order for orchestration.

### 2.3 Contradiction: helper code vs O2

| Source | Behavior |
|--------|----------|
| `run_ptb_lifecycle` @ `90cda7c…` | `stop.wait` → seal → PTB cleanup only |
| DRAIN_STOP O2 / this contract | insert phases P3–P9 before PTB cleanup |

**Resolution:** future code slice(s) **must** change helper (or a dedicated orchestrator called from it) to run P3–P9 before P10. This docs PR does **not** implement that.

---

## 3. PTB producers (обязательное уточнение)

### 3.1 Что не является proof

- `stop.is_set()`
- `enable_polling=False` / updater not running
- empty WE Queue / `qsize==0`
- `WorkAdmission` SEALED alone

### 3.2 Что требуется

После seal orchestration **обязан** дождаться in-flight PTB Application work, которое ещё может:

- скачать/разобрать update;
- вызвать isolated handlers / schedules / ingest paths;
- `_ensure_profile_worker` / `put_nowait_if_open`.

Точный wait primitive (Application handler tasks / update processor drain / explicit in-flight counter) — **открытый code-design point Q-PTB1**, но контракт запрещает передавать `producers_complete=True` без такого доказательства.

CancelledError PTB callback **не** cancel Accepted Future и **не** drain work (§ DRAIN_STOP 2.4).

---

## 4. Executor ownership и shutdown

### 4.1 Ownership

| Owner | `core.job_dispatch.get_job_executor()` process-global TPE |
|-------|----------------------------------------------------------|
| Not owners | `WorkAdmission`, PTB `Application`, sender loop |

### 4.2 Submit sources

| Path | Counted in Accepted registry? |
|------|-------------------------------|
| `WorkAdmission.submit_job_if_open` / `submit_if_open` / `submit_auto_enable_run_if_open` | **yes** (isolated) |
| `dispatch_job_background` / `sync` / `async` | **no** (mixed/bypass) |

Full graceful claim for isolated Antares assumes isolated-only submits after boot. If mixed `dispatch_job_*` ran in-process, admission wait **does not** prove TPE idle — orchestration must **fail-closed** or refuse mixed (O10 remains open).

### 4.3 Production shutdown rules

1. Only after sender phase attempted per policy (§2 + Q-EX1).
2. Forbid new isolated submits (admission already SEALED; also gate executor submit if needed).
3. Do **not** `cancel_futures=True` for Accepted work.
4. Do **not** call `_reset_job_executor_for_tests`.
5. Do **not** `shutdown(wait=True)` on the PTB asyncio loop thread.
6. Do **not** `shutdown(wait=True)` after overall deadline exhausted.
7. `shutdown(wait=False, cancel_futures=False)` alone does **not** prove threads stopped — need join/observe or explicit remainder.
8. No promise of bounded process exit.

---

## 5. Overall deadline

| Rule | Detail |
|------|--------|
| Clock | one `deadline = monotonic() + timeout` at orchestration entry |
| Propagation | each helper gets `timeout=max(0, deadline - monotonic())` or absolute where API already uses absolute |
| Sender | `stop_isolated_sender(..., timeout=remaining)`; owner HTTP session captures **its** absolute deadline at claim (TASK-48); no extra +30s |
| No re-budget | forbidding `timeout=30` default re-armed per phase as if fresh |
| Expired before new destructive phase | skip scheduling new mutations (mirror sender `deadline_before_loop_stop`); observe already-requested work |
| After deadline | no `wait=True` executor shutdown; no kill Accepted; report remainder |

---

## 6. Cancel, repeat, partial failure

### 6.1 Ownership of wait

Orchestration owns the waiter Tasks/Futures that observe slice APIs. Slice owners remain:

- WE stop / registry wait / sender session — as in TASK-41/43/48;
- PTB cleanup — `_cleanup_application` task (already cancel-shielded).

### 6.2 Repeat stop

| Already done | Repeat behavior |
|--------------|-----------------|
| SEALED | idempotent |
| WE stop success | TASK-41 idempotent path |
| Registry wait done | TASK-43 idempotent |
| Sender STOPPED + same proof | fast-path success |
| Sender partial HTTP/LOOP | continue/observe; no duplicate Bot.shutdown / loop.stop when already requested |

### 6.3 Cancel rules

- Cancel orchestration waiter **≠** cancel Accepted Futures, AE continuation, WE work, registry daemon, sender owner HTTP session.
- PTB cleanup still runs on helper exit paths (existing behavior) unless a future slice documents otherwise — **startup failure cleanup must remain**.

### 6.4 What may stay open on partial failure

| Living resource | Allowed? |
|-----------------|----------|
| Accepted Future still running | yes until drained or remainder |
| WE thread after failed join | yes → overall failure |
| Registry STARTED alive past deadline | yes → failure (D29) |
| Sender HTTP_STOPPED but loop live | yes → not overall success |
| Executor threads | yes → remainder; no pretend success |
| PTB Application partially cleaned | leftover fields required |

**Forbidden on partial with live producers:** claiming overall graceful success; freezing registry early; sealing sender intake before registry join; cancelling Accepted work to “finish faster”.

---

## 7. Result model (разделение)

Будущий structured result (имена code slice) **обязан** разделять:

| Field class | Meaning |
|-------------|---------|
| `business_outcomes` | job/AE/registry append business errors (visible separately) |
| `work_drained` | Accepted Futures + continuation + WE items terminal |
| `resources_stopped` | WE threads, registry daemons, sender `full_resource_stopped`, executor threads |
| `ptb_cleanup` | `PtbLifecycleResult` actions/leftover/errors |
| `overall_ok` | all mandatory phases succeeded |
| `remainder` | sealed?; futures; continuation; WE; registry; sender fields; executor; PTB leftover |

Rules:

- `work_drained=True` + living WE/registry/sender/executor ⇒ `overall_ok=False`.
- `sender.full_resource_stopped=True` alone ⇒ **not** sufficient for `overall_ok`.
- Delivery/intake D27/D28 ≠ sender resource failure (TASK-47/48).

---

## 8. Startup failure vs graceful stop

| Path | Behavior (keep) |
|------|-----------------|
| Reject before initialize (polling/unsupported) | seal if admission; raise; no false cleanup success |
| Fail initialize/start | primary preserved; seal; staged `_cleanup_application` |
| Graceful stop after open | P1–P10 |

PTB Application HTTP clients ≠ `integrations.telegram_bot` sender Bot graph. Do not call `application_lifecycle` close helpers on module sender Bot; do not call sender stop on Application bot.

---

## 9. Матрица будущих Event/barrier-тестов (orchestration)

Без sleep-as-proof. Реальные admission/executor/Queue/Future/registry/sender harnesses where already available. Helper wiring tests only after a code slice exists.

| ID | Scenario | Expectation |
|----|----------|-------------|
| Orc1 | late AE worker: seal, AE still queued, then `_ensure_profile_worker` | worker in final WE list; join required (D24) |
| Orc2 | registry warning `send_message_sync` after WE `task_done` | sender not stopped until registry joined (D30) |
| Orc3 | Accepted Future still queued in TPE | work drain incomplete; no WE freeze yet |
| Orc4 | dead WE worker before get | failure + remainder; no retry (D12/D23) |
| Orc5 | overall timeout mid-Accepted wait | failure; Futures not cancelled (D25) |
| Orc6 | cancel orchestration wait then repeat | no duplicate sentinel/HTTP/loop.stop; work continues |
| Orc7 | partial sender HTTP_STOPPED then repeat with new budget | continues to STOPPED; no second successful Bot.shutdown |
| Orc8 | executor ownership: only test reset available | production path refuse/fail-closed until real shutdown API |
| Orc9 | initialize failure | staged PTB cleanup; no sender stop claimed |
| Orc10 | `producers_complete=False` | WE/registry stop refuse |
| Orc11 | false `producers_complete=True` while handler still putting | **forbidden**; future test must catch missing producer wait |
| Orc12 | sender STOPPED but executor threads alive | `overall_ok=False` |
| Orc13 | mixed `dispatch_job_background` in-process | isolated graceful refuse or explicit remainder (O10 open) |
| Orc14 | deadline before sender loop.stop | `deadline_before_loop_stop`; HTTP_STOPPED remains |
| Orc15 | registry freeze attempted while WE unfinished>0 | refuse |

---

## 10. Разбиение будущих code slices (не автостарт)

| Slice | Content | Depends on |
|-------|---------|------------|
| **49.A** | PTB producer completion primitive + truthful `producers_complete` | TASK-24 helper |
| **49.B** | Wire P5–P7 into/after `run_ptb_lifecycle` (Accepted wait → WE stop → registry wait) | 49.A; TASK-40/41/43 |
| **49.C** | Wire P8 `stop_isolated_sender` with remaining deadline + ownership proof plumbing | 49.B; TASK-48 |
| **49.D** | Production executor shutdown API + P9 | 49.C; job_dispatch |
| **49.E** | Structured overall result + Orc* tests | 49.A–D |

Do **not** start these automatically from this docs PR.

---

## 11. Open questions (для GPT review; код не выбирает молча)

| ID | Topic | Constraint already fixed | Still open |
|----|-------|--------------------------|------------|
| Q-PTB1 | Exact PTB producer wait API | must be stronger than bool/empty queue | concrete primitive |
| Q-EX1 | Executor shutdown if sender partial-failed | no pretend overall success | whether P9 still attempts best-effort observe |
| Q-EX2 | Names of production executor shutdown | not test reset; no cancel Accepted | public function name |
| Q-OWN1 | Where sender ownership proof is stored for helper | claim side-effect-free; validate exact object | boot-time claim holder API |
| Q-HLP1 | Orchestrator inside `run_ptb_lifecycle` vs sibling function | order fixed | module layout |
| O10 | Mixed-stop | remains open | — |

---

## 12. Out of scope

Runtime/tests/requirements этого PR; helper wiring; serve/`enable_polling=True`; mixed-stop O10; merge/Ready/deploy/Railway; live Telegram; изменение работающего Test/mixed; повторное закрытие TASK-39–48; автостарт slices 49.A–E; заявление «глобальный graceful готов».
