# Контракт допуска новой работы isolated Antares (TASK-25)

| Мета | Значение |
|------|----------|
| **Статус** | к review (только документы; runtime не менялся; pytest не запускался) |
| **База** | закрытие TASK-24 `364976424b1818e37a76bc0b1d10cb254e4479de` (принятый review HEAD `34ef7af33cc112d369e0c9ee860d66954268ce8c`, Draft PR #27) |
| **Start/stop** | [MODULAR_REORG_ANTARES_STARTSTOP.md](MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **Lifecycle** | [MODULAR_REORG_ANTARES_LIFECYCLE.md](MODULAR_REORG_ANTARES_LIFECYCLE.md) |
| **Migration / JOB_ACCEPT** | [MODULAR_REORG_MIGRATION.md](MODULAR_REORG_MIGRATION.md) — рычаг cutover **не** этот PR |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Следующая зависимость **перед сервисом**: управляемый допуск новой работы и его закрытие при остановке. PTB `stop`/`shutdown` это **не** закрывают.

Runtime в TASK-25 **не** менять. Live polling, sender stop, worker join, executor shutdown **не** входят в этот PR и **не** входят в первый будущий code scope допуска.

`JOB_ACCEPT` как production env-флаг **не** добавлять без отдельного cutover-контракта. Isolated допуск — **состояние процесса**, не замена mixed scheduler.

---

## 0. Что считается работой

Допуск **не** сводится к `JOB_REGISTRY` / `request_job`. Часть операций Antares выполняется без job-dispatch.

| Класс | Примеры | Сейчас |
|-------|---------|--------|
| Постановка JOB_REGISTRY | `/run_*`, `/operator_wallets_ready`, `/wallet_editor_refresh` | `run_job_async` → `dispatch_job_async` → `request_job` |
| Прямая registry/Auto-Enable операция | `/registry_replay`, `/registry_export`, `/auto_enable_plan`, `/auto_enable_run` | callback → `run_in_executor` / sync API; **нет** `request_job` |
| Document ingest | `Document.ALL` | `add_task` / `add_add_wallet_task` / `add_edit_wallet_task` |
| Будущие schedules | isolated loop (ещё нет) | mixed: `schedule_loop` → `dispatch_job_background` |
| Внутренние повторные постановки | Auto-Enable `enqueue_auto_enable_batch`; job → ещё один dispatch/enqueue | **новая** работа, не «продолжение» уже принятой |
| Read-only callback | `/whoami`, `/help`, `/start`, `/status`, `/registry_health`, `/rules_validate` | ACL + ответ; **не** постановка работы |
| Outbound sender | `integrations.telegram_bot.send_message_sync` | очередь исходящих; **не** точка приёма операторской работы |

`/reload_rules` инвалидирует snapshot — для isolated это **постановка работы** (побочный эффект), не read-only.

Conversion bridge (`integrations/conversion_wallet_editor_bridge.py` → `add_task`) — путь mixed/Raccoon, не Antares handlers. Isolated **не** меняет его в этом контракте.

---

## 1. Карта вызовов (исходники на закрытии TASK-24)

Поступление Telegram update (когда появится serve/polling; sandbox кладёт в `update_queue`):

```text
PTB Updater / update_queue
  → Application._update_fetcher
  → handler callback
```

Handlers: `modules/antares/handlers.py` `get_antares_handlers()`; ingest: `modules/antares/document_ingest.py`.

### 1.1 Update → callback → JOB_REGISTRY

| Шаг | Файл | Символ |
|-----|------|--------|
| CommandHandler | `modules/antares/handlers.py` | `cmd_run_wallet` / `hourly` / `download` / `rate` / `cmd_operator_wallets_ready` / `cmd_wallet_editor_refresh` |
| ACL | `modules/antares/handlers.py` | `_run_antares_command` → `guard_or_deny` |
| Reply «Запускаю» | `core/tg_command_dispatch.py` | `run_job_async` L29–35 **до** dispatch |
| Async dispatch | `core/job_dispatch.py` | `dispatch_job_async` L111–128 |
| Sync body | `core/job_runner.py` | `request_job` L178: events, `JOB_REGISTRY`, `_try_lock`, `_RUNNING` |

`request_job` **не** проверяет допуск. Busy (`job_rejected_busy`) и unknown type — **не** «допуск закрыт».

### 1.2 Update → callback → прямые операции (без `request_job`)

| Команда | Файл | После ACL |
|---------|------|-----------|
| `/registry_health` | `handlers.py` `cmd_registry_health` | `build_registry_health_report` — **read-only** |
| `/registry_replay` | `handlers.py` `cmd_registry_replay` | `replay_pending_outbox_records` |
| `/registry_export` | `handlers.py` `cmd_registry_export` | `run_in_executor(build_registry_export_from_postgres)` |
| `/auto_enable_plan` | `handlers.py` `cmd_auto_enable_plan` | `run_in_executor(run_auto_enable_plan)` |
| `/auto_enable_run` | `handlers.py` `cmd_auto_enable_run` | `run_in_executor(run_auto_enable)` → `enqueue_auto_enable_batch` (`integrations/wallet_editor_auto_enable.py`, `automation/worker.py`) |
| `/reload_rules` | `handlers.py` `cmd_reload_rules` | invalidate + force snapshot |

Reply «Replaying…» / «Building export…» / «Строю plan…» сейчас уходит **до** завершения работы и **без** проверки допуска.

### 1.3 Document ingest → worker enqueue

```text
MessageHandler(Document.ALL, handle_wallet_editor_document)
  modules/antares/document_ingest.py
    allowlist / operator / routing
    reply «Файл получен»          ← до enqueue
    get_file + download_to_drive
    add_add_wallet_task | add_edit_wallet_task | add_task
      automation/worker.py
        _ensure_profile_worker (lazy daemon thread)
        queue.put
```

`ensure_worker_started()` — no-op. Stop/join/sentinel **нет**.

### 1.4 Mixed schedules (не isolated runtime)

`scheduler.py` `schedule_loop` L240+ → `dispatch_job_background` (`core/job_dispatch.py` L67) → `ThreadPoolExecutor.submit(request_job)`. Isolated этот loop **не** копирует (LIFECYCLE.md). Когда isolated schedules появятся — тот же допуск, **до** `submit`.

### 1.5 Уже принятая работа

| Поток | Где живёт | Stop API сейчас |
|-------|-----------|-----------------|
| JOB_REGISTRY run | `_RUNNING` + file lock в `request_job` | нет cancel/finish API |
| Job executor | `core/job_dispatch.py` `_JOB_EXECUTOR` | только `_reset_job_executor_for_tests` |
| WE worker | `automation/worker.py` daemon + `Queue` | нет |
| Telegram sender | `integrations/telegram_bot.py` queue + loop | нет production stop |
| PTB callbacks | `concurrent_updates` tasks | `app.stop` gather (STARTSTOP.md); не jobs |

---

## 2. Владелец состояния допуска

Владелец: **isolated Antares process**, объект будущего модуля `modules.antares.work_admission` (имя файла code PR может уточнить).

| Среда | Состояние | Поведение |
|-------|-----------|-----------|
| Mixed `scheduler.py` / import без bind | **unbound** (default) | допуск **не** участвует; поведение как сейчас |
| Isolated после успешного `app.start` | **open** | разрешённая новая работа принимается |
| Isolated с начала остановки | **sealed** | новая работа **не** принимается; **не** открывается снова в этом процессе |

Правила:

1. Default **unbound**, не «закрыт». Иначе import handlers в mixed/тестах сломает постановку.
2. `open()` — только isolated lifecycle после `app.start`, до кормления updates, которые могут ставить работу. `boot` / `run` helper не вызывают — bind **не** обязателен.
3. `seal()` — один раз, при **начале** isolated stop (`stop.set()` / вход в finally helper **до** ожидания drain бизнес-потоков, которых ещё нет). Не ждать `app.shutdown`.
4. Sealed **навсегда для этого запуска**. Повторный `open()` — ошибка / no-op с сохранением sealed.
5. Не хранить допуск как несинхронизированный `bool` в handlers. Нужен lock (или эквивалент), общий для `try_accept` и постановки.

`core.job_runner.request_job` и `core.job_dispatch` **не** владельцы isolated-допуска: правка там без default-unbound изменит mixed.

---

## 3. Четыре слоя (не смешивать)

| Слой | Что | Этот контракт |
|------|-----|----------------|
| A. Поступление новых updates | polling / `put` в `update_queue` | STARTSTOP.md: прекратить `put`/polling **до** `app.stop`. Допуск **не** заменяет fetcher |
| B. Начало callback | PTB уже отдал Update handler'у | Callback **может** стартовать после seal, если update уже в обработке. Допуск не отменяет running handler |
| C. Постановка job/worker/прямой операции | dispatch, `queue.put`, export/replay/auto-enable/reload | **точка принятия** § 4 |
| D. Уже принятая и выполняющаяся работа | `_RUNNING`, очередь профиля, executor future, sender | продолжается; **нет** обещания cancel/finish, пока нет API |

Seal закрывает **C**. A закрывается отдельно (не кормить очередь). B не kill. D не drain в этом PR.

---

## 4. Точка принятия и гонка

**Принято** = под одним lock: допуск open **и** единица работы поставлена (submit / `queue.put` / вход в прямую операцию). Иначе **отказано**, постановки нет.

Недостаточно:

```text
if admission.is_open:          # читает bool
    dispatch_job_async(...)    # другая гонка: seal между if и submit
```

Контракт гонки (два потока: seal vs enqueue):

- Ровно один исход: **accepted** (попала в executor/очередь/прямую операцию) **или** **rejected** (sealed, побочного enqueue нет).
- Нельзя: seal вернул успех, а enqueue всё же произошёл.
- Нельзя: rejected, но `request_job` / `queue.put` уже вызван.
- Проверка и постановка — одна критическая секция; либо `try_accept()` возвращает token, действительный только пока lock удерживается до enqueue, и enqueue внутри той же секции.

Для `run_job_async`: допуск **до** reply «Запускаю» и **до** `dispatch_job_async`. Иначе оператор видит «Запускаю», а работа отклонена — путаница с ACL/ошибкой job.

Для ingest: допуск **до** `queue.put` (предпочтительно до `get_file`, чтобы не качать файл в sealed). Если «Файл получен» уже ушёл — исход гонки всё равно однозначен на `put`; ответ «не принимаем» не должен сопровождаться очередью.

Для `dispatch_job_async` / `background`: isolated обёртка принимает **до** `executor.submit`. `submit` после seal = принятие.

Внутренний re-enqueue (auto-enable batch, повторный dispatch из job) — **новая** C. Sealed → не ставить. Уже взятый worker item (D) дорабатывается.

---

## 5. Ответы при закрытом допуске

Не менять ACL (`guard_or_deny` / `deny_message`) и не подменять бизнес-результаты (`job_rejected_busy`, `unknown_job_type`, allowlist ingest, operator unmapped).

Порядок isolated handler:

1. ACL как сейчас. Deny ACL — те же тексты, допуск не спрашивать.
2. Если ACL ok и операция класса C — `try_accept`. Отказ — **отдельное** сообщение «сейчас не принимаем новую работу» (точный текст code PR). Не маскировать под `⛔` ACL.
3. Read-only (класс B без C) — допуск не режет.

Логи: причина `admission_sealed`, не `access_denied`.

Не обещать в тексте отмену running jobs.

---

## 6. Mixed и env

Mixed `run_polling` + `schedule_loop` **без изменений**. Unbound admission = no-op.

Не вводить `JOB_ACCEPT` в `os.getenv` в первом code: migration рычаг cutover — другой контракт (два процесса, старый drain). Isolated seal — in-process, на текущий запуск.

`JOB_DISPATCH_VIA_EXECUTOR` не переиспользовать как допуск.

---

## 7. Минимальный будущий code (не этот PR)

Один проверяемый этап:

1. `modules.antares.work_admission`: unbound / open / sealed; `open()`, `seal()`, `try_accept()` под lock.
2. Isolated: `open()` после успешного `app.start` в пути, который реально кормит callbacks; `seal()` в начале stop helper (без смены PTB порядка STARTSTOP).
3. Обвязать **только** Antares C-пути: `handlers.py` job-команды и прямые mutating cmds, `document_ingest.py` перед enqueue. Не менять `request_job` / mixed `scheduler.py` / `telegram_bot` / `automation.worker` API.
4. `boot` / `run` без bind — как сейчас.
5. Не делать live polling, sender stop, worker join, executor shutdown.

Проверки — детерминированные, с `threading.Event` / barrier (не sleep-as-sync):

| Сценарий | Ожидание |
|----------|----------|
| Sealed, затем C | работа не стартует; очередь/executor/request_job не вызваны |
| Open, разрешённая C | постановка как сегодня (после ACL) |
| Open, затем seal, затем C | не принимается |
| Гонка seal ∥ try_accept+enqueue | один исход; нет enqueue после успешного seal |
| Mixed import / unbound | прежние вызовы `dispatch_job_*` / `add_task` без admission |

Read-only `/whoami` после seal (если callback уже идёт) — допускается; не требует C.

---

## 8. Зависимости отдельно (не «заодно»)

| Тема | Почему отдельно |
|------|-----------------|
| Live polling / `enable_polling=True` / публичный `serve` | STARTSTOP.md; TASK-24 helper polling не реализует |
| Sender stop | нет production API |
| Worker join / sentinel | daemon queues; нет stop |
| `ThreadPoolExecutor.shutdown` production | есть только test reset |
| Cancel/finish `_RUNNING` | нет API |
| Durable inbox / schedule cursor | MIGRATION.md |
| `JOB_ACCEPT` env cutover | другой процесс, не isolated seal |
| Drain принятых очередей | нужен stop API worker/executor |

---

## 9. Вне скоупа TASK-25

Реализация `work_admission`; правка runtime; pytest; live Telegram; mixed gate; Railway; merge; исходное дерево Test; retarget; deploy.
