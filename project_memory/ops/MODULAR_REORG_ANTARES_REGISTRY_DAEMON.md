# Контракт isolated Wallet Editor registry daemon drain/join (TASK-42)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт **принят** (`d0e73d5ba4bc465e2245d18c206cf6ad99ebf9b1`); close `041825f…`; code TASK-43 принят GPT на `01e0c55dc84b9e6be78ff20f6b1f5b58be017601`, **не выпущен**, helper не wired, **не** полный graceful; merge/deploy нет |
| **База** | закрытие TASK-41 `655e14aac8413fce44a9d3ece1be653441ac700a` (принятый review HEAD `6ae8f88de2d146e9a550ae750745214c7c36c136`, runtime `c5ad7022d6d15773ad46b593486d0e4a6e94efc5`, Draft PR #44) |
| **Обследованный SHA** | runtime `c5ad702…` / close `655e14a…` |
| **Drain/stop** | [MODULAR_REORG_ANTARES_DRAIN_STOP.md](MODULAR_REORG_ANTARES_DRAIN_STOP.md) TASK-39 принят `69ae53c…` |
| **WE stop** | TASK-41 принят `6ae8f88…` — `stop_isolated_profile_workers` |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Цель: учесть каждую **принятую** isolated daemon-операцию `we-registry-*`, дождаться её terminal (join) и запретить новую после конца producers. Это **не** полный graceful: sender, executor shutdown, helper, serve, mixed-stop **не** входят. Durable outbox **не** доказательство конца внешнего I/O.

Имена code PR (TASK-43): `RegistryDaemonLifecycle`, `wait_isolated_registry_daemon_ops`, `IsolatedRegistryDaemonCreateRejected`, `IsolatedRegistryDaemonStopError`. Runtime этого docs PR **нет**.

---

## 0. Жёсткие правила (из TASK-39, не заново)

1. Work drain ≠ resource shutdown. Бизнес-ошибка append (outbox `failed` / timeout / exception в `append_run_to_dropbox_registry`) **допустима** при успешном join.
2. Полный graceful **обязан** join уже запущенных `we-registry-*` **до** sender stop (D30, O4b). Без этого среза полный graceful **не** заявлять.
3. Durable pending / replay later **не** разрешение игнорировать живой daemon (D29).
4. `delayed_cleanup` **не** registry I/O (O4): не ждать, не join, не смешивать.
5. Cancel/timeout **ожидания** не убивает thread, не меняет outbox, не снимает незавершённую запись с реестра операций.
6. Deadline при **STARTED** и всё ещё **alive** thread → **failure** + remainder, без retry. Уже finished после успешного `start()` — **не** failure.
7. PTB loop **не** блокировать `Thread.join()`.
8. Mixed-stop **не** этот контракт.

---

## 1. Обследованные production paths (SHA `c5ad702…`)

### 1.1 Producer disable → daemon

Единственный production caller `prepare_registry_outbox_and_schedule`: `automation.worker._run_disable_task` **после** `send_text` / `send_document`, **до** `delayed_cleanup`.

Файл: `integrations/wallet_editor_registry_async.py`.

| Шаг | Поток | Когда terminal для **этого** шага | Учёт |
|-----|-------|-----------------------------------|------|
| `persist_durable_result_copy` | WE worker | синхронно до return prepare | тело WE item (`unfinished_tasks`) |
| `create_outbox_record` → `PENDING` + `_outbox_lock` | WE worker | синхронно | durable index `{STATE_DIR}`; **не** daemon join |
| `schedule_registry_append` | WE worker | запись видна **до** `Thread.start()`; успешный `start()` → STARTED; return **не** ждёт append | **daemon accounting** (этот контракт) |
| `_run` → `append_run_to_dropbox_registry` | daemon `we-registry-{run_id}` | wrapper `finally` → TERMINAL; join — resource | I/O; outbox `SYNCING`/`SYNCED`/`FAILED` — **бизнес** |
| `delayed_cleanup` | отдельный daemon | sleep + `os.remove` | **исключение O4**; не этот реестр |

`schedule_registry_append` **fire-and-forget**: thread `daemon=True`, имя `we-registry-{task.run_id}`. Process-local списка живых thread **нет**. Повторный `start` на каждый вызов; `sentinel_put`-аналога нет.

`add_wallet` / `edit_wallet` **не** вызывают prepare/schedule.

### 1.2 Owner внешнего I/O

`integrations.wallet_editor_registry.append_run_to_dropbox_registry`:

- never raises наружу (best-effort; ошибки в log + outbox);
- retries до `registry_timeout_seconds`; `time.sleep` между попытками;
- каждая попытка: `with wallet_editor_registry._lock:` → `_append_attempt` → `append_attempt_postgres`;
- outbox: `update_outbox_status` (`_outbox_lock` в async-модуле);
- может вызвать `send_message_sync` (`_send_slow_append_warning` / `_send_timeout_warning` / `_send_rev_conflict_warning` / `_send_missing_otlezka_warnings`) → sender S1–S3 **после** WE `task_done`.

`mirror_batch` из `_append_attempt` на SUCCESS **не** передаётся в `schedule_mirror_batch` на этом SHA. `schedule_mirror_batch` (`we-registry-mirror-{operation}`) существует в `wallet_editor_registry_db/mirror.py`, **production append его не стартует**.

### 1.3 Не daemon на этом SHA

| Путь | Поток | Почему не TASK-42 daemon |
|------|-------|--------------------------|
| `/registry_replay` → `replay_pending_outbox_records` | Accepted **job executor** (TASK-40) | **синхронный** `append_run_to_dropbox_registry` на том же Future; нового `we-registry-*` нет |
| AE `patch_enable_results_in_dropbox_registry` | WE auto-enable worker | sync под `_lock`; не `schedule_registry_append` |
| registry refresh job | job executor | не `we-registry-*` |
| `delayed_cleanup` | свой daemon | O4 |

После `wait_accepted_executor_work` replay I/O **этого** Future уже terminal. Это не join `we-registry-*`.

### 1.4 Locks

| Lock | Файл | Что защищает |
|------|------|----------------|
| `_outbox_lock` | `wallet_editor_registry_async.py` | JSON outbox index |
| `_lock` | `wallet_editor_registry.py` | одна попытка append/patch |
| нет | — | список живых `we-registry-*` (дырка этого среза) |

Join существующих thread **отсутствует**.

---

## 2. Lifecycle registry operation

**Владелец:** process-local модуль `wallet_editor_registry_async` (не `WorkAdmission`, не PTB Application). Isolated stop только если `bound_admission() is admission` — как TASK-41. Mixed/unbound: отказ, без freeze, без join чужих thread.

Durable outbox **не** этот реестр. Только `create_outbox_record` / `PENDING` без `start()` — **не** daemon operation.

Точные enum/имена — code PR. Минимальная семантика записи:

| Состояние | Смысл |
|-----------|--------|
| REGISTERED / starting | запись **уже видна** drain; `Thread.start()` ещё не успел |
| STARTED | успешный `Thread.start()`; поток принят; `is_alive()` может стать False, когда `_run` закончится |
| TERMINAL | resource-учёт закрыт: wrapper `finally` после работы **или** создание не стало STARTED (`start()` бросил) |

Обязательные свойства:

1. Operation видна в registry **до** `Thread.start()`.
2. Успешный `Thread.start()` атомарно/однозначно переводит запись в STARTED (под тем же lock, что и видимость).
3. Exception из `Thread.start()` **не** оставляет ложную live operation: запись не STARTED; её не ждут как in-flight daemon; accounting consistent (нет «висит REGISTERED навсегда»). Durable outbox может остаться pending/failed по бизнес-правилам — это **не** in-flight daemon.
4. Wrapper `_run` в `finally` отмечает TERMINAL **независимо** от business success/failure append/outbox.
5. TERMINAL не исчезает так, чтобы concurrent drain пропустил переход между snapshot и ожиданием.
6. Уже TERMINAL к началу wait обрабатывается без deadlock: join уже finished thread допустим и **success**.

**Reaping (выбрано):** бесконечную историю не хранить. Запись можно снять **только после** того, как drain (или сам terminal-учёт) **наблюдал** TERMINAL: join STARTED-thread **или** фиксация start-failure, так что операция больше не live. Снимать STARTED/`is_alive()==False` до этого наблюдения **запрещено** — иначе окно «started, но уже невидим drain». Имена/структура — code PR; точка reaping однозначна: **после наблюдения TERMINAL**, не по голому `is_alive()==False` до wait.

Очереди daemons нет. «Queued/not started» в тестах = REGISTERED или STARTED, `_run` ещё не вошёл в append (OS scheduling).

### 2.1 Already-finished STARTED thread — не failure

Зарегистрированная operation, чей thread **успешно started** и уже завершился **до начала drain**, — **normal terminal**. Join такого thread допустим и должен завершиться **success**. Сам факт `is_alive() == False` после успешного `start()` **не** shutdown failure. «Dead before observer» **не** failure.

Wait/join **failure** только при отдельно доказанном abnormal lifecycle:

- deadline истёк, а **STARTED** thread всё ещё **alive**;
- запись в невозможном/inconsistent состоянии (например REGISTERED зависла без `start()` и без terminal-учёта; STARTED без thread; accounting повреждён).

`Thread.start()` бросил: операция **не** STARTED, живого daemon нет, drain её **не** ждёт (R15). Это **не** shutdown failure wait и **не** WE «dead worker». Если после исключения осталась live/REGISTERED запись — это уже inconsistent accounting (failure). Durable outbox shutdown **не** переписывает.

---

## 3. Join, бизнес и durable

`join` **не** доказательство business success. Shutdown **не** меняет автоматически outbox `PENDING` / `FAILED` / `SYNCED`.

После **successful** `Thread.start()` (STARTED):

| Наблюдение | Resource shutdown | Бизнес |
|------------|-------------------|--------|
| thread уже finished до wait | terminal / **success** | outbox как есть |
| thread finished во время join | terminal / **success** | outbox как есть |
| append/outbox `FAILED` | **может быть success** | failure отдельно |
| deadline + STARTED thread **alive** | **failure** + remainder | не трогать outbox из-за shutdown |

| Durable | Daemon |
|---------|--------|
| `PENDING`/`SYNCING` + STARTED **alive** | I/O ещё идёт; drain **не** успех |
| `PENDING` + STARTED **joined/TERMINAL** | leftover для replay; **не** in-flight |
| `SYNCED` | бизнес-успех append |
| `FAILED` | бизнес-failure; join при этом может быть success |

Replay later ≠ ignore in-flight (D29).

---

## 4. Запрет новой операции после producers

Producers `schedule_registry_append` на isolated disable-пути: тело WE disable **до** `task_done`. Когда у всех profile queues `unfinished_tasks==0`, каждый `prepare` уже вернул (включая `Thread.start()`).

**Freeze** `schedule_registry_append` / `_ensure` daemon: после этого условия (и bound sealed admission, как у TASK-41: producers_complete, нет Accepted futures, нет continuation). Поздний `start` → явный reject. Финальный snapshot — **после** freeze.

Новый daemon **не** должен появиться незаметно после финального snapshot.

`/registry_replay` как Accepted job **не** зовёт `schedule_registry_append` сейчас. Если future code начнёт — либо идёт в тот же freeze-path, либо остаётся executor work (TASK-40). Менять replay в этом docs **нельзя**.

---

## 5. Production wait/join protocol

Имена — code PR. Семантика:

1. Отказать mixed/unbound.
2. Требовать те же предусловия producers, что и полное использование TASK-41 (sealed, attested PTB producers, idle Accepted executor, empty continuation, `unfinished_tasks==0`). Пустой outbox / пустой список thread **не** достаточны сами по себе, если freeze ещё не снят со snapshot.
3. Freeze создания.
4. Снять **финальный** список registered operations (включая уже TERMINAL, пока не reap после наблюдения).
5. Для каждой **STARTED**: `await asyncio.to_thread(thread.join, remaining)`; не sync join на PTB loop. Если thread уже finished — join сразу возвращается, это **success**, не remainder.
6. Повторный успешный wait не стартует новые thread и не дублирует учёт.
7. Failure + remainder **без retry** только abnormal: deadline и STARTED всё ещё alive; inconsistent accounting. **Не** «dead before observer». `start()` exception — не live daemon; drain её **не** ждёт (R15).
8. Helper `run_ptb_lifecycle` **не** вызывать API в этом срезе.

### Порядок относительно TASK-39 §5 и TASK-41

TASK-39 §5 нумеровал: work drain items → **join we-registry-*** → WE sentinel → sender → executor → PTB.

TASK-41 уже соединил проверку `unfinished_tasks==0` и join idle WE workers в одном API.

**Инвариант, не открытый:** и WE join, и registry daemon join **завершены до sender** (D30). Sender intake seal только когда registry daemons joined.

**Выбор этого контракта (совместим с существующим TASK-41 API, не переписывает TASK-41):** будущий helper вызывает `stop_isolated_profile_workers` (items drained + WE join), затем wait/join registry daemons, затем sender. Это совпадает с пользовательским срезом «executor → WE → registry → sender». Нумирация TASK-39 §5 шагов 4 и 5 описывала эпоху **до** TASK-41 API; менять TASK-39 заново не требуется, пока D30 держится.

Не подключать helper в TASK-42.

---

## 6. Remainder

Remainder — только **незавершённые или аномальные** operations. Нормально TERMINAL / already-finished STARTED **не** попадает в failure remainder лишь потому, что `is_alive() == False`.

Минимум на каждую такую запись:

- frozen;
- run_id;
- lifecycle state;
- thread name;
- started?;
- alive?;
- outbox status;
- reason.

Не включать `delayed_cleanup`. Durable `PENDING` сам по себе не заполняет remainder и не делает drain success.

---

## 7. Матрица будущих Event/barrier-тестов (code PR)

Реальные `schedule_registry_append` / production `_run` / `append_run_to_dropbox_registry` (I/O можно stub barrier'ом). Без sleep-as-proof. Не harness end_loop. Потоки join в finally. Ошибки thread видны основному тесту.

| # | Сценарий | Ожидание |
|---|----------|----------|
| R1 | WE disable вернулся (`task_done`), append ещё в barrier | wait не успех; remainder alive |
| R2 | daemon started, вошёл в append | registered + started |
| R3 | `start()` вернул, `_run` ещё не вошёл в append | операция в финальном списке; wait ждёт |
| R4 | registered + **successful** `start()`; `_run` **полностью** завершился **до** начала wait | wait **success**; join finished thread ок; **нет** false failure из-за dead thread; без deadlock |
| R5 | exception внутри append | join успех; бизнес-ошибка на outbox/log отдельно |
| R6 | cancel/timeout wait | thread жив, работа продолжается; запись в реестре |
| R7 | повторный wait после R6 | тот же thread; без второй регистрации |
| R8 | **STARTED** и после deadline thread **всё ещё alive** | failure + remainder; без retry. Finished-до-deadline **не** R8 |
| R9 | успешный drain | нет незавершённых registered live operations; все **successfully started** threads TERMINAL / joined / reaped по § 2 |
| R10 | freeze + попытка нового `schedule_registry_append` | reject; snapshot не растёт |
| R11 | durable `PENDING`, thread уже joined (бизнес fail) | shutdown wait может быть успех; pending ≠ in-flight |
| R12 | mixed/unbound | отказ; freeze нет |
| R13 | helper source | `run_ptb_lifecycle` не содержит wait/join registry daemon |
| R14 | `delayed_cleanup` жив | не в remainder и не блокирует success |
| R15 | запись создана; `Thread.start()` **бросает** | живого daemon нет; нет зависшей live operation; drain **не** ждёт её и **не** failure из-за dead thread; outbox может остаться pending/failed по бизнесу — не in-flight |

Business Telegram/browser — заглушки.

---

## 8. Вне скоупа

Runtime этого PR; sender stop; executor shutdown; `run_ptb_lifecycle` orchestration; serve/polling; mixed-stop; `we-registry-mirror-*` (не стартует с append на обследованном SHA); sync replay/patch как daemon; merge/retarget/deploy; live Telegram; исходное Test; повторное закрытие TASK-39/40/41; повторная реализация TASK-41.

---

## 9. Открытые решения (для review; код не выбирает молча)

| # | Тема | Зафиксировано здесь | Остаётся открытым |
|---|------|---------------------|-------------------|
| Q1 | Владелец списка operations | process-local `wallet_editor_registry_async`, не `WorkAdmission` | точные имена API |
| Q2 | Register vs `Thread.start`; reaping | insert до `start()`; STARTED атомарно после успеха; reap **после наблюдения TERMINAL** | точные имена enum |
| Q3 | Порядок WE join vs daemon join | invariant: оба до sender; helper: TASK-41 затем TASK-42 | менять TASK-41 API **нельзя** |
| Q4 | `we-registry-mirror-*` | вне среза: append не вызывает `schedule_mirror_batch` | если code позже вызовет — отдельное уточнение |
| Q5 | Replay | executor Future, не daemon | не переносить replay на `we-registry-*` этим docs |
| Q6 | Mixed | отказ как TASK-41 | mixed-stop (O10) |

---

## 10. Связь

- TASK-39 O4 / O4b / D29 / D30 / § 1.7 / § 5.
- TASK-40: Accepted executor, в т.ч. `/registry_replay`.
- TASK-41: freeze WE workers; `unfinished_tasks==0` закрывает producers `prepare`.
- Следующие: sender, executor shutdown, helper.
