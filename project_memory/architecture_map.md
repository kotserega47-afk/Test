# Architecture Map — `analizis`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Статус документа** | draft |
| **Последнее обновление** | 2026-05-24 |
| **Метод** | Stage A1 — extraction из кода и repo-документов (без изменений runtime) |

---

## Назначение

`analizis` — управляемая аналитическая платформа для платёжных операций: автоматическая выгрузка Excel из веб-кабинетов (Antares, Raccoon), анализ conversion / payout / wallet / hourly-метрик, доставка отчётов и алертов в Telegram. Бизнес-пороги и доступы задаются через `rules.xlsx` в Dropbox; архитектурные параметры — YAML в `config/`.

Основные контуры:

| ID | Контур | Статус |
|----|--------|--------|
| R1 | Raccoon wallet-аналитика (PayIn, правила, Telegram) | CONFIRMED |
| R2 | Raccoon hourly-отчёт (скачивание + агрегация + dedup) | CONFIRMED |
| R3 | Raccoon daily conversion (сводка за вчера, 00:00 MSK) | CONFIRMED |
| R4 | Antares conversion/payout через Dropbox + `main.py` | CONFIRMED |
| R5 | Antares wallet-аналитика (ручной TG / CLI) | CONFIRMED |
| R6 | Antares полный downloader → Dropbox → анализ | CONFIRMED (не в `scheduler`; отдельный запуск) |
| R7 | Bakai rate monitor | CONFIRMED (только TG-команда) |
| R8 | Antares hourly (`hourly_downloader` + `hourly_report`) | DORMANT (нет импортов из prod entry) |
| R9 | Event model / `core/events.py` | DORMANT (файл пуст) |
| R10 | Rules edit через Telegram-бот (CONTRACT_BOT) | DOCS_ONLY (в коде нет handlers редактирования rules) |

---

## 1. Production Entrypoints

### 1.1 Primary (deploy)

| Поле | Значение | Статус |
|------|----------|--------|
| **Deploy target** | Railway (`railway.toml`, Nixpacks `nixpacks.toml`) | CONFIRMED |
| **Service name** | `file-analyzer` | CONFIRMED |
| **Run command** | `/opt/venv/bin/python scheduler.py` | CONFIRMED |
| **Builder** | NIXPACKS; venv `/opt/venv`; Playwright Chromium в install phase | CONFIRMED |
| **Procfile** | Содержит только `postinstall` (apt + playwright), не `web:`/`worker:` | CONFIRMED — **не** primary start command |

**Startup flow (`scheduler.py` → `main()`):**

1. Fail-fast, если пуст `TG_BOT_TOKEN` (`scheduler.py`).
2. `Application.builder().token(BOT_TOKEN).build()` — Telegram long polling.
3. Регистрация handlers из `integrations/tg_commands.get_handlers()`.
4. `RULES.get_snapshot(force_sync=True)` — принудительная синхронизация `rules.xlsx` при старте.
5. Фоновые daemon-потоки (см. §6 Scheduler Map).
6. `app.run_polling(close_loop=False)` — блокирующий основной поток.

**Зависимости при старте:** `TG_BOT_TOKEN`, Dropbox credentials (через `rules_provider` / `dropbox_watcher`), `RULES_XLSX_PATH`, Playwright/Chromium (для фоновых job), env для Raccoon (`RACCOON_LOGIN` / `PASSWORD`), chat IDs для отчётов.

**Consumers:** один long-running контейнер Railway; операторы — через Telegram-команды.

| Статус использования | CONFIRMED active |

---

### 1.2 Secondary entrypoints (`if __name__ == "__main__"`)

| Entry | Модуль | Trigger | Consumers | Статус |
|-------|--------|---------|-----------|--------|
| E2 | `main.py <filename>` | CLI / вызов из `downloader.py` | `process_file()` → analyzers | CONFIRMED |
| E3 | `integrations/downloader.py` | CLI / отдельный Railway job (не в `scheduler`) | `run_download()` → Dropbox + `main.process_file` | CONFIRMED; prod wiring — **UNKNOWN** |
| E4 | `integrations/downloader_wallets.py` | CLI / `/run_wallet` | `run_wallet_cycle()` | CONFIRMED |
| E5 | `integrations/raccoon_wallet_downloader.py` | CLI / scheduler + `/run_raccoon` | `run_raccoon_wallet_cycle()` | CONFIRMED |
| E6 | `integrations/bakai_monitor_playwright.py` | CLI / `/run_rate` | `check_bakai_rate()` | CONFIRMED |
| E7 | `integrations/hourly_downloader.py` | Только `__main__` (если есть) | — | DORMANT — **нет импортов** в репозитории |
| E8 | `scheduler.py` | `__main__` | то же, что prod | CONFIRMED |

**`main.py` startup:** `acquire_lock("/tmp/dropbox_pipeline.lock")` → `process_file(argv[1])` → `release_lock()`.

---

### 1.3 Transport / sidecar modules (import-time entry)

| Модуль | Роль при import | Статус |
|--------|-----------------|--------|
| `integrations/telegram_bot.py` | Поднимает фоновый asyncio loop + worker очереди; требует `TELEGRAM_BOT_TOKEN` | CONFIRMED |
| `integrations/dropbox_watcher.py` | Инициализация Dropbox client; fail при отсутствии токенов | CONFIRMED |

**Риск:** `scheduler` использует `TG_BOT_TOKEN`, `telegram_bot` — `TELEGRAM_BOT_TOKEN`. В `.env` могут быть оба; поведение при несовпадении — **UNKNOWN** (STALE_RISK).

---

## 2. Runtime Pipelines

### P1 — Dropbox dispatch (conversion / payout / card)

| | |
|---|---|
| **Trigger** | `main.process_file(filename)` — CLI; из `downloader.run_download()` после upload |
| **Entry** | `main.py` |
| **Happy path** | 1. `get_analyzer(filename)` по `config/analysis_map.yaml` 2. `download_file` из `DROPBOX_INPUT_PATH` → `/tmp` 3. Card/CD: сохранить `last_card_path`, move to processed 4. Иначе: `analyzer.run(**kwargs)` с `card_files` / `col_mapping` 5. `move_file` в `DROPBOX_PROCESSED_PATH` с датой в имени |
| **Failure path** | Ошибка download → log + `send_message_sync`; ошибка analyzer → log + Telegram, файл **не** moved; ошибка move → log + Telegram |
| **Side effects** | Dropbox read/write; Telegram (`TELEGRAM_CHAT_ID_ANALIZ` в analyzers); in-process `last_card_path` |
| **Lock** | `run_once_guard` при CLI entry только |
| **Статус** | CONFIRMED |

**Analyzers:** `conversion` (`file_pattern: conversion`), `payout` (`payout`); оба требуют card/cd aux (**CONFIRMED** в `selector.py`).

---

### P2 — Antares full download + analyze

| | |
|---|---|
| **Trigger** | `integrations/downloader.run_download()` — `__main__` или внешний cron (**UNKNOWN** в repo) |
| **Happy path** | Playwright Antares → card, conversion (payin −2d), cd, payout → upload Dropbox → `acquire_lock` → `process_file(conversion+card)` → optional `process_file(payout+cd)` |
| **Failure path** | Exception в browser → Telegram + raise; lock busy → skip analyze, notify |
| **Side effects** | `/tmp/downloads`, auth `auth_state.json`, Dropbox input |
| **Статус** | CONFIRMED (код); scheduled prod — **UNKNOWN** |

---

### P3 — Antares wallet

| | |
|---|---|
| **Trigger** | `/run_wallet` (TG) или `python integrations/downloader_wallets.py` |
| **Happy path** | Playwright payin/payout по `wallet_config.yaml` → `analyze_wallets(payin, payout)` → Telegram + rules |
| **Failure path** | `__main__` → `send_message_sync` с ошибкой |
| **Статус** | CONFIRMED |

---

### P4 — Raccoon wallet

| | |
|---|---|
| **Trigger** | Scheduler: `run_hourly_at_minute(..., minute=0)`; TG: `/run_raccoon` |
| **Happy path** | Playwright Raccoon payin only (`payout` download закомментирован) → `analyze_raccoon_wallets(payin, payout=None)` |
| **Failure path** | Exception → log; TG job — traceback в чат |
| **Config** | `config/raccoon_wallet_config.yaml` |
| **Статус** | CONFIRMED |

---

### P5 — Raccoon hourly report

| | |
|---|---|
| **Trigger** | Scheduler: каждые `RACCOON_HOURLY_EVERY_MIN` (default 5); TG: `/run_hourly_raccoon` |
| **Happy path** | `run_hourly_raccoon_cycle()` → `/tmp/hourly_raccoon/payin.xlsx` → `run_hourly_report()` → fingerprint vs `last_sent.json` → Telegram `TELEGRAM_CHAT_ID_HOURLY_RACCOON` |
| **Failure path** | Missing payin → `FileNotFoundError`; empty interval → skip send; exception в scheduler thread → log, цикл продолжается |
| **Config** | `config/raccoon_hourly_report.yaml` |
| **Статус** | CONFIRMED |

---

### P6 — Raccoon daily conversion

| | |
|---|---|
| **Trigger** | Scheduler thread `run_daily_conversion_loop`: MSK 00:00–00:02, once per date |
| **Input** | Hardcoded `/tmp/hourly_raccoon/payin.xlsx` |
| **Happy path** | `run_daily_conversion_report(payin_path)` → агрегация за вчера → Telegram `TELEGRAM_CHAT_ID_RACCOON_WALLET` |
| **Failure path** | Empty day → log, return None |
| **Статус** | CONFIRMED |

---

### P7 — Bakai rate monitor

| | |
|---|---|
| **Trigger** | TG `/run_rate` only (не в scheduler loops) |
| **Happy path** | Playwright `bakai.kg` → compare with `/tmp/bakai_last_buy_rate.txt` → Telegram current/alert chats |
| **Failure path** | 3 retries; final fail → message + optional screenshot file |
| **Window** | 08:00–23:55 MSK (`_in_time_window`) |
| **Статус** | CONFIRMED |

---

### P8 — Telegram command routing

| | |
|---|---|
| **Trigger** | Incoming Telegram commands |
| **Happy path** | `check_access(RULES, ctx, command)` → `_run_job` (single-flight `asyncio.Lock`) → executor runs sync job |
| **Failure path** | deny_message по reason; concurrent job → «Уже выполняется» |
| **Commands** | `start`, `help`, `status`, `whoami`, `reload_rules`, `run_wallet`, `run_rate`, `run_raccoon`, `run_hourly_raccoon` |
| **Статус** | CONFIRMED |

---

### P9 — Antares hourly (legacy)

| | |
|---|---|
| **Modules** | `integrations/hourly_downloader.run_hourly_cycle`, `analyzers/hourly_report.run_hourly_report` |
| **Trigger** | **Нет ссылок** из `scheduler.py` / `tg_commands.py` |
| **Статус** | DORMANT |

---

## 3. Integrations

| ID | Интеграция | Producer | Consumer | Direction | Criticality | Статус |
|----|------------|----------|----------|-----------|-------------|--------|
| I1 | **Dropbox API** | `dropbox_watcher` | main, analyzers, `rules_provider`, conversion (special_cards) | Bi (read/write/move) | High — файлы и rules | CONFIRMED |
| I2 | **Telegram Bot API** | `telegram_bot`, `python-telegram-bot` polling | Все analyzers, downloaders, monitors | Outbound | High | CONFIRMED |
| I3 | **Antares** (`antares.plus`) | Playwright downloaders | wallet, conversion pipelines | In (export xlsx) | High для Antares контуров | CONFIRMED |
| I4 | **Raccoon** (`raccoon.it.com`) | `raccoon_*_downloader` | wallet/hourly/daily analyzers | In | High для Raccoon | CONFIRMED |
| I5 | **Bakai** (`bakai.kg`) | `bakai_monitor_playwright` | Rate alerts | In | Medium (on-demand) | CONFIRMED |

**Env (подмножество, CONFIRMED в коде):**

| Variable | Used by |
|----------|---------|
| `DROPBOX_*` | dropbox_watcher |
| `DROPBOX_INPUT_PATH`, `DROPBOX_PROCESSED_PATH` | main, downloader, payout |
| `DROPBOX_SPECIAL_PATH` | conversion |
| `RULES_XLSX_PATH` | rules_provider, access, analyzers |
| `RULES_SYNC_MIN_INTERVAL_SEC` | rules_provider (default 60) |
| `TG_BOT_TOKEN` | scheduler |
| `TELEGRAM_BOT_TOKEN` | telegram_bot |
| `TELEGRAM_CHAT_ID_*` | per-analyzer channels |
| `ANTARES_LOGIN/PASSWORD` | Antares downloaders |
| `RACCOON_LOGIN/PASSWORD` | Raccoon downloaders |
| `PLAYWRIGHT_HEADLESS` | all Playwright |
| `RACCOON_HOURLY_EVERY_MIN`, `RESTART_GRACE_MIN` | scheduler |

---

## 4. Persistence

| Хранилище | Source of truth | Read paths | Write paths | Статус |
|-----------|-----------------|------------|-------------|--------|
| **Dropbox input folder** | Операционная выгрузка / downloader upload | `main`, `conversion` (indirect) | `downloader._upload_local_to_dropbox` | CONFIRMED |
| **Dropbox processed** | Архив обработанных | — | `main.move_file`, payout move | CONFIRMED |
| **`rules.xlsx` (Dropbox)** | Control plane (бизнес-правила, access) | `rules_provider` → `/tmp/rules_cache/rules.xlsx` | DOCS_ONLY via bot; **нет upload в коде TG** | CONFIRMED read |
| **`special_cards.xlsx` (Dropbox)** | Партнёрские start_date для conversion | `conversion.run` download | **UNKNOWN** write path in repo | CONFIRMED read |
| `/tmp/rules_cache/rules.xlsx` | Кэш snapshot rules | `access_rules`, `config_manager`, wallet analyzers | `rules_provider` download | CONFIRMED |
| `/tmp/dropbox_pipeline.lock` | Mutex single-flight | `run_once_guard` | same | CONFIRMED |
| `/tmp/hourly_raccoon/payin.xlsx` | Latest Raccoon payin export | hourly + daily reports | `raccoon_hourly_downloader` | CONFIRMED |
| `/tmp/hourly_raccoon/last_sent.json` | Dedup state hourly report | `raccoon_hourly_report` | same | CONFIRMED |
| `/tmp/bakai_last_buy_rate.txt` | Last seen rate | bakai monitor | same | CONFIRMED |
| Playwright `storage_state` JSON | Session cookies | `auth_state*.json` paths per downloader | Playwright save | CONFIRMED |
| `config/*.yaml` | Repo (deploy artifact) | analyzers, selector | deploy / git only | CONFIRMED |
| In-process `main.last_card_path` | Ephemeral pairing | conversion/payout in same process | main | CONFIRMED (STALE_RISK между процессами) |

**Дублирование rules paths (STALE_RISK):**

- `rules_provider`: `/tmp/rules_cache/rules.xlsx` + `RULES_XLSX_PATH`
- `dropbox_watcher.download_rules_xlsx`: `RULES_LOCAL_PATH` (default `/tmp/rules/rules.xlsx`) + `DROPBOX_RULES_PATH` — функция **не вызывается** elsewhere | DORMANT |

---

## 5. Runtime Contracts

### 5.1 File / Excel contracts

| Contract | Location | Consumers | Статус |
|----------|----------|-------------|--------|
| **rules.xlsx** sheets: `access`, `commands`, `exclude_time`, `wallet_limits`, `thresholds_partner`, `meta` | Dropbox (`RULES_XLSX_PATH`) | `CONTRACT_RULES.md`, `access_rules`, `config_manager`, wallet analyzers | CONFIRMED (doc + code); полная валидация `meta` в runtime — **UNKNOWN** |
| **analysis_map.yaml** | `config/` | `analyzers/selector.py` | CONFIRMED |
| **conversion_config.yaml** | `config/` | `conversion.py` (columns, pools, statuses) | CONFIRMED |
| **payout_config.yaml** | `config/` | `payout.py` | CONFIRMED |
| **wallet_config.yaml** | `config/` | `wallet_analyzer`, `downloader_wallets` | CONFIRMED |
| **raccoon_wallet_config.yaml** | `config/` | raccoon wallet path | CONFIRMED |
| **raccoon_hourly_report.yaml** | `config/` | `raccoon_hourly_report.py` | CONFIRMED |
| **hourly_report.yaml** | `config/` | `hourly_report.py` (dormant pipeline) | DORMANT |
| Input xlsx column names | Antares/Raccoon exports | pandas loaders | CONFIRMED implicit |

### 5.2 Code models (in-process)

| Model | Module | Fields / role | Статус |
|-------|--------|---------------|--------|
| `RulesSnapshot` | `core/rules_provider.py` | `local_path`, `rules_version` (sha256), `stat_key`, `loaded_at_ts`, `source` | CONFIRMED |
| `Snapshot` (access) | `core/access_rules.py` | `access_map`, `commands_map`, `stat_key`, … | CONFIRMED |
| `CommandRule` | `core/access_rules.py` | `required_level`, `allow_private`, `allow_groups`, `enabled` | CONFIRMED |
| `ValidationResult` | `core/config_manager.py` | `ok`, `errors`, `warnings`, `df_norm` | CONFIRMED |
| `AccessContext` | `core/access_guard.py` | `chat_type`, `chat_id`, `user_id` | CONFIRMED |
| Analyzer `run()` return | `conversion`, `payout` | `dict` with `summary`, etc. | CONFIRMED |
| Hourly fingerprint payload | `raccoon_hourly_report` | JSON + sha256 `hash` in state | CONFIRMED |

### 5.3 API contracts

| API | Contract | Статус |
|-----|----------|--------|
| Telegram Bot HTTP | `send_message_sync(text, chat_id)` — chat_id required | CONFIRMED |
| Telegram polling commands | See P8; allow/deny via `commands` sheet | CONFIRMED |
| Dropbox | `download_file`, `upload_file`, `move_file`, `list_files` | CONFIRMED |

### 5.4 Document-only contracts

| Document | Scope | Статус |
|----------|-------|--------|
| `CONTRACT_BOT.md` | Bot edits rules.xlsx, audit events | DOCS_ONLY (no edit handlers in `tg_commands`) |
| `CONTRACT_RULES.md` | rules.xlsx schema v3 | DOCS_ONLY + partial code validation |
| `ARCHITECTURE.md` | Control/Execution/Observability planes | DOCS_ONLY (частично расходится с `scheduler` — см. риски) |

---

## 6. Scheduler Map

| Job / loop | Interval / trigger | Module(s) | Locks / concurrency | Retries | Restart semantics | Статус |
|------------|-------------------|-----------|---------------------|---------|-------------------|--------|
| **Telegram polling** | Continuous | `scheduler.main` | TG jobs: `_running_lock` (1 manual job) | — | Process restart via `restart_worker` | CONFIRMED |
| **run_raccoon_wallet** | Every hour at `:00` MSK | `run_hourly_at_minute` → `run_raccoon_wallet_cycle` | `job_start`/`job_end` counter | Loop catches Exception, continues | Waits `RESTART_GRACE_MIN` before `os._exit(1)` | CONFIRMED |
| **run_hourly_raccoon** | Every `RACCOON_HOURLY_EVERY_MIN` (default 5) | `run_every_minutes` → download + report | same | same | same | CONFIRMED |
| **daily_conversion** | MSK 00:00–00:02, once/date | `run_daily_conversion_loop` | No job counter | — | same | CONFIRMED |
| **restart_worker** | Fixed MSK times: 10:30, 13:30, 16:30, 19:30, 22:30, 01:30 | `restart_worker` | Waits until `active_jobs()==0` or grace exceeded | — | `os._exit(1)` → Railway container restart | CONFIRMED |
| **dropbox_pipeline lock** | Per `main` / `downloader` analyze burst | `run_once_guard` | File lock, stale after 600s | — | — | CONFIRMED |
| **Antares downloader** | Not in scheduler | `downloader.py` | Uses pipeline lock on analyze phase | — | — | UNKNOWN schedule |
| **Bakai rate** | Manual only | TG | — | 3 attempts in `run_rate_monitor_safe` | — | CONFIRMED |

**Нет в коде:** APScheduler, cron expressions, Celery, OS cron definitions (кроме Railway start command).

---

## 7. Cache Map

| Cache | Owner | Key / TTL | Consumers | Invalidation | Статус |
|-------|-------|-----------|-----------|--------------|--------|
| **rules snapshot** | `core/rules_provider` | `(mtime,size)` + TTL `RULES_SYNC_MIN_INTERVAL_SEC` | access, config_manager, analyzers | `force_sync=True`, file change, download fail → last good | CONFIRMED |
| **AccessRules Snapshot** | `core/access_rules` | `stat_key` of cached xlsx | `access_guard`, tg_commands | `RULES.invalidate()`, rules file change | CONFIRMED |
| **exclude_time ValidationResult** | `core/config_manager._EXCLUDE_TIME_CACHE` | per path + stat_key | wallet analyzers | file change, `clear_rules_caches()` | CONFIRMED |
| **wallet_limits load** | `config_manager.get_wallet_limits_df` | reads via rules snapshot each call; no separate module cache | wallet analyzers | rules sync | CONFIRMED |
| **hourly report dedup** | `/tmp/hourly_raccoon/last_sent.json` | content hash fingerprint | `raccoon_hourly_report` | new hash / day rollover logic | CONFIRMED |
| **Bakai last rate** | `/tmp/bakai_last_buy_rate.txt` | float file | bakai monitor | on rate change | CONFIRMED |
| **Playwright auth state** | per-downloader JSON files | filesystem | respective downloaders | manual delete / re-login | CONFIRMED |
| **main.last_card_path** | `main` module global | in-memory | conversion/payout in same process | new card file | CONFIRMED |
| **Notify throttle** | `config_manager._NOTIFY_STATES` | hash + cooldown 60 min | exclude_time warnings | content change | CONFIRMED |

---

## 8. Sources of Truth

| Данные | Источник | Приоритет | Статус |
|--------|----------|-----------|--------|
| Бизнес-правила (exclude, limits, thresholds, access) | `rules.xlsx` @ Dropbox | Highest (per `PROJECT_REFERENCE`, `CONTRACT_RULES`) | CONFIRMED |
| Архитектурные параметры (партнёры, окна, UI mapping) | `config/*.yaml` in repo | Below rules | CONFIRMED |
| Маршрутизация analyzer по имени файла | `config/analysis_map.yaml` | — | CONFIRMED |
| Пороги conversion exclude (legacy) | `conversion_config.yaml` `pools.*.exclude` | Coexists with rules in wallet paths | CONFIRMED (dual model for conversion) |
| Операционные Excel | Antares/Raccoon exports | Input artifacts | CONFIRMED |
| special_cards start dates | Dropbox `special_cards.xlsx` | conversion only | CONFIRMED |
| Права TG команд | `rules.xlsx` sheets `access` + `commands` | — | CONFIRMED |
| Документация «бот редактирует rules» | `CONTRACT_BOT.md` | — | DOCS_ONLY vs code |

---

## 9. Runtime Invariants

| ID | Invariant | Статус |
|----|-----------|--------|
| INV1 | Без `TG_BOT_TOKEN` scheduler не стартует | CONFIRMED |
| INV2 | Без Dropbox credentials `dropbox_watcher` raise at import | CONFIRMED |
| INV3 | `send_message_sync` / `send_file_sync` требуют явный `chat_id` | CONFIRMED |
| INV4 | Одновременно один manual TG job (`_running_lock`) | CONFIRMED |
| INV5 | `run_once_guard`: второй процесс с тем же lock file пропускает analyze (timeout 600s) | CONFIRMED |
| INV6 | Scheduled threads increment `_active_jobs` — restart ждёт завершения (до grace) | CONFIRMED |
| INV7 | `rules_provider` fail-safe: при ошибке sync отдаёт последний valid snapshot если файл есть | CONFIRMED |
| INV8 | `access_guard` fail-closed при `rules_not_ready` / `rules_invalid` | CONFIRMED |
| INV9 | `get_exclude_time_df` fatal validation → `RuntimeError` (stops wallet analyzer path) | CONFIRMED |
| INV10 | Raccoon wallet cycle: payout download disabled in code — analyzer always `payout_path=None` | CONFIRMED |
| INV11 | `core/events.py` empty — no event append implementation | CONFIRMED |

---

## 10. Risks / Unknowns

| ID | Risk / unknown | Тег | Evidence |
|----|----------------|-----|----------|
| U1 | `TG_BOT_TOKEN` vs `TELEGRAM_BOT_TOKEN` split | STALE_RISK | `scheduler.py` vs `telegram_bot.py` |
| U2 | Запуск `integrations/downloader.py` в prod (отдельный Railway cron/service?) | UNKNOWN | Not referenced in `scheduler.py` / `railway.toml` |
| U3 | `PROJECT_REFERENCE` / `ARCHITECTURE.md`: «scheduler не запускает job автоматически» | STALE_RISK | Contradicts `scheduler.py` background threads |
| U4 | `CONTRACT_BOT` rule-editing via Telegram | DOCS_ONLY | No edit handlers in `tg_commands.py` |
| U5 | `download_rules_xlsx` vs `rules_provider` duplicate paths | STALE_RISK | `DROPBOX_RULES_PATH` / `/tmp/rules/` unused |
| U6 | `core/events.py` observability contract | DORMANT | Empty file; `ARCHITECTURE.md` describes events |
| U7 | Antares hourly pipeline wired to prod | UNKNOWN | No imports of `hourly_downloader` / `hourly_report` |
| U8 | `main.last_card_path` не shared между процессами | STALE_RISK | Global in `main.py`; separate CLI invocations |
| U9 | Полная runtime-валидация `rules.xlsx` `meta.version` | UNKNOWN | Documented in `CONTRACT_RULES`; not verified in scanned paths |
| U10 | Secrets in repo `.env` | STALE_RISK | Grep shows tokens in `.env` (operational security) |
| U11 | `Procfile` не defines process type | UNKNOWN | May confuse Heroku-style deploy vs `railway.toml` |

---

## Runtime inventory (summary)

### Active in prod entry (`scheduler.py`)

| Component | Role | Статус |
|-----------|------|--------|
| `scheduler.py` | Prod entry, polling, background schedules, restart | CONFIRMED |
| `integrations/tg_commands.py` | Command routing | CONFIRMED |
| `integrations/raccoon_wallet_downloader.py` | Raccoon payin export | CONFIRMED |
| `integrations/raccoon_hourly_downloader.py` | Hourly payin export | CONFIRMED |
| `analyzers/raccoon_hourly_report.py` | Hourly Telegram report | CONFIRMED |
| `analyzers/raccoon_daily_conversion.py` | Midnight daily summary | CONFIRMED |
| `analyzers/raccoon_wallet_analyzer.py` | Wallet analytics | CONFIRMED |
| `core/rules_provider.py`, `access_rules.py`, `access_guard.py` | Rules + ACL | CONFIRMED |
| `core/config_manager.py` | exclude_time / wallet_limits validation | CONFIRMED |
| `integrations/dropbox_watcher.py`, `telegram_bot.py` | Transport | CONFIRMED |

### Dormant / manual-only

| Component | Reason | Статус |
|-----------|--------|--------|
| `integrations/hourly_downloader.py` | No imports | DORMANT |
| `analyzers/hourly_report.py` | No scheduler/TG wire | DORMANT |
| `integrations/downloader.py` | Not in scheduler | CONFIRMED code; UNKNOWN prod schedule |
| `main.py` (standalone) | CLI / called from downloader | CONFIRMED |
| `integrations/bakai_monitor_playwright.py` | TG manual only | CONFIRMED |
| `integrations/downloader_wallets.py` | TG / CLI | CONFIRMED |
| `core/events.py` | Empty | DORMANT |
| `dropbox_watcher.download_rules_xlsx` | Uncalled | DORMANT |

---

## Reading hints

- **Incident на Raccoon hourly:** P5 + §7 cache `last_sent.json` + `/tmp/hourly_raccoon/payin.xlsx`
- **Incident на access/TG:** P8 + `rules.xlsx` sheets + `rules_provider` fail-safe
- **Incident на conversion:** P1/P2 + `special_cards` + `conversion_config.yaml` pools
- **Deploy / restart:** §1.1 + §6 `restart_worker`
- **Не дублировать:** детальные таблицы rules columns → `CONTRACT_RULES.md`; task workflow → `project_memory/workflow.md` (если создан)

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | Stage A1: документ создан из codebase scan (`scheduler`, pipelines, integrations, config, contracts) |
