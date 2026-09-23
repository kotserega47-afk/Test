# Контракт допуска isolated schedules (TASK-35)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт на review; runtime **не** менялся; реализации **нет** |
| **База** | закрытие TASK-34 `057614413480831795a393bcfd3ac14f69d89fe4` (принятый review HEAD `f76f9c96f46b2489ecb08a38c542de421b609ba6`, Draft PR #37) |
| **Обследованный SHA** | runtime schedules `f76f9c9…`; уточнение контракта от review HEAD `209b96dc30df324d69c90d4619deae6aee2e7af8` |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **Mixed owner** | `scheduler.py` `schedule_loop` — **поведение не менять**; isolated **не** импортирует этот модуль |

Контракт описывает будущий isolated code. Mixed `schedule_loop` / `dispatch_job_background` / `evaluate_hourly_gate` сохраняют текущее поведение, включая мутацию gate **до** dispatch. Durable cursor, exactly-once и «пропусков нет» **не** объявлять реализованными. Успех будущего code **не** drain/join и **не** полный запрет новой работы в процессе.

---

## 1. Фактическая цепочка mixed (исходники `f76f9c9…`)

```text
scheduler.schedule_loop                          # mixed daemon; isolated loop нет
  load_schedules(force_sync=False)
    get_snapshot_v2 → ScheduleRulesAccessor.get_enabled_schedules
    interval → every_seconds, job_type=rule.job_key
    cron     → cron
  часы keyed by job_type: next_every / next_cron
  due:
    every_seconds: первый hit только arm next_every[jt]=ts_now+interval
                   далее ts_now >= ts_next → due
    cron: первый hit только _next_cron_run(now); далее now >= dt_next → due
    hourly: evaluate_hourly_gate мутирует last_* до dispatch
  dispatch_job_background → executor.submit(request_job)  # обход WorkAdmission
  request_job                                             # не знает admission
```

`Schedule.id` в loop **не** используется. `Schedule.coalesce` **не** читается. После due mixed: `next_every[jt] = ts_now + interval`; cron next от **второго** `datetime.now(MSK)` после dispatch. Gate-skip mixed **сдвигает** `next_*`. Exception `dispatch_job_background` mixed **всё равно** сдвигает `next_*`. Isolated эти два пункта **не** копирует как «слот принят».

---

## 2. Семь keys — фильтр до admission

`ANTARES_ASSEMBLY_JOB_TYPES`: `wallet`, `hourly`, `rate`, `download`, `wallet_editor_registry_refresh`, `wallet_editor_registry_replay`, `script_job:operator_wallets_ready`.

Чужой / неизвестный `job_type`: **до** peek gate и **до** `submit_job_if_open` — нет arm, нет due-потребления, нет submit. Не Accepted.

---

## 3. HourlyGate: peek, затем commit только после Accepted

Mixed `evaluate_hourly_gate` пишет `last_intraday_key` / `last_final_key` **до** dispatch. Isolated так **не** делает.

Isolated (копия формул, без импорта `scheduler.py`):

1. `get_job_params(job="hourly")` и расчёт кандидата — **вне** admission lock (I/O не под lock).
2. **Peek** на снимке/копии gate: `should_fire`, reason, candidate keys. Опубликованный `HourlyGate` **не** меняется.
3. `should_fire is False` → gate-skip (§ 4), не submit.
4. `should_fire is True` → `submit_job_if_open`.
5. **Accepted** → **commit** candidate в опубликованный gate **и** сдвиг `next_*`.
6. **Rejected** или исключение `submit` до Future → commit **нет**; due-маркер **не** потребляется.
7. Ошибка уже принятого Future → gate и `next_*` **не** откатываются.

Допустима проверка на `copy.copy(gate)` / dataclass-замене, затем присвоение полей только в commit. Intraday bucket и `final_daily_time` — независимые candidate fields; commit только тех, что peek пометил к фиксации. Mixed не менять.

---

## 4. Часы, повторы, sealed (без «может»)

Tick получает `now_ts: float` и `now_dt: datetime` (MSK) снаружи. Внутри одного tick **нет** busy-retry: каждый row не больше одной попытки submit; затем возврат. Следующая попытка — только на **следующем** вызове `tick`.

| Событие | next_every (interval) | next_cron | gate last_* |
|---------|----------------------|-----------|-------------|
| Arm (ключа ещё нет) | `now_ts + max(1, every_seconds)` | `_next_cron_run(now_dt, cron)` | не трогать |
| Accepted | `now_ts + max(1, every_seconds)` | `_next_cron_run(now_dt, cron)` | commit peek |
| Gate-skip (`should_fire` false) | **сдвигает** так же, как строка due без admit | **сдвигает** `_next_cron_run(now_dt, cron)` | **не** commit |
| closed / ошибка submit до Future | **не** сдвигать | **не** сдвигать | **не** commit |
| sealed | **не** сдвигать; **явный no-op submit** | то же | не commit |
| ошибка Future после Accepted | уже сдвинуты, **не** откат | то же | уже commit, не откат |
| чужой key | не писать часы | не писать | — |

**От какого времени следующий запуск (isolated):** всегда от **входов tick** `now_ts` / `now_dt`, не от предыдущего `ts_next` и не от второго wall-clock после submit. Пропущенные интервалы не догоняются (скачок от now). Это совместимо с mixed interval `ts_now + interval`; для cron isolated **намеренно** использует тот же `now_dt`, что due (тестируемый fake clock). Mixed второй `datetime.now` не копировать.

**closed / ошибка submit:** на следующем tick тот же due ещё истинный (маркер не потреблён) → снова peek/submit. Не цикл внутри текущего tick.

**sealed:** источник новых schedule-задач на этом tick — **явный no-op** (нет submit, нет потребления gate/`next_*`). Первый code **не** обязан останавливать поток: потока нет. Serve позже может перестать звать `tick`. Не drain/join.

**Accepted** = успешный `executor.submit(request_job, …)` → `AdmissionAccepted`. Это **не** успех бизнес-задания. `job_rejected_busy` / unknown type / exception **внутри** Future слот **назад не возвращают**.

---

## 5. Минимальный production API следующего code

**Вариант (выбран):** формулы cron/hourly **копируются** в isolated-модули с pin-тестами против текущих mixed формул. `scheduler.py` в первом code **не** трогать и **не** импортировать. Общий `core/`-extract — отдельное решение, не этот этап. `core.schedules.load_schedules`, `core.scheduler_clocks_control`, `core.config_manager.get_job_params`, `WorkAdmission` — допустимы (не mixed gate, не `telegram_bot`).

| Роль | Имя |
|------|-----|
| Владелец | `modules/antares/scheduler.py` (`modules.antares.scheduler`) |
| Cron | `modules/antares/schedule_timing.py` — `_next_cron_run` / parse (копия формул `scheduler.py` L60–103) |
| Gate peek/commit | `modules/antares/hourly_gate.py` — dataclass + peek + commit; **не** `scheduler.HourlyGate` |
| Tick | единственный владелец: `tick(state, *, now_ts, now_dt, schedules, admission) -> TickResult` |

Состояние (`IsolatedScheduleState`): `next_every`, `next_cron`, isolated `HourlyGate`. Живёт у caller теста / будущего serve; `tick` — единственный, кто apply reset, arm, peek, submit, commit.

**Reset:** в **начале** `tick` через существующий `core.scheduler_clocks_control._apply_scheduler_clock_reset_if_requested(state.next_every, state.next_cron)`. HourlyGate **не** чистится (как mixed). Caller `tick` reset сам не дублирует. `/reload_rules` по-прежнему только `request_scheduler_clocks_reset`.

**Входы tick:** clock (`now_ts`, `now_dt`), `schedules: list[Schedule]`, `admission: WorkAdmission`. Не читать env clock внутри. Не звать `dispatch_job_background`.

**Первый code:** testable `tick` без serve, без thread, без `time.sleep`. `apps.antares` `boot`/`run` и `assemble_antares` **не** стартуют schedules и **не** зовут `tick`.

Запрет импорта в `modules.antares.scheduler` и соседних hourly/timing: `scheduler` (top-level mixed), `integrations.telegram_bot`, `integrations.tg_commands`.

---

## 6. Accepted Future

Сразу после `AdmissionAccepted`, **до** любого await tick:

1. Commit gate + `next_*` (§ 3–4).
2. Наблюдение: `future.add_done_callback` (sync tick без running loop) логирует exception **один раз**; **не** `future.result()` / не await job. Если позже tick окажется на event loop — дополнительно `watch_admitted_future(..., job_type=jt)` **сразу**, без wait. Не использовать mixed `dispatch_job_background`.
3. `seal` **не** cancel принятый Future.
4. Сбой шага 2 (диагностика/callback attach) логируется отдельно; **не** un-commit; **не** повторный submit того же слота на этом и следующих tick (часы уже как после Accepted).

---

## 7. Идентичность расписания (сохранить mixed, не keyed by id)

Clocks **keyed by `job_type`**, отдельно `next_every` и `next_cron`. `Schedule.id` **не** ключ часов.

Несколько enabled rows **одного** `job_type`:

- две **interval** строки: **общий** `next_every[jt]`; порядок `load_schedules` как mixed (arm/due зависят от порядка в одном tick);
- **interval + cron** того же `job_type`: **два** независимых clock, **два** возможных submit;
- не вводить ключ по `id` и не reject duplicates в этом этапе.

Это **сохранение** mixed, не новая семантика. Валидация/отклонение дублей — отдельное решение, не молча.

Чужие keys (§ 2) в эти dict не попадают. Исчезнувший из текущего списка allowlist `job_type` — pop из dict, как mixed `active`.

---

## 8. «Два тика» — дефект ожидания теста, не mixed runtime

```
py -3.12 -m pytest tests/test_scheduler_dispatch.py::test_schedule_loop_calls_dispatch_job_background -q --tb=short
```

На `0576144…` / том же loop что `f76f9c9…`, Python 3.12.10: **1 failed** — два `("wallet", "scheduler")`. Fake clock 0→2→100 плюс 3 итерации (`KeyboardInterrupt` на 3-м sleep) даёт arm + два due. Ожидание теста (ровно один dispatch) **неверно для этого clock**. Mixed runtime **не** менять. Isolated тесты этот assert не копируют.

---

## 9. Матрица будущих проверок (code PR)

Fake clock + Event/barrier; без sleep как доказательства. Реальные `WorkAdmission` и `executor.submit`. Без live polling, `scheduler.py` import, `telegram_bot`. `request_job` — stub с Event.

| # | Сценарий | Ожидание |
|---|----------|----------|
| S1 | interval due + OPEN, ключ из семи | один submit под lock; Accepted; `next_every` от `now_ts` |
| S2 | cron arm, затем due | первый tick только arm; due → Accepted; next от `now_dt` |
| S3 | чужой key | нет admission, нет часов |
| S4 | seal до submit (lock barrier) | Rejected; `request_job` нет; gate/`next_*` не потреблены |
| S5 | submit до seal | Accepted Future живёт; seal не cancel |
| S6 | closed | как S4 |
| S7 | submit exception до Future | не Accepted; gate/`next_*` не потреблены; следующий tick может принять |
| S8 | hourly peek fire → Rejected / submit error | тот же bucket на следующем tick может Accepted; last_* не сдвинуты |
| S9 | hourly Accepted (intraday bucket) | второй tick того же bucket не дублирует submit |
| S10 | `final_daily_time` fire / already / not due | commit только после Accepted; skip сдвигает cron/interval `next_*`, не last_final_key если не fire |
| S11 | gate-skip (already fired / waiting / no config) | нет submit; `next_*` **сдвинуты**; last_* без нового commit |
| S12 | ошибка Future после Accepted | лог один раз; часы/gate не откат; нет повторного слота |
| S13 | диагностика после commit бросает | слот остаётся Accepted; нет второго submit |
| S14 | reset clocks | `next_*` пусты, gate жив; следующий hit только arm |
| S15 | две interval rows одного job_type | общий `next_every`; порядок списка как mixed |
| S16 | interval+cron одного job_type | два clock, возможны два submit |
| S17 | модуль tick не импортирует mixed `scheduler` / `telegram_bot` | проверка импортов |
| S18 | I/O `get_job_params` вне admission lock | lock не держат на peek params |

---

## 10. Границы

**Обходы admission:** internal `enqueue_auto_enable_batch`; conversion `add_task`; `tg_receiver`.

**Lifecycle:** drain; worker/executor/sender stop; serve; mixed-stop; автозапуск tick из boot/run.

`load_schedules` / `rules_provider` — не обход постановки. Не обещать durable cursor, coalesce, exactly-once, sealed ⇒ нет любой новой работы в процессе.
