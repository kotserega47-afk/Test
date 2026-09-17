# Survey — modular Antares / Raccoon / WR (2026-09-17)

| Мета | Значение |
|------|----------|
| **Task** | [TASK-2026-09-17-01](../active_tasks/TASK-2026-09-17-01_modular_reorg_survey.md) |
| **Снимок** | 2026-09-17 (git + код); Railway connector **2026-09-17** (GPT review) |
| **Метод** | git ls-remote + clone/worktree read-only; код; KB; Railway `list_deployments` (пересказ review, не повторный вызов) |
| **Не смешивать** | `telegram_task_registry` / приложенный PROJECT_CONTEXT.md другого проекта |
| **AGENTS.md** | в репозитории Test **отсутствует** (поиск 2026-09-17) |

Секреты, значения токенов, содержимое `.env` в отчёт **не входят**.

---

## 1. Facts / Assumptions / UNKNOWN

### CONFIRMED (факты)

| ID | Факт | Источник |
|----|------|----------|
| F1 | Репозиторий Test: `https://github.com/deniskotdavydov1991-wq/Test.git` | `git remote origin` |
| F2 | Репозиторий Platform_2.0: `https://github.com/deniskotdavydov1991-wq/Platform_2.0.git` | `git ls-remote` |
| F3 | Test `origin/test_main` SHA **`535994c91a9829724204727f1346a387755755df`** — `Merge pull request #3 from kotserega47-afk/fix/wallet-editor-terminal-field` | `git fetch` 2026-09-17 |
| F4 | Test `origin/develop` SHA **`5fc9304c4552dca34ccf52a5b3ad53688185636c`** — далеко от `test_main` (KB bootstrap / старый код) | ls-remote |
| F5 | Platform `origin/develop` SHA **`ebbcd6c11b0c2418575f807edd40feb1c4fed468`** — `Deduplicate Raccoon missing threshold warnings by new operations` | clone 2026-09-17 |
| F6 | Platform `origin/main` SHA **`ea466780a44b3b110a7e392e71386df0296bd474`** — `Добавил 114 терминал`; **не** fast-forward к develop (`610` / `50` commits left-right) | `git rev-list` |
| F7 | Platform `origin/test_main` SHA **`636aee6ebe8b4a0e249f84caa8f7322e1a05f31d`** — `Удалён .env из репозитория` | ls-remote |
| F8 | Platform `origin/feature/bd_for_card` SHA **`3a2c65f04dab5bc05d0790dc96a2ebbf79d3509e`** | ls-remote |
| F9 | Оба репозитория: prod entry в deploy-конфиге = `python scheduler.py` (пути venv различаются) | `railway.toml` |
| F10 | Test Telegram: `TELEGRAM_BOT_TOKEN` + `app.run_polling()` в `scheduler.py` | Test `scheduler.py` |
| F11 | Platform Telegram: `TG_BOT_TOKEN` + `app.run_polling()` в `scheduler.py` | Platform `scheduler.py` |
| F12 | Код Test `scheduler.py` (тот же SHA, что задеплоен F25): смешанная **регистрация** Antares + Raccoon jobs + WalletEditor + script_jobs. Это не доказательство, что raccoon jobs **срабатывают** по расписанию в prod (U12) | `tg_commands.py`, `raccoon_jobs.py` |
| F13 | Platform `develop` — **Raccoon-only** runtime: Antares downloaders **не** импортируются из `scheduler.py`; `/run_wallet` — stub | Platform `scheduler.py` L188–214, `tg_commands.py` `cmd_run_wallet` |
| F14 | Wallet Editor (Playwright card edit, Auto-Enable, PG registry) есть **только в Test**; в Platform `develop` каталога `automation/` **нет** | file tree compare |
| F15 | Одноимённые Raccoon-файлы Test vs Platform **не эквивалентны** (blob SHA различаются для всех проверенных `.py`, кроме `run_once_guard.py`) | § 5 |
| F16 | Hardcoded site URLs: Antares `https://antares.plus/lkcard/#/…`; Raccoon `https://raccoon.it.com/partner/#/…`; Bakai `https://bakai.kg/ru/` | code |
| F17 | В Test **нет** `.github/workflows`; в Platform `develop` **нет** `.github/` | glob |
| F18 | Test PR **#2**: GitHub **Draft / OPEN**, HEAD **`7aadf49af2b4abc5bc24c93e07d284343f79a5d2`** (`fix(wallet-editor): hold AutoEnable lock with OS flock`). `#1` и `#3` влиты в `test_main` | GitHub 2026-09-17; `git ls-remote` |
| F19 | Platform: `refs/pull/*/head` **пусто** на момент съёмки git | ls-remote |
| F20 | Platform `develop` **содержит tracked `.env`** (`git ls-tree HEAD .env`). Platform `test_main` этот файл удалил. Содержимое не читалось и не цитируется. | git |
| F21 | Import Test `integrations/downloader.py`: `raise` без `TELEGRAM_CHAT_ID_ANALIZ`; `playwright install chromium`; `os.makedirs(/tmp/downloads)`; `TZ=Europe/Moscow` | L31–66 |
| F22 | Import Platform `analyzers/raccoon_hourly_report.py`: `raise` без `TELEGRAM_CHAT_ID_HOURLY_RACCOON` и `TELEGRAM_CHAT_ID_RACCOON_WALLET` | L44–51 |
| F23 | Import Test `raccoon_hourly_downloader.py` / `hourly_downloader.py`: `os.makedirs` рабочих каталогов | code |
| F24 | Локальный checkout Test (не этот PR): ветка `fix/wallet-editor-terminal-field` + **незакоммиченные** файлы; работа велась в **отдельном worktree** | `git status` исходного клона |
| F25 | Railway project `c20968a7-00b1-49d2-952b-2ca52bd5a626`. Test service `542c84d6-7e99-4657-bdf7-1da52f696946`, deployment `f5ecea80-e046-4cf7-a308-e3f75b05fb41`, status **SUCCESS**, branch **`test_main`**, SHA **`535994c91a9829724204727f1346a387755755df`** | Railway connector `list_deployments`, GPT 2026-09-17 |
| F26 | Raccoon service `0060b136-4c66-4ca6-8f2e-3471d456b70a`, deployment `468d3c96-0dd4-4210-a9b9-f07ba86f04ba`, status **SUCCESS**, branch **`develop`**, SHA **`ebbcd6c11b0c2418575f807edd40feb1c4fed468`** (репозиторий Platform_2.0 — по постановке и совпадению SHA с `origin/develop`) | то же |
| F27 | SUCCESS деплоя **не** подтверждает job health и **не** опровергает двойной Raccoon | ограничение источника F25/F26 |

### ASSUMPTIONS (не доказано продом)

| ID | Предположение | Зачем |
|----|---------------|-------|
| A1 | *(закрыто F25)* connected branch Test = `test_main`, SHA = `535994c…` | — |
| A2 | *(закрыто F26)* connected branch Raccoon = `develop`, SHA = `ebbcd6c…` | имя git-репо на коннекторе не цитировалось в пересказе; SHA совпадает с Platform `origin/develop` |
| A3 | Сервисы используют **разные** Telegram-токены (иначе два `run_polling` конфликтуют) | нужно подтвердить ops |
| A4 | На Railway volume `/data/state` задан только у Test (WalletEditor outbox / job locks) | `STATE_DIR` default `/data/state` в Test `core/job_runner.py`; Platform locks — in-process `threading.Lock` |
| A5 | Будущий WR — отдельный кабинет, **не** fork Antares UI без проверки | требование постановки |

### UNKNOWN

| ID | Что неизвестно |
|----|----------------|
| U1 | Job health / uptime процессов; restart policy; включён ли auto-deploy на каждый push в ветку (типично да, **не** проверено в UI) |
| U2 | Совпадают ли `TELEGRAM_BOT_TOKEN` (Test) и `TG_BOT_TOKEN` (Platform) |
| U3 | Prod rows `rules.xlsx` (schedules, access, telegram_routes) на каждом сервисе |
| U4 | Адрес, auth, DOM, функции **WR**; есть ли Wallet Editor / Auto-Enable / те же отчёты |
| U5 | *(закрыто F18)* PR #2 Draft/OPEN HEAD `7aadf49…` |
| U6 | CI на GitHub Actions (workflows отсутствуют в дереве; внешние checks UNKNOWN) |
| U7 | Staging |
| U8 | Использует ли prod Test `TELEGRAM_ROUTES_FROM_RULES_V2=1` |
| U9 | Display name в Railway UI vs `railway.toml` `file-analyzer`; точный connected **git repo URL** в коннекторе (в пересказе были service id / branch / SHA) |
| U10 | Расходятся ли `RACCOON_LOGIN` / browser sessions между двумя сервисами |
| U11 | Состояние локального `.env` в clone Platform (файл есть в working tree develop) — не читался |
| U12 | **Выполняет ли процесс Test в prod jobs `raccoon_*`** (enabled rows в rules, фактический `job_started`). Наличие модулей в репозитории Test **не** доказательство |

---

## 2. Ветки, SHA, пересечения с задачей

### Test (`deniskotdavydov1991-wq/Test`)

| Ref | SHA | Заметка |
|-----|-----|---------|
| `origin/test_main` | `535994c91a9829724204727f1346a387755755df` | **deployed** на Test service (F25); Wallet Editor PR #3 |
| `origin/develop` | `5fc9304c4552dca34ccf52a5b3ad53688185636c` | не использовать как prod-источник этой программы |
| PR **#2** | `7aadf49af2b4abc5bc24c93e07d284343f79a5d2` | Draft/OPEN; Auto-Enable flock; пересечение с WE / locks |

Пересечение: не смешивать PR #2 в одном merge с модульностью без review.

### Platform_2.0

| Ref | SHA | Заметка |
|-----|-----|---------|
| `origin/develop` | `ebbcd6c11b0c2418575f807edd40feb1c4fed468` | **deployed** на Raccoon service (F26) |
| `origin/test_main` | `636aee6ebe8b4a0e249f84caa8f7322e1a05f31d` | удаление `.env` из git; **не** вершина develop |
| `origin/main` | `ea466780a44b3b110a7e392e71386df0296bd474` | сильно расходится с develop |
| `feature/bd_for_card` | `3a2c65f04dab5bc05d0790dc96a2ebbf79d3509e` | не WR; смысл ветки **не** разбирался построчно |

---

## 3. Deploy, запуск, CI

| | Test `test_main` | Platform `develop` |
|--|------------------|---------------------|
| `railway.toml` start | `/usr/bin/tini -s -- /opt/venv/bin/python scheduler.py` | `[services.main] start = "/opt/venv/bin/python scheduler.py"` (без tini в toml) |
| Service name in toml | `file-analyzer` | `file-analyzer` (**то же имя файла конфигурации**) |
| `nixpacks.toml` | venv + playwright chromium + **tini** | есть, отличается blob |
| `Procfile` | playwright apt + chromium | аналогичный postinstall |
| Live deploy SHA (2026-09-17) | **`535994c…`** (F25); health **UNKNOWN** | **`ebbcd6c…`** (F26); health **UNKNOWN** |
| Auto-deploy on merge to connected branch | предполагается включённым; UI не менять в этой задаче | то же для `develop` |
| Telegram mode | long polling | long polling |
| Webhook | не найден в entry | не найден в entry |
| Workers | daemon `schedule_loop` + WalletEditor per-profile threads (`ensure_worker_started`) | 4 daemon threads: WalletReporter interval, HourlyReporter :00, daily conversion, `restart_worker` |
| CI | нет workflow-файлов | нет workflow-файлов |
| Tests | `tests/` pytest (локально) | `tests/unit/` (меньше набор) |

**Не считать отсутствие CI успешной проверкой.**

---

## 4. Карта функций

Легенда источника: **T** = Test `test_main`; **P** = Platform `develop`; **оба** = имя есть в обоих, поведение **не** считать одинаковым.

### 4.1 Jobs и расписания

| Функция | Код (T) | Кто исполняет (ориентир) | Триггер T | Триггер P | Настройки / данные | Внешние эффекты | Различие T vs P |
|---------|---------|--------------------------|-----------|-----------|--------------------|-----------------|-----------------|
| Antares hourly report | `analyzers/hourly_report.py`, `integrations/hourly_downloader.py`, `tg_commands.run_hourly_job` | сервис Test | `schedules` sheet → `job_type=hourly` + hourly gate; `/run_hourly` | **не стартует** из scheduler | Rules V2, `ANTARES_*`, `/tmp/hourly/` | Antares UI, TG `platform_hourly_report` / `TELEGRAM_CHAT_ID_HOURLY` | P: файлы dormant |
| Antares wallet cycle | `integrations/downloader_wallets.py` | Test | schedule `wallet`; `/run_wallet` | stub `/run_wallet` | `ANTARES_*`, `auth_state_wallets.json` | Antares, TG wallet route | P не запускает |
| Antares download+conversion+payout | `integrations/downloader.py`, `conversion_pipeline`, `analyzers/payout.py` | Test | schedule `download`; `/run_download` | файлы есть, scheduler не импортирует | Dropbox input, `TELEGRAM_CHAT_ID_ANALIZ` **required at import T** | Antares, Dropbox, TG, conversion WE hook | P DORMANT in process |
| Bakai rate | `integrations/bakai_monitor_playwright.py` | Test schedule `rate`; оба — `/run_rate` | schedule + TG | **только** `/run_rate` | bakai.kg, chat env/routes | Playwright, TG | разный wiring |
| Raccoon HourlyReporter (wallet analysis) | T: код `raccoon_wallet` job; P: `run_raccoon_wallet_cycle` thread :00 | **P: сервис Raccoon (F26).** T: код есть; **факт запуска в prod Test = U12** | schedule/TG **если** включено в rules (U3) | hardcoded hourly + `/run_raccoon` | Raccoon site, `RACCOON_*`, `/tmp/raccoon_wallet` | Raccoon UI, TG | разные registry; T Rules V2 vs P YAML |
| Raccoon WalletReporter (10-min payin) | T: код `raccoon_hourly`; P: `raccoon_wallet_report_pipeline` | **P: да (код scheduler). T: U12** | rules если enabled | `RACCOON_REPORTER_EVERY_MIN` default 10 | `/tmp/hourly_raccoon/` | Raccoon, TG | T lazy chat; P import-time raise |
| Raccoon daily conversion | T: код `raccoon_daily_conversion`; P: loop 00:00–00:02 | **P: да. T: U12** | rules schedule | hardcoded window | payin.xlsx | TG | разный запуск |
| Job locks T | `core/job_runner.py` PID file `{STATE_DIR}/locks/{job_type}.lock` | Test | request_job | — | STATE_DIR | skip second run | P: in-memory threading locks per `job_key`, **нет** PID files |
| Dropbox pipeline lock | `run_once_guard.py` `/tmp/dropbox_pipeline.lock` | Test download job | acquire | same file **если** кто-то вызовет downloader | /tmp | skip | blob **SAME** |
| Process restart | нет в Test scheduler | — | — | `RESTART_TIMES` 10:30…01:30 MSK; `ENABLE_HARD_RESTART` | — | `os._exit` optional | только P |

### 4.2 Telegram

| | Test | Platform |
|--|------|----------|
| Token env | `TELEGRAM_BOT_TOKEN` | `TG_BOT_TOKEN` |
| Access | `AccessRules` + rules.xlsx | то же имя класса, другой blob `core/access_rules.py` |
| Commands (T) | `/status` `/whoami` `/reload_rules` `/run_wallet` `/run_hourly` `/run_download` `/run_rate` `/run_raccoon` `/run_hourly_raccoon` `/rules_validate` `/auto_enable_*` `/wallet_editor_refresh` `/registry_*` `/run_script_hello` `/operator_wallets_ready` + Excel documents | `/status` `/whoami` `/reload_rules` `/run_wallet`(stub) `/run_rate` `/run_raccoon` `/run_hourly_raccoon` `/run_raccoon_analyzer` `/run_raccoon_reporter` |
| Extra P commands | нет analyzer/reporter split | download-only / report-only WalletReporter |
| Concurrent TG jobs | `dispatch_job_async` + PID lock | asyncio lock **один** job на процесс + per-key thread lock |
| WalletEditor ingest | `MessageHandler` documents | **нет** |

### 4.3 Wallet Editor (только Test)

| Функция | Код | Запуск | Данные | Эффекты |
|---------|-----|--------|--------|---------|
| Card edit | `automation/engine.py`, `wallet_terminal_field.py`, `wallet_form_helpers.py` | TG xlsx → per-profile worker | `WALLET_EDITOR_*`, auth `/tmp/auth_state_wallet_editor_<PROFILE>.json` | Antares Save, TG result, PG registry |
| Add / edit wallet | `add_wallet_engine.py`, `edit_wallet_engine.py` | Excel routing | те же профили | Antares |
| Auto-Enable | `wallet_editor_auto_enable*.py` | `/auto_enable_plan` `/auto_enable_run`; scheduler Phase C **open** в KB | PG + hold/Отлёжка | Antares + registry patch |
| Registry | `wallet_editor_registry*.py`, `wallet_editor_registry_db/` | workers, `/registry_*`, jobs refresh/replay | PostgreSQL SoT; Dropbox manual sheets | PG, TG export |
| HOLD | `wallet_editor_hold.py` | before add_partner | hold list | block Antares |
| Conversion hook | `conversion_wallet_editor_bridge.py` | after conversion | `CONVERSION_WALLET_EDITOR*` | enqueue WE |

**Защиты (сохранить; код Test `test_main` / PR #3):**

- unread chips / loading **не** равны пустому полю — `automation/wallet_terminal_field.py`;
- ошибка add блокирует зависимый `set_status` и Save — `automation/engine.py`;
- несохранённые изменения не становятся OK;
- Save подтверждается повторным чтением;
- ready привязан к проверенной карте и opening token.

**Не переносить в Raccoon/WR без доказательства тех же DOM-контрактов.**

### 4.4 Правила и конфиги

| | Test | Platform |
|--|------|----------|
| Rules V2 package | `core/rules_v2/` **есть** | **нет** |
| Raccoon wallet config | Rules V2 only (`resolve_raccoon_wallet_config`) | YAML `config/raccoon_wallet_config.yaml` |
| Schedules | `core/schedules.py` ← rules workbook | hardcoded threads + env interval |
| YAML retained T | `config/payout_config.yaml`, `config/raccoon_hourly_report.yaml` | полный набор yaml включая analysis_map, conversion, wallet |

### 4.5 Хранилища (не общие сущности)

| Ресурс | Test path / key | Platform | Изоляция сегодня |
|--------|-----------------|----------|------------------|
| Job PID locks | `{STATE_DIR}/locks/*.lock` | thread locks | разные механизмы |
| WE outbox | `{STATE_DIR}/wallet_editor/` | нет | только T |
| Antares auth | `/tmp/auth_state.json`, `auth_state_wallets.json`, hourly, WE per profile | dormant copies of downloaders | **одинаковые hardcoded `/tmp/...` имена** в коде Raccoon |
| Raccoon auth | `/tmp/auth_state_raccoon.json`, `/tmp/hourly_raccoon_auth.json` | те же пути в коде | **коллизия, если два процесса на одном host/volume** |
| Payin xlsx | `/tmp/hourly_raccoon/payin.xlsx` | то же | коллизия |
| PG | `DATABASE_URL` WE registry | нет psycopg в requirements P | только T |
| Dropbox | rules, downloads, WE manual | rules (legacy provider) | зависит от env (UNKNOWN) |

Одинаковые номера карт / партнёров / операторов **не** делают записи общими между проектами.

---

## 5. Таблица blob-различий (одноимённые файлы)

Сравнение `origin/test_main:<path>` vs Platform `develop HEAD:<path>`.

| Path | Результат |
|------|-----------|
| `run_once_guard.py` | **SAME** `6354d061e23189764d244a066a8f0e86f47a4201` |
| `scheduler.py` | DIFF |
| `integrations/tg_commands.py` | DIFF |
| `integrations/raccoon_hourly_downloader.py` | DIFF |
| `integrations/raccoon_wallet_downloader.py` | DIFF |
| `analyzers/raccoon_hourly_report.py` | DIFF |
| `analyzers/raccoon_wallet_analyzer.py` | DIFF |
| `analyzers/raccoon_daily_conversion.py` | DIFF |
| `integrations/hourly_downloader.py` | DIFF |
| `integrations/downloader.py` | DIFF |
| `integrations/downloader_wallets.py` | DIFF |
| `integrations/bakai_monitor_playwright.py` | DIFF |
| `core/rules_provider.py` | DIFF |
| `core/access_rules.py` | DIFF |
| `railway.toml` | DIFF |
| `nixpacks.toml` | DIFF |
| `main.py` | DIFF |

Только в Platform (среди `.py` вне tests): `job_runner.py` (корень), `core/events.py`.

Только в Test: WalletEditor, `core/rules_v2`, `core/job_runner.py`, script_jobs, conversion modernization, telegram_routes, и др. (~130+ модулей).

**Вывод:** источник истины для Antares + Wallet Editor = **Test `test_main` / deployed `535994c…`**. Источник истины для **текущего prod-поведения Raccoon** = **Platform `develop` / deployed `ebbcd6c…`**. Копии Raccoon в дереве Test — **другой код**, не доказательство, что сервис Test их исполняет (U12).

---

## 6. Жёстко заданное и побочные эффекты импорта

| Что | Где | Риск для модульности |
|-----|-----|----------------------|
| Antares URLs | downloaders, `automation/engine.py` `WALLET_URL` | WR нельзя подставить без адаптера |
| Raccoon URLs | raccoon downloaders | отдельный адаптер |
| Bakai URL | `bakai_monitor_playwright.py` | не проектный кабинет |
| Chat env names | `core/rules_v2/constants.py` (T); import raises (P hourly report, T downloader/payout) | смешанный процесс требует env «чужого» проекта |
| `file-analyzer` | оба `railway.toml` | путаница в Railway UI |
| `os.makedirs` /tmp | raccoon/hourly downloaders | импорт = FS write |
| `playwright install` | T `downloader.py` import | импорт = сеть/диск |
| `JOB_REGISTRY.update` | T `tg_commands`, `raccoon_jobs`, `script_jobs` import | импорт регистрирует jobs |
| `RULES = AccessRules(...)` | оба `tg_commands` module level | не браузер, но I/O при snapshot |

Цель: **import модуля проекта не должен** стартовать browser, polling, schedule, jobs.

---

## 7. STALE_RISK относительно KB Test

KB `architecture_map.md` / `tasks.md` **S2** («Raccoon отсутствует в `.py`») **устарело для дерева Test `test_main`**: модули и команды **есть в коде**. Это не закрывает U12 (исполнение в production Test). Prod Raccoon-only процесс — Platform `develop` (F26).

---

## 8. WR

В обоих деревьях **нет** идентификаторов WR (URL, модуль, job_type). Подключение WR **заблокировано** вопросами в ADR / migration § WR gate.
