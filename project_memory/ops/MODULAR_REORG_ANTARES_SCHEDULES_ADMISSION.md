# Контракт допуска isolated schedules (TASK-35)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт на review; runtime **не** менялся; реализации **нет** |
| **База** | закрытие TASK-34 `057614413480831795a393bcfd3ac14f69d89fe4` (принятый review HEAD `f76f9c96f46b2489ecb08a38c542de421b609ba6`, Draft PR #37) |
| **Обследованный SHA** | `0576144…` / runtime schedules как на `f76f9c9…` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **Mixed owner** | `scheduler.py` `schedule_loop` — **не** менять этим этапом |

Контракт описывает будущий isolated code. Mixed `schedule_loop` / `dispatch_job_background` сохраняют текущее поведение. Durable schedule cursor, exactly-once и «пропусков нет» **не** объявлять реализованными. Успех будущего code **не** есть drain/join и **не** полный запрет новой работы в процессе.

---

## 1. Фактическая цепочка (исходники `f76f9c9…`)

```text
scheduler.schedule_loop                          # mixed daemon thread; isolated loop нет
  load_schedules(force_sync=False)
    get_snapshot_v2 → ScheduleRulesAccessor.get_enabled_schedules
    interval → Schedule.schedule_type="every_seconds", job_type=rule.job_key
    cron     → Schedule.schedule_type="cron"
    прочие stype / пустой job_key / пустой cron / every_seconds<=0 → drop
  выбор due:
    every_seconds: первый hit только вооружает next_every[jt]=now+interval, без dispatch
                   далее ts_now >= ts_next → due
    cron: первый hit только _next_cron_run, без dispatch; далее now >= dt_next → due
    hourly: дополнительно HourlyGate (job_params), до dispatch
  dispatch_job_background(jt, Actor(kind="scheduler"))
    get_job_executor().submit(request_job, jt, actor, force_rules_sync=False)
    add_done_callback → лог exception Future
    caller не ждёт result
  request_job                                    # lock job_type, JOB_REGISTRY; не знает admission
```

Подтверждено чтением: `core/schedules.py`, `scheduler.py` L240–342, `core/job_dispatch.py` `dispatch_job_background` L67–90, `core/job_runner.py` `request_job` L178.

`Schedule.coalesce` в `schedule_loop` **не** читается. После due mixed ставит `next_every[jt] = ts_now + interval` (скачок от «сейчас», промежуточные интервалы не догоняются). Поле coalesce **не** есть exactly-once.

`dispatch_job_background` **обходит** `WorkAdmission`: голый `executor.submit`. Isolated code **не** должен звать его как gate.

Часы: in-memory `next_every` / `next_cron`. `/reload_rules` → `request_scheduler_clocks_reset` → на следующем тике `_apply_scheduler_clock_reset_if_requested` чистит оба dict; **HourlyGate не сбрасывается**. После reset следующий hit снова только вооружает, без немедленного dispatch.

Остановка mixed loop: `while True` + `time.sleep(5)`; daemon из `main()`. Isolated serve **не** запускает этот loop. Источник новых schedule-задач в mixed — сам `schedule_loop`.

---

## 2. Семь Antares keys (фильтр до dispatch)

Allowlist isolated = `ANTARES_ASSEMBLY_JOB_TYPES` (`modules/antares/assembly.py`):

| key |
|-----|
| `wallet` |
| `hourly` |
| `rate` |
| `download` |
| `wallet_editor_registry_refresh` |
| `wallet_editor_registry_replay` |
| `script_job:operator_wallets_ready` |

Фильтр — **до** `submit` / `dispatch_job_background`. Неизвестный или чужой `job_type` (conversion, raccoon_*, wr, опечатка, пустой) **не** диспатчится и **не** идёт в `WorkAdmission.submit_*`. Это не Accepted.

HourlyGate для `hourly` остаётся **до** admit: skip gate ≠ отказ admission и ≠ Accepted.

---

## 3. Атомарный допуск на submit

Будущий isolated путь due-слота:

```text
если jt не в семи keys → skip, не submit
outcome = admission.submit_job_if_open(jt, Actor(kind="scheduler"))
  with WorkAdmission._lock:
    если state is not OPEN → AdmissionRejected(state)   # submit не вызывается
    иначе executor.submit(request_job, ...) → AdmissionAccepted(future)
```

Не оборачивать `await` / `future.result()` / `request_job()` в admission lock. Не считать `dispatch_job_background` атомарным admit. Не менять `request_job`.

| Исход | Слот принят? | Часы isolated |
|-------|----------------|---------------|
| `AdmissionAccepted` | **да** (успешный `executor.submit`) | двигать `next_*` как mixed после due (скачок от now) |
| `AdmissionRejected` (closed/sealed) | **нет** | **не** выдавать за успешный слот; не двигать `next_*` как после Accepted |
| исключение `submit` до Future | **нет** | то же: не Accepted; не маскировать под успешный слот |
| ошибка **внутри** уже принятого Future | слот уже принят | часы уже сдвинуты; это слой D / callback лога, не откат admit |
| skip фильтра / hourly gate | **нет** | не submit; gate-skip mixed двигает `next_every` без job — isolated может повторить тот же skip без вызова submit |

Mixed сегодня двигает `next_every` **даже после exception** `dispatch_job_background`. Isolated **не** копирует это как «слот принят». Mixed этим PR **не** править.

closed: bound, ещё не `open` — isolated loop не должен submit. sealed: новые due не submit; уже принятые Future продолжают.

---

## 4. Пропущенный слот, reload, stop источника

**Пропуск:** если тик опоздал, mixed один раз due и ставит next от `ts_now`, а не догоняет каждый интервал. Isolated — та же модель. Это **не** durable cursor и **не** запрет пропусков.

**Reload/reset clocks:** как сейчас — очистка `next_every`/`next_cron` по флагу; HourlyGate живёт. После очистки нет немедленного fire. Isolated использует тот же `request_scheduler_clocks_reset` / apply, не отдельный диск.

**Стоп новых schedule-задач:** isolated loop перестаёт `submit_if_open` после `seal` (проверка на тике или выход из loop). Это **не** drain очереди, **не** `executor.shutdown`, **не** worker join, **не** sender stop. Принятые Future не cancel.

---

## 5. Mixed

`scheduler.py` `schedule_loop`, `dispatch_job_background`, `JOB_DISPATCH_VIA_EXECUTOR`, early profile gate — без изменений. Не чинить mixed ради зелёного теста § 6.

Isolated loop — отдельный модуль/вход (`python -m apps.antares` / будущий serve), не ветка внутри mixed `main()`.

---

## 6. Известное падение «два тика» (sandbox, не чинить mixed)

Команда (cwd worktree, `PYTHONPATH` снят, `PROJECT_PROFILE` unset), Python **3.12.10**, SHA `0576144…` / тот же `schedule_loop` что `f76f9c9…`:

```
py -3.12 -m pytest tests/test_scheduler_dispatch.py::test_schedule_loop_calls_dispatch_job_background -q --tb=short
```

**1 failed**, exit 1: `assert calls == [("wallet", "scheduler")]` — слева **два** `("wallet", "scheduler")`.

Причина (подтверждена кодом + этим прогоном, не UNKNOWN):

1. Тест патчит `sleep`: 3-й вызов → `KeyboardInterrupt` ⇒ **три** итерации loop.
2. `every_seconds=1`; `time.time`: 1→0.0 (только arm), 2→2.0 (**dispatch 1**), далее 100.0 (**dispatch 2**).
3. Ожидание теста — ровно один dispatch. Поведение loop при таком clock согласовано с L292–309: первый hit не шлёт, каждый последующий due шлёт и сдвигает next от `ts_now`.

Соседний `test_schedule_loop_ticks_while_background_job_holds` с тем же clock/sleep проверяет `tick_count >= 2`, не единственный dispatch.

Исторически то же падение на TASK-25/26 (`a33df9c…`, `137fa639…`). **Не** менять mixed scheduler, чтобы тест стал зелёным, без отдельного решения. Isolated тесты § 7 **не** копируют этот assert.

---

## 7. Матрица будущих проверок (code PR, не этот)

Fake clock + `threading.Event` / barrier; **без** `time.sleep` как доказательства. Реальный `WorkAdmission` + реальный `executor.submit` (обёртка наблюдения). Без live polling, браузера, сети. Бизнес-`request_job` можно заменить функцией с Event.

| # | Сценарий | Ожидание |
|---|----------|----------|
| S1 | due + OPEN, ключ из семи | один `submit` под lock; `AdmissionAccepted`; часы после Accepted |
| S2 | неизвестный / чужой key | нет dispatch/submit |
| S3 | seal **до** submit (barrier: seal держит lock / seal завершён до submit) | `AdmissionRejected`; `request_job` не ставится; слот **не** успех |
| S4 | submit **до** seal | Accepted Future живёт после seal; не cancel |
| S5 | `submit` бросает до Future | не Accepted; часы не как после успеха; нет ложного «слот взят» |
| S6 | closed (не OPEN) | как S3 |
| S7 | hourly gate skip | нет submit; не путать с Rejected |
| S8 | reset clocks | dict пусты; следующий hit только arm |

Гонки: наблюдать попытку захвата admission lock, не короткий timeout без события.

---

## 8. Границы / оставшаяся работа

Этот контракт — **только** isolated schedules admit.

**Обходы admission (постановка работы):** внутренний `enqueue_auto_enable_batch`; conversion bridge `add_task`; применимые legacy-входы (`automation/tg_receiver.py`).

**Lifecycle:** drain очереди; worker join; executor shutdown; sender stop; serve; mixed-stop.

`rules_provider` / `load_schedules` сами по себе **не** обход постановки (чтение rules). Live Telegram; merge/retarget/deploy; исходное Test; правка mixed ради § 6.

### 8.1 Не обещать

- durable cursor / запись last-run на диск;
- exactly-once, отсутствие пропусков, использование `Schedule.coalesce`;
- что sealed ⇒ в процессе нет никакой новой работы (ingest/TG/internal enqueue — другие пути);
- drain/join принятых jobs.
