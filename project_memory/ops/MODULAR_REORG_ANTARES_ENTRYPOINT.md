# План isolated Antares entrypoint (TASK-17)

| Мета | Значение |
|------|----------|
| **Статус** | PROPOSED (только документы; runtime не менялся) |
| **База** | закрытие TASK-16 `950a66a745bbac64cbf2281951a5ed28e01474cf` (review HEAD `f29cc89…`) |
| **Сборка** | [MODULAR_REORG_ANTARES_ASSEMBLY.md](MODULAR_REORG_ANTARES_ASSEMBLY.md) |
| **ADR** | [MODULAR_REORG_ADR.md](MODULAR_REORG_ADR.md) `apps/` |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Этот документ — контракт **отдельного** процесса Antares. Mixed `scheduler.py` и `enforce_legacy_scheduler_profile()` остаются как есть: явный `PROJECT_PROFILE=antares` на mixed entry по-прежнему отказ (exit 2). Railway `startCommand`, профили production, merge и cutover **не** входят.

---

## 1. Путь и команда

| | Контракт | Сейчас (CONFIRMED) |
|--|----------|--------------------|
| Isolated entry | `apps/antares.py` (тонкий process entry по ADR) | файла **нет** |
| Команда (локально / subprocess) | `py -3.12 apps/antares.py` из корня репозитория | не существует |
| Mixed entry | `python scheduler.py` | `scheduler.py` L368–369 `if __name__ == "__main__": main()` |
| Prod start | `/usr/bin/tini -s -- /opt/venv/bin/python scheduler.py` | `railway.toml` `[deploy].startCommand` |

Первый code PR **не** меняет `railway.toml`, `nixpacks.toml`, `Procfile`. Isolated команда — только новый файл + тесты. Выкат на сервис Test этой командой — отдельный ops-PR после приёмки.

Не вызывать isolated entry через `import scheduler` / `python scheduler.py`: mixed gate отклонит `antares`.

---

## 2. Допустимый `PROJECT_PROFILE` и ранний отказ

Парсер `core.project_profile.parse_project_profile` **не** менять. Isolated gate — **новая** функция (тот же модуль `project_profile_boot.py` или соседний), **без** изменения `decide_legacy_scheduler_boot` / `enforce_legacy_scheduler_profile`.

| Вход | Isolated `apps/antares.py` | Mixed `scheduler.py` (без изменений) |
|------|---------------------------|--------------------------------------|
| `PROJECT_PROFILE=antares` (точное имя, `implicit_default=False`) | **допуск** после isolated gate | `UnwiredProjectProfileError`, exit **2** |
| unset / `""` / whitespace (`implicit_default=True`) | **отказ** рано: это documented mixed, не Antares | `legacy_mixed`, продолжение |
| `raccoon` / `wr` | **отказ** рано | отказ exit 2 |
| неизвестное значение | **отказ** (`InvalidProjectProfileError`) | отказ exit 2 |

Отказ isolated gate: stderr + `SystemExit` 2 **до** импорта Telegram, `JOB_REGISTRY`, `assemble_antares`, `telegram_bot`, worker, schedules.

`PROJECT_PROFILE=antares` **не** включать на mixed entry в этом же PR.

---

## 3. Порядок: gate → rules/logger → assembly → запрет старта при сбое

Целевой `main` isolated entry (первый code PR реализует шаги 1–5 **без** шагов 6–9):

1. Isolated profile gate (§ 2).
2. Явный `load_dotenv()` **после** gate (сейчас dotenv только в `integrations.telegram_bot` на import, `scheduler.py` сам dotenv не вызывает).
3. Проверка `TELEGRAM_BOT_TOKEN` (непусто). Отказ **до** импорта `telegram_bot` / Application, чтобы не стартовал sender loop.
4. `AccessRules(os.getenv("RULES_XLSX_PATH", "").strip())` + MAIN logger. Конструктор workbook **не** читает (как mixed).
5. `assemble_antares(rules=…, logger=…)`. Любой `AntaresAssemblyError` / сбой импорта → `SystemExit` ≠ 0, **без** `Application`, `run_polling`, `ensure_worker_started`, `schedule_loop`.
6. *(будущий code, не первый PR)* `get_snapshot(force_sync=True)` fail-fast workbook.
7. *(будущий)* `Application.builder().token(…).concurrent_updates(True)` + `get_antares_handlers()`.
8. *(будущий)* `ensure_worker_started()` затем daemon `schedule_loop` **только** с фильтром семи keys.
9. *(будущий)* `app.run_polling(close_loop=False)` как mixed, пока нет отдельного shutdown-контракта.

Смешанный `main()` сегодня (CONFIRMED, `scheduler.py` L349–365): token → Application → mixed `get_handlers()` → `RULES.get_snapshot(force_sync=True)` → worker → `schedule_loop` thread → `run_polling`. Импорты mixed jobs уже произошли **до** `main` (L38–42), после mixed gate (L4–8). Isolated **не** копировать этот import-порядок.

---

## 4. Будущий старт и остановка компонентов

Порядок **старта** (после успешной сборки; не в первом code PR):

1. Isolated gate + dotenv + token.
2. Rules/logger + `assemble_antares`.
3. Fail-fast rules snapshot.
4. Telegram Application + Antares handlers (17 CommandHandler + Document.ALL).
5. Wallet Editor worker (`automation.worker.ensure_worker_started`).
6. Filtered schedule thread.
7. Polling.

Порядок **остановки** (сейчас в mixed **нет** graceful shutdown: `run_polling(close_loop=False)`, sender loop `run_forever` daemon, schedule_loop daemon). Зафиксировать как цель, не реализовывать в первом code PR:

1. Прекратить polling (не принимать новые updates).
2. Остановить schedule thread (не диспатчить новые jobs).
3. Дождаться / отказать новые `dispatch_job_*` / `request_job`.
4. Остановить profile worker queues (`automation.worker`).
5. Остановить sender loop (`integrations.telegram_bot.loop.stop` / join thread) — сегодня stop API **нет**.
6. Не считать `close_loop=False` контрактом isolated shutdown, пока нет явной реализации.

Первый code PR **не** вводит новый shutdown; только документирует, что после ошибки сборки шаги 3–7 не начинаются.

---

## 5. Обследование исходников (факты / неизвестное)

### 5.1 Токен, routes, workbook, `.env`

| Тема | CONFIRMED | Следствие для isolated |
|------|-----------|------------------------|
| Token | Mixed: `BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")` после импортов; `main` бросает, если пусто. `telegram_bot`: `load_dotenv()` затем `TELEGRAM_TOKEN` на import. Platform Raccoon в survey — `TG_BOT_TOKEN` (другой репозиторий). | Isolated читать **`TELEGRAM_BOT_TOKEN` после dotenv**, до `telegram_bot`. Не переключать на `TG_BOT_TOKEN`. |
| `.env` | Только `telegram_bot` (и CLI tools). Railway задаёт env без файла. | Явный dotenv в entry **после** gate, один раз. Не импортировать `telegram_bot` ради dotenv. |
| Workbook | `RULES_XLSX_PATH` → `AccessRules` → `get_snapshot_v2` / `rules_provider` → `dropbox_watcher.download_file`. Конструктор не качает файл. | Сборка (шаг 5) workbook не обязана читать. Fail-fast snapshot — шаг 6 (следующий code). |
| Routes | `ALLOWED_TELEGRAM_ROUTE_KEYS` содержит и Antares (`platform_*`, `conversion_wallet_editor`, `bakai_*`, WE), и **`raccoon_*`**, и `analiz_*`. Jobs Antares шлют через `send_message_to_route` на platform/WE/bakai. `/status` mixed helper **не** фильтрует raccoon route keys. | Первый entry PR routes не режет. Чужие route keys в constants — не регистрация jobs. Фильтр status/routes — не минимальный code. |

### 5.2 Import-time sender loop и внешние clients

| Тема | CONFIRMED | Следствие |
|------|-----------|-----------|
| Sender loop | `telegram_bot` L346–356: `asyncio.new_event_loop()` + daemon `run_forever` **на import**, даже если token пуст (`bot is None`). | Isolated **не** импортировать `telegram_bot` до успешной сборки и до решения стартовать sender/polling. `handlers.cmd_status` делает lazy import `get_telegram_sender_health_snapshot` — сработает только при `/status`, не при `assemble_antares`. |
| Dropbox | `core.job_runner` → `rules_provider` → `import dropbox_watcher`. `_get_dbx()` lazy при download. | Import модуля Dropbox при сборке неизбежен, пока нет split `job_runner`. Соединение — при snapshot/download, не на import. |
| Playwright / engine | `register_jobs()` подгружает downloader-модули при **вызове** сборки; `automation.engine` — при WE task, не при assemble. | Сборка уже в TASK-16. Первый entry вызывает `assemble_antares`, не `engine.run`. |
| HTTPX Bot | `telegram_bot` создаёт `HTTPXRequest` + `Bot` на import. | Ещё один аргумент отложить import `telegram_bot`. |

### 5.3 Чужие schedules

| Тема | CONFIRMED |
|------|-----------|
| Loader | `core.schedules.load_schedules` возвращает **все** enabled rows workbook, без allowlist job_type. |
| Dispatch | `schedule_loop` для каждого `s.job_type` вызывает `dispatch_job_background` → `request_job`. |
| Unknown job | `JOB_REGISTRY.get` is None → event `job_failed` / `unknown_job_type`, **без** lock. Snapshot rules всё равно читается. |

Isolated **не** должен крутить raccoon/hello schedules. Фильтр: только `ANTARES_ASSEMBLY_JOB_TYPES` (семь keys) **перед** dispatch. Без фильтра чужая строка workbook даст failed events и лишний rules sync.

Фильтр schedules — **не** в первом code PR (там нет `schedule_loop`). Обязателен в PR, который включает шаг 8.

Содержимое prod `rules.xlsx` (есть ли raccoon rows на сервисе Test) — **UNKNOWN** (survey U12).

### 5.4 State, locks, tmp, очереди

| Ресурс | Путь / объект | Общий с mixed? |
|--------|----------------|----------------|
| Job PID locks | `STATE_DIR/locks/<job>.lock` (`job_runner`, default `/data/state`) | **да**, те же семь Antares keys + raccoon names если mixed жив |
| Scheduler clocks | in-memory `next_every` / `next_cron` процесса | нет шаринга между процессами |
| WE ingest tmp | `modules.antares.document_ingest.TMP_DIR` = `/tmp/wallet_editor` | **да**, если два процесса |
| WE results | `automation.runtime.WALLET_EDITOR_RESULT_DIR` = `/tmp/wallet_editor` | **да** |
| Auth state | `/tmp/auth_state_wallet_editor.json` / per-operator env | **да** |
| Durable WE | `{STATE_DIR}/wallet_editor/…` outbox/results | **да** |
| Sender queue | `telegram_bot.queue` asyncio, in-process | отдельная на процесс; **два polling** на один token — конфликт Telegram |
| Job executor | `core.job_dispatch` ThreadPoolExecutor, process-local | process-local |
| Profile workers | `automation.worker._profile_workers` Queue+Thread | process-local; диск/Antares UI — нет |

Первый isolated process **не** вводит отдельные `STATE_DIR` / tmp. Два живых процесса (mixed prod + isolated) на одном токене и `STATE_DIR` — **блокер cutover**, не блокер docs/первого code.

### 5.5 Общий worker / engine

`automation.worker` + `automation.engine` — единственный WE runtime (ingest, add/edit, auto-enable batch). Isolated Antares **должен** использовать тот же worker после старта компонентов. Raccoon downloaders worker **не** используют. Ядро `job_runner.request_job` общее; изоляция — составом `JOB_REGISTRY` после `assemble_antares` (семь keys, TASK-16).

Split `job_runner` / отдельный engine **не** входят в первый entry PR.

---

## 6. Минимальный следующий code PR

**Один** code PR после этого плана (предлагаемое имя TASK-18 / `feat/task-…-18-antares-entrypoint-boot`):

Добавить:

- `apps/antares.py` — `main` / `__main__`
- isolated gate (новая функция; mixed `enforce_legacy_scheduler_profile` **без** правок поведения)
- вызов: gate → dotenv → token → AccessRules+logger → `assemble_antares` → успех: процесс завершается с 0 **или** остаётся idle без polling (фиксируется в code PR: предпочтение **exit 0 после сборки**, чтобы случайно не крутить Telegram)
- child-harness тесты

Не включать в этот PR:

- `Application.run_polling`, `ensure_worker_started`, `schedule_loop`
- фильтр schedules (нужен только со loop)
- изменение mixed gate / `PROJECT_PROFILE=antares` на `scheduler.py`
- Railway / Nixpacks / профили сервиса
- `JOB_ACCEPT`, durable inbox, cutover
- split `job_runner`, отложенный import `dropbox_watcher`
- graceful shutdown sender loop
- отдельный `STATE_DIR`

Следующий **после** boot-PR: start order § 4 шаги 6–9 + schedule allowlist + subprocess «ошибка сборки ⇒ нет polling».

---

## 7. Subprocess-проверки первого code PR

Все в **отдельном** процессе (как TASK-16 harness). Pytest родителя не импортирует `apps.antares` так, чтобы обойти gate.

| # | Проверка | Ожидание |
|---|----------|----------|
| 1 | `PROJECT_PROFILE` unset / `""` / raccoon / wr / мусор | exit 2, нет `assemble_antares` успеха, нет `telegram_bot` в `sys.modules` |
| 2 | `PROJECT_PROFILE=antares`, token пуст | отказ до `telegram_bot` import / до polling |
| 3 | `antares` + валидные rules/logger stubs, чистый registry | `assemble_antares` успех; `set(JOB_REGISTRY)==` семь keys |
| 4 | чужой ключ в registry до сборки | `AntaresAssemblyError`, exit ≠ 0, нет `run_polling` |
| 5 | после успеха/отказа | нет `integrations.tg_commands`, `integrations.raccoon_jobs` в `sys.modules` |
| 6 | `python scheduler.py` с `PROJECT_PROFILE=antares` (регресс mixed) | по-прежнему exit 2 (существующие `test_project_profile_boot`) |

Не запускать живой Telegram, Dropbox download, Playwright, PG. Sitecustomize ребёнка — как TASK-16 (telegram/playwright stubs **не** маскируют запрещённый raccoon import).

Docs-only TASK-17: pytest **не** запускать.

---

## 8. Блокеры и UNKNOWN

Не блокеры этого **плана** (можно review документов):

- отсутствие `apps/antares.py` — это следующий code;
- mixed gate отклоняет `antares` — **намеренно**;
- `JOB_ACCEPT` / cutover / Railway.

Блокеры **первого code**, если их не учесть в scope:

1. Import `telegram_bot` стартует sender loop — entry не должен импортировать его «за компанию».
2. Грязный процесс после частичной регистрации (TASK-16): отказ сборки проверять **новым** процессом.

Блокеры **запуска** isolated (не этот план, не boot-PR):

3. `load_schedules` без allowlist — чужие rows workbook.
4. Общий `STATE_DIR`/locks/tmp с живым mixed и **один** `TELEGRAM_BOT_TOKEN` (двойной polling).
5. Prod start всё ещё `scheduler.py` — isolated не выйдет на Railway без ops-PR.
6. Нет API остановки sender loop.

UNKNOWN:

- U12: исполняются ли raccoon schedules на сервисе Test.
- Нужен ли отдельный `STATE_DIR` уже во втором code PR или только при cutover.
- Когда ослаблять mixed gate (только после доказанного isolated start, отдельное решение).
