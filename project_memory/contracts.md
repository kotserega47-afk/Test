# Contracts — `analizis`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Статус документа** | draft |
| **Последнее обновление** | 2026-05-24 |
| **Метод** | Stage A2 — extraction из `architecture_map.md` + codebase |
| **Связанные документы** | `project_memory/architecture_map.md`, `CONTRACT_RULES.md`, `CONTRACT_BOT.md`, `ARCHITECTURE.md`, `PROJECT_REFERENCE.md` |

> **Не хранить значения секретов** в KB — только имена переменных и семантика.

**Статусы:** CONFIRMED | DORMANT | UNKNOWN | DOCS_ONLY | STALE_RISK

**Breaking change** — изменение, ломающее потребителей без миграции.

---

## 1. Runtime Contracts

### RT-001 — Dropbox file dispatch (`main.process_file`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Скачать входной файл из Dropbox, выбрать analyzer, выполнить анализ, переместить в processed |
| **Producer** | `main.py` (CLI), `integrations/downloader.run_download()` |
| **Consumer** | `analyzers/selector.get_analyzer` → `analyzers.conversion.run` / `analyzers.payout.run` |
| **Runtime path** | `DROPBOX_INPUT_PATH/{filename}` → `/tmp/{filename}` → analyzer → `DROPBOX_PROCESSED_PATH/{renamed}` |
| **Source of truth** | Имя файла + `config/analysis_map.yaml`; pairing card — aux arg или `main.last_card_path` |
| **Failure modes** | Нет analyzer → warning, return; download fail → Telegram + return; analyzer exception → Telegram, file not moved; move fail → Telegram |
| **Notes** | `requires_card` для conversion/payout; lock только при `python main.py` entry |

---

### RT-002 — Analyzer routing (`get_analyzer`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Сопоставить имя файла с модулем analyzer |
| **Producer** | `analyzers/selector.py` (load `analysis_map.yaml` at import) |
| **Consumer** | `main.process_file` |
| **Runtime path** | substring match `file_pattern in filename.lower()` |
| **Source of truth** | `config/analysis_map.yaml` |
| **Failure modes** | No match → `(None, None, False)` → dispatch skipped |
| **Notes** | `requires_card=True` iff pattern in `{conversion, payout}` |

---

### RT-003 — Conversion analysis (`conversion.run`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Анализ conversion Excel + card; отчёт, Telegram, optional Excel |
| **Producer** | `main.process_file`, `downloader` chain |
| **Consumer** | `main` (reads `result["summary"]`) |
| **Signature** | `run(conv_file, card_files, col_mapping, *, generate_excel=True, send_telegram=True) -> dict` |
| **Runtime path** | Dropbox `special_cards.xlsx` + local conv/card → pandas → rules from YAML pools |
| **Source of truth** | `config/conversion_config.yaml`; exclude intervals in YAML `pools.*.exclude`; card pairing file |
| **Failure modes** | Missing column → `ValueError`; Telegram/Excel errors logged, partial outputs |
| **Return contract** | `{"summary": dict, "workbook", "problem_cards": DataFrame, "report_path": str\|None}` |
| **Notes** | **Не** использует `rules.xlsx` exclude_time (только wallet paths) — dual model |

---

### RT-004 — Payout analysis (`payout.run`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Анализ payout + cd (направления); Excel + Telegram |
| **Producer** | `main.process_file` |
| **Consumer** | `main` |
| **Signature** | `run(payout_file, card_files, *args, **kwargs) -> dict` |
| **Source of truth** | `config/payout_config.yaml` (`PayoutsErrors`, `IgnoreErrors`) |
| **Failure modes** | No card_files → `{}`; missing columns → `{}` or warning; move to processed in-module |
| **Return contract** | `{"workbook", "report_path", "summary": {"Карты на перевод", "Карты на проверку"}}` |

---

### RT-005 — Rules sync (`get_rules_snapshot`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Dropbox → local cache; version hash; TTL; fail-safe |
| **Producer** | `core/rules_provider.py` |
| **Consumer** | `access_rules`, `config_manager`, wallet analyzers |
| **Runtime path** | `RULES_XLSX_PATH` (file or folder/rules.xlsx) → `/tmp/rules_cache/rules.xlsx` |
| **Source of truth** | Dropbox `rules.xlsx` |
| **Failure modes** | Download fail + no cache → `RuntimeError`; stale cache served if prior snap exists |
| **Notes** | `_wait_file_stable` 3×0.25s, timeout 8s; TTL `RULES_SYNC_MIN_INTERVAL_SEC` default 60 |

---

### RT-006 — Access snapshot (`AccessRules.get_snapshot`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Parse `access` + `commands` sheets into in-memory maps |
| **Producer** | `core/access_rules.py` |
| **Consumer** | `access_guard`, `tg_commands`, `scheduler` startup |
| **Failure modes** | Missing columns / invalid values → `ValueError` (fail-closed for guard) |
| **Notes** | Cache by `stat_key` of rules file; `invalidate()` clears |

---

### RT-007 — Command guard (`check_access`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Authorize Telegram command before execution |
| **Producer** | `core/access_guard.py` |
| **Consumer** | `integrations/tg_commands` handlers |
| **Failure modes** | `rules_not_ready`, `rules_invalid`, `unknown_command`, `insufficient_level`, etc. → deny |
| **Notes** | Command normalized: strip `/`, lower case |

---

### RT-008 — Exclude time gate (`get_exclude_time_df`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Load + validate `exclude_time` sheet; cached by mtime/size |
| **Producer** | `core/config_manager.py` |
| **Consumer** | `wallet_analyzer`, `raccoon_wallet_analyzer` |
| **Failure modes** | Fatal validation → `RuntimeError` + throttled Telegram notify; warnings → notify, still returns df |
| **Notes** | Id format `EXC-#####` enforced in code |

---

### RT-009 — Wallet limits gate (`get_wallet_limits_df`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Load + validate `wallet_limits` sheet |
| **Producer** | `core/config_manager.py` |
| **Consumer** | wallet analyzers |
| **Failure modes** | Fatal → `RuntimeError` |
| **Notes** | `scope` ∈ {partner, group}; `limit_type` validated in code |

---

### RT-010 — Telegram outbound (`send_message_sync` / `send_file_sync`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Async queue-backed delivery from sync code |
| **Producer** | `integrations/telegram_bot.py` |
| **Consumer** | All analyzers, downloaders, monitors |
| **Contract** | **`chat_id` required** — `ValueError` if missing |
| **Failure modes** | Import of `telegram_bot` fails without `TELEGRAM_BOT_TOKEN`; queue worker logs errors |
| **Notes** | HTTPX: connect 20s, read 40s; `send_photo_sync` uses raw `requests` |

---

### RT-011 — Manual TG job runner (`_run_job`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Single-flight execution of sync job in thread pool |
| **Producer** | `integrations/tg_commands.py` |
| **Consumer** | `/run_*` commands |
| **Failure modes** | Lock held → user message; exception → traceback tail (3500 chars) to chat |
| **Notes** | Does not increment `scheduler._active_jobs` |

---

### RT-012 — Antares wallet cycle

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Playwright download payin/payout → `analyze_wallets` |
| **Producer** | `integrations/downloader_wallets.run_wallet_cycle` |
| **Consumer** | `analyzers/wallet_analyzer.analyze_wallets` |
| **Trigger** | TG `/run_wallet`, CLI `__main__` |
| **Failure modes** | Missing credentials → `RuntimeError`; errors in `__main__` → Telegram |
| **Notes** | `ANALYZER_KEY = "wallet"` |

---

### RT-013 — Raccoon wallet cycle

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Playwright payin download → `analyze_raccoon_wallets` |
| **Producer** | `integrations/raccoon_wallet_downloader.run_raccoon_wallet_cycle` |
| **Consumer** | `analyzers/raccoon_wallet_analyzer` |
| **Trigger** | Scheduler hourly :00 MSK; TG `/run_raccoon` |
| **Notes** | Payout download **commented out**; `payout_path=None` always |

---

### RT-014 — Raccoon hourly job

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Download payin → hourly report with dedup |
| **Producer** | `scheduler` thread `run_hourly_raccoon` |
| **Consumer** | `run_hourly_raccoon_cycle` + `run_hourly_report` |
| **Failure modes** | Missing `/tmp/hourly_raccoon/payin.xlsx` → `FileNotFoundError`; empty interval / same hash → skip send |

---

### RT-015 — Raccoon daily conversion

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Yesterday summary from cached payin file |
| **Producer** | `scheduler.run_daily_conversion_loop` |
| **Consumer** | `run_daily_conversion_report("/tmp/hourly_raccoon/payin.xlsx")` |
| **Notes** | Hardcoded input path; depends on hourly downloader populating file |

---

### RT-016 — Antares full downloader

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED (code); **UNKNOWN** (prod schedule) |
| **Purpose** | Antares export → Dropbox → `process_file` conversion/payout |
| **Producer** | `integrations/downloader.run_download` |
| **Failure modes** | Browser error → raise after Telegram; lock busy → skip analyze |
| **Notes** | Sets `os.environ["TZ"]="Europe/Moscow"` |

---

### RT-017 — Bakai rate monitor

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Purpose** | Scrape buy RUB rate; alert on change |
| **Producer** | `run_rate_monitor_safe` (3 retries 5/15/30s + jitter) |
| **Trigger** | TG `/run_rate` only |
| **Failure modes** | Outside 08:00–23:55 MSK → no-op; final fail → Telegram + optional screenshot |

---

### RT-D01 — Rules download alternate (`download_rules_xlsx`)

| Поле | Значение |
|------|----------|
| **Status** | DORMANT |
| **Purpose** | Alternate rules path via `DROPBOX_RULES_PATH` / `RULES_LOCAL_PATH` |
| **Evidence** | Defined in `dropbox_watcher.py`; **no callers** in repo |
| **Notes** | STALE_RISK vs `rules_provider` canonical path |

---

### RT-D02 — Event append model

| Поле | Значение |
|------|----------|
| **Status** | DORMANT |
| **Purpose** | Observability events (`ARCHITECTURE.md`, `CONTRACT_RULES` §5) |
| **Evidence** | `core/events.py` is **empty** |
| **Notes** | DOCS_ONLY behavior described; not implemented |

---

### RT-D03 — Antares hourly pipeline

| Поле | Значение |
|------|----------|
| **Status** | DORMANT |
| **Modules** | `integrations/hourly_downloader`, `analyzers/hourly_report` |
| **Evidence** | No imports from `scheduler` / `tg_commands` |
| **Notes** | Requires `TELEGRAM_CHAT_ID_HOURLY` at import |

---

### RT-DOCS-01 — Telegram rules editor

| Поле | Значение |
|------|----------|
| **Status** | DOCS_ONLY |
| **Purpose** | Bot as sole rules editor (`CONTRACT_BOT.md`) |
| **Evidence** | `tg_commands` has `/reload_rules` only (re-read), no write/upload handlers |

---

## 2. File Contracts

### FC-001 — `rules.xlsx`

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED (read path); meta version enforcement — **UNKNOWN** in scanned code |
| **Expected location** | Dropbox: `RULES_XLSX_PATH` or `{folder}/rules.xlsx`; cache: `/tmp/rules_cache/rules.xlsx` |
| **Format** | Excel (openpyxl) |
| **Sheets (runtime-used)** | `access`, `commands` — **CONFIRMED** (`access_rules`); `exclude_time`, `wallet_limits` — **CONFIRMED** (`config_manager`); `thresholds_partner` — **CONFIRMED** (wallet analyzers read via pandas) |
| **Sheets (documented)** | `meta` — **DOCS_ONLY** in `CONTRACT_RULES.md`; runtime validation of `meta.version` not found in grep |
| **Reader** | `rules_provider`, `access_rules`, `config_manager`, wallet analyzers |
| **Writer** | **UNKNOWN** in codebase (manual / external) |
| **Validation** | Column presence + value rules in `access_rules`, `validate_exclude_time`, `validate_wallet_limits`; thresholds read with soft-fail warnings in analyzers |
| **Failure modes** | Guard deny; wallet analyzer stop on fatal exclude_time |

**`access` sheet (CONFIRMED columns):** `chat_id`, `user_id`, `level`; optional `enabled` (default 1).

**`commands` sheet (CONFIRMED):** `command`, `required_level`, `allow_private`, `allow_groups`; optional `enabled`.

**`exclude_time` (CONFIRMED):** `id`, `enabled`, `analyzers`, `partner`, `start_dt`, `end_dt`, `reason`, `created_by`, `created_at`.

**`wallet_limits` (CONFIRMED):** `id`, `enabled`, `analyzers`, `scope`, `scope_value`, `limit_type`, `limit_value`, `reason`.

**`thresholds_partner` (CONFIRMED per CONTRACT_RULES + analyzer reads):** `id`, `enabled`, `analyzer`, `partner`, `metric`, `threshold_min`/`threshold_max`, `reason`, …

---

### FC-002 — Dropbox input/processing files

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Expected location** | `{DROPBOX_INPUT_PATH}/{filename}` → `/tmp/{filename}` → `{DROPBOX_PROCESSED_PATH}/{name}_(DD.MM.YYYY).xlsx` |
| **Naming** | `conversion_*`, `payout_*`, `card_*`, `cd_*` (from downloaders) |
| **Reader** | `main`, analyzers |
| **Writer** | `downloader._upload_local_to_dropbox`, `move_file` |
| **Format** | `.xlsx` |
| **Failure modes** | `download_file` → False; `move_file` → False |

---

### FC-003 — `special_cards.xlsx`

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Location** | `{DROPBOX_SPECIAL_PATH}/special_cards.xlsx` (default `/Ostin/platform/special`) |
| **Reader** | `conversion.run` |
| **Writer** | **UNKNOWN** in repo |
| **Purpose** | Per (card, partner) `start_date` rules |

---

### FC-004 — Raccoon hourly payin artifact

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Path** | `/tmp/hourly_raccoon/payin.xlsx` |
| **Writer** | `raccoon_hourly_downloader._download_payin` |
| **Readers** | `raccoon_hourly_report`, `raccoon_daily_conversion` |
| **Required columns (CONFIRMED usage)** | `Партнер`, `Дата/Время создания`, `Статус`, `Сумма`; hourly also uses `Метод пополнения` in daily report |
| **Failure modes** | Missing file → `FileNotFoundError` / daily skip |

---

### FC-005 — Hourly report state

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Path** | `/tmp/hourly_raccoon/last_sent.json` |
| **Schema** | JSON with `hash` (sha256 of fingerprint payload), `day`, `payin` block stats |
| **Writer/Reader** | `raccoon_hourly_report` |
| **Invalidation** | New fingerprint hash |

---

### FC-006 — Bakai rate state

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Path** | `/tmp/bakai_last_buy_rate.txt` |
| **Format** | Single float as text |
| **Writer/Reader** | `bakai_monitor_playwright` |

---

### FC-007 — Playwright auth state files

| Path | Owner module | Статус |
|------|--------------|--------|
| `/tmp/auth_state.json` | `downloader.py` (Antares main) | CONFIRMED |
| `/tmp/auth_state_wallets.json` | `downloader_wallets.py` | CONFIRMED |
| `/tmp/auth_state_raccoon.json` | `raccoon_wallet_downloader.py` | CONFIRMED |
| `/tmp/hourly_raccoon_auth.json` | `raccoon_hourly_downloader.py` | CONFIRMED |
| `/tmp/hourly_auth.json` | `hourly_downloader.py` (dormant) | DORMANT |

---

### FC-008 — Pipeline lock file

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED |
| **Path** | `/tmp/dropbox_pipeline.lock` |
| **Content** | PID string |
| **Stale after** | 600s (default `acquire_lock(timeout)`) |
| **Users** | `main.py`, `downloader.run_download` analyze phase |

---

### FC-009 — Analyzer output reports (Excel)

| Artifact | Producer | Delivery | Статус |
|----------|----------|----------|--------|
| `report_{conv}_({date}).xlsx` | `conversion.run` | `send_file_sync` → ANALIZ | CONFIRMED |
| Payout workbook | `payout.run` | `send_file_sync` + Dropbox move | CONFIRMED |
| Wallet reports | wallet analyzers | Telegram | CONFIRMED (paths in-module) |

---

### FC-010 — Repo YAML configs

| File | Role | Статус |
|------|------|--------|
| `config/analysis_map.yaml` | Analyzer routing | CONFIRMED |
| `config/conversion_config.yaml` | columns, pools, partners, valid_statuses | CONFIRMED |
| `config/payout_config.yaml` | error thresholds | CONFIRMED |
| `config/wallet_config.yaml` | partners, groups, download_periods | CONFIRMED |
| `config/raccoon_wallet_config.yaml` | Raccoon wallet periods | CONFIRMED |
| `config/raccoon_hourly_report.yaml` | payin layout, partner keys | CONFIRMED |
| `config/hourly_report.yaml` | Antares hourly | DORMANT |

---

### FC-011 — `automation/` tree

| Поле | Значение |
|------|----------|
| **Status** | STALE_RISK |
| **Evidence** | Workspace contains `automation/__pycache__/*.pyc` (`engine`, `worker`, `tg_receiver`, …) but **no `.py` sources**; `automation/tests/` not present on disk |
| **Notes** | Cannot confirm test contracts from code in current workspace |

---

## 3. Env Contracts

> Значения не документируются. **Required** = fail-fast or hard failure on use path.

### 3.1 Telegram

| Name | Status | Consumer | Required | Default | Runtime effect | Failure if missing |
|------|--------|----------|----------|---------|----------------|------------------|
| `TG_BOT_TOKEN` | CONFIRMED | `scheduler.py` | **Yes** (prod entry) | `""` | Builds `python-telegram-bot` Application for polling | `RuntimeError` at startup |
| `TELEGRAM_BOT_TOKEN` | CONFIRMED | `integrations/telegram_bot.py` | **Yes** on import of module | — | Bot client for `send_*_sync` queue | `ValueError` at import |
| `TELEGRAM_CHAT_ID_ANALIZ` | CONFIRMED | `conversion`, `payout`, `downloader` | Yes for those modules | — | Target channel for Antares analytics | `RuntimeError` at import |
| `TELEGRAM_CHAT_ID_WALLET` | CONFIRMED | `wallet_analyzer`, `downloader_wallets` | Yes in analyzer; wallet DL falls back | — | Wallet reports | `RuntimeError` / fallback to `TELEGRAM_CHAT_ID` |
| `TELEGRAM_CHAT_ID` | CONFIRMED | wallet downloaders fallback | Optional | — | Fallback chat | Uses if wallet-specific unset |
| `TELEGRAM_CHAT_ID_RACCOON_WALLET` | CONFIRMED | raccoon wallet + daily conversion | Yes in analyzers | — | Raccoon wallet/daily messages | `RuntimeError` in analyzers |
| `TELEGRAM_CHAT_ID_HOURLY_RACCOON` | CONFIRMED | `raccoon_hourly_report` | Yes | — | Hourly traffic report | `RuntimeError` at import |
| `TELEGRAM_CHAT_ID_HOURLY` | DORMANT | `hourly_report.py` | Yes if module imported | — | Antares hourly | Import fail if dormant path enabled |
| `CURRENT_RATE_BAKAI_CHAT_ID` | CONFIRMED | `bakai_monitor` | Yes | — | Status messages | `RuntimeError` at import |
| `NEW_RATE_BAKAI_CHAT_ID` | CONFIRMED | `bakai_monitor` | Yes | — | Alert on rate change | `RuntimeError` at import |

---

### 3.2 Dropbox

| Name | Status | Consumer | Required | Default | Runtime effect | Failure if missing |
|------|--------|----------|----------|---------|----------------|------------------|
| `DROPBOX_ACCESS_TOKEN` | CONFIRMED | `dropbox_watcher` | One of token **or** refresh trio | — | Dropbox SDK client | `ValueError` at import |
| `DROPBOX_REFRESH_TOKEN` | CONFIRMED | `dropbox_watcher` | With APP_KEY/SECRET if no access token | — | OAuth refresh client | same |
| `DROPBOX_APP_KEY` | CONFIRMED | `dropbox_watcher` | With refresh token | — | OAuth | same |
| `DROPBOX_APP_SECRET` | CONFIRMED | `dropbox_watcher` | With refresh token | — | OAuth | same |
| `DROPBOX_INPUT_PATH` | CONFIRMED | `main`, `downloader`, `payout` | Yes on those paths | — | Input folder path | `RuntimeError` / failed operations |
| `DROPBOX_PROCESSED_PATH` | CONFIRMED | `main`, `payout` | Yes when moving | — | Archive folder | move fails / errors |
| `DROPBOX_SPECIAL_PATH` | CONFIRMED | `conversion` | Optional | `/Ostin/platform/special` | special_cards location | download special fails |
| `RULES_XLSX_PATH` | CONFIRMED | `rules_provider`, `access`, analyzers | Yes for rules/access | — | Dropbox path to rules | `RuntimeError` / guard deny |
| `DROPBOX_RULES_PATH` | DORMANT | `download_rules_xlsx` only | Optional | `/rules.xlsx` | Unused alternate | — |
| `RULES_LOCAL_PATH` | DORMANT | `download_rules_xlsx` only | Optional | `/tmp/rules/rules.xlsx` | Unused alternate | — |

---

### 3.3 Antares (Playwright)

| Name | Status | Consumer | Required | Default | Failure if missing |
|------|--------|----------|----------|---------|------------------|
| `ANTARES_LOGIN` | CONFIRMED | `downloader`, `downloader_wallets`, `hourly_downloader` | Yes on run | — | `RuntimeError` in cycle |
| `ANTARES_PASSWORD` | CONFIRMED | same | Yes on run | — | same |
| `PLAYWRIGHT_HEADLESS` | CONFIRMED | all Playwright modules | Optional | `"1"` / true-ish | Headless browser |

---

### 3.4 Raccoon (Playwright)

| Name | Status | Consumer | Required | Default | Failure if missing |
|------|--------|----------|----------|---------|------------------|
| `RACCOON_LOGIN` | CONFIRMED | raccoon downloaders | Yes on run | — | `RuntimeError` |
| `RACCOON_PASSWORD` | CONFIRMED | same | Yes on run | — | same |

---

### 3.5 Rules cache / sync

| Name | Status | Consumer | Required | Default | Runtime effect |
|------|--------|----------|----------|---------|----------------|
| `RULES_SYNC_MIN_INTERVAL_SEC` | CONFIRMED | `rules_provider` | Optional | `60` | Min seconds between Dropbox syncs |

---

### 3.6 Scheduler / timezone / restart

| Name | Status | Consumer | Required | Default | Runtime effect |
|------|--------|----------|----------|---------|----------------|
| `RACCOON_HOURLY_EVERY_MIN` | CONFIRMED | `scheduler` | Optional | `5` | Interval minutes for hourly job |
| `RESTART_GRACE_MIN` | CONFIRMED | `scheduler.restart_worker` | Optional | `10` | Minutes wait for active jobs before `os._exit(1)` |
| `TZ` (set in code) | CONFIRMED | `downloader.py` sets `Europe/Moscow` | — | — | Process TZ for that module |
| MSK `ZoneInfo` | CONFIRMED | scheduler, reports, downloaders | — | hardcoded | All schedule semantics |

**Not env — hardcoded:** `RESTART_TIMES` in `scheduler.py`: (10:30), (13:30), (16:30), (19:30), (22:30), (01:30) MSK.

---

### 3.7 Misc

| Name | Status | Consumer | Required | Default |
|------|--------|----------|----------|---------|
| `TMP` | CONFIRMED | `payout` temp report path | Optional | `/tmp` |

---

### 3.8 Env conflict summary (critical)

| Conflict | Status | Impact |
|----------|--------|--------|
| **`TG_BOT_TOKEN` vs `TELEGRAM_BOT_TOKEN`** | STALE_RISK | Polling may use token A while `send_message_sync` uses token B if only one var set — **UNKNOWN** cross-bot behavior |
| **`RULES_XLSX_PATH` vs `DROPBOX_RULES_PATH`** | STALE_RISK | Two rules path conventions; only former active |

---

## 4. Scheduler Contracts

| Job ID | Trigger | Interval / cron | Timezone | Lock | Error handling | Restart interaction | Output / side effects | Status |
|--------|---------|-----------------|----------|------|----------------|---------------------|-------------------------|--------|
| **SCH-01** Telegram polling | Process start | Continuous | — | `_running_lock` for manual jobs only | Handler exceptions → PTB behavior | `os._exit` kills all | Incoming commands | CONFIRMED |
| **SCH-02** `run_raccoon_wallet` | `run_hourly_at_minute(..., 0)` | Every hour at **:00:05** MSK (second=5) | MSK | `job_start`/`job_end` | Log exception; loop continues | Waits in grace window | PayIn xlsx → wallet analysis → TG | CONFIRMED |
| **SCH-03** `run_hourly_raccoon` | `run_every_minutes` | Every `RACCOON_HOURLY_EVERY_MIN` (default **5**) | MSK | same | same | same | Updates `payin.xlsx`, maybe TG hourly report | CONFIRMED |
| **SCH-04** `daily_conversion` | Loop poll | When `hour==0` and `minute<=2`, once per date | MSK | **Not** tied to `job_start` | Uncaught → thread dies? (no outer try) — **UNKNOWN** | Not counted in `_active_jobs` | TG daily text | CONFIRMED |
| **SCH-05** `restart_worker` | Wall-clock | Next of `RESTART_TIMES` MSK | MSK | N/A | Always exits after grace | `os._exit(1)` | Full container restart (Railway) | CONFIRMED |

**Overlap policy:** Scheduled jobs can run concurrently (separate threads); only manual TG jobs are single-flight. **STALE_RISK:** daily loop not in `_active_jobs` — restart may interrupt midnight report.

**No cron language:** intervals implemented via `while True` + sleep in daemon threads.

---

## 5. API / Integration Contracts

### INT-01 — Telegram

| | |
|---|---|
| **Status** | CONFIRMED |
| **Direction** | Outbound (reports); Inbound (commands via polling) |
| **Auth** | `TG_BOT_TOKEN` (polling), `TELEGRAM_BOT_TOKEN` (send API) |
| **Assumptions** | Long polling `close_loop=False`; commands registered as `CommandHandler` |
| **Timeouts** | HTTPX connect 20s, read 40s; `send_photo_sync` requests timeout 30s |
| **Retry** | Queue worker logs error; no automatic resend — **CONFIRMED** |
| **Failure modes** | Missing token → startup/import failure; send errors logged |
| **Consumers** | All pipelines delivering to operators |

---

### INT-02 — Dropbox

| | |
|---|---|
| **Status** | CONFIRMED |
| **Direction** | Bi-directional file sync |
| **Auth** | `DROPBOX_ACCESS_TOKEN` **or** refresh token + app key/secret |
| **API surface** | `files_download`, `files_upload` (overwrite), `files_move_v2` (autorename), `files_list_folder` |
| **Retry** | None in wrapper — returns False/[] |
| **Failure modes** | False return → callers handle; import-time auth missing → ValueError |
| **Consumers** | P1, P2, rules_provider, conversion special_cards |

---

### INT-03 — Antares (`antares.plus`)

| | |
|---|---|
| **Status** | CONFIRMED |
| **Direction** | Inbound (browser export downloads) |
| **Auth** | `ANTARES_LOGIN` / `ANTARES_PASSWORD`; session in `storage_state` JSON |
| **Assumptions** | UI selectors: login form, Export buttons, calendar `[data-date]`, routes `/payin`, `/wallet`, `/vyplaty` |
| **Timeouts** | `expect_download` 90s–360s depending on module; page goto 5s–20s |
| **Failure modes** | Screenshots to `/app/logs` or `/tmp` on some paths; Telegram notify |
| **Consumers** | P2, P3, dormant hourly |

---

### INT-04 — Raccoon (`raccoon.it.com`)

| | |
|---|---|
| **Status** | CONFIRMED |
| **Direction** | Inbound |
| **Auth** | `RACCOON_LOGIN` / `RACCOON_PASSWORD`; email field login |
| **Assumptions** | Partner portal `#/payin`, `#/login`; same calendar patterns as Antares |
| **Timeouts** | download 90s–180s |
| **Consumers** | P4, P5, P6 |

---

### INT-05 — Bakai (`bakai.kg`)

| | |
|---|---|
| **Status** | CONFIRMED |
| **Direction** | Inbound (scrape) |
| **Auth** | None (public page) |
| **Assumptions** | `select[value=transfer]`; RUB row `tr:has(img[src*='rub'])`; buy rate in 2nd cell |
| **Timeouts** | `page.goto` 60000ms |
| **Retry** | `run_rate_monitor_safe`: 3 attempts, delays 5/15/30s + random 0–3s |
| **Failure modes** | `RateMonitorError` + screenshot path; alert chat on change uses `NEW_RATE_BAKAI_CHAT_ID` |
| **Consumers** | P7 |

---

## 6. DTO / Data Contracts

### DTO-01 — `RulesSnapshot` (frozen dataclass)

| | |
|---|---|
| **Status** | CONFIRMED |
| **Producer** | `get_rules_snapshot()` |
| **Consumers** | `AccessRules`, indirect via path in analyzers |
| **Fields** | `local_path: str`, `rules_version: str` (sha256 hex), `stat_key: (mtime, size)`, `loaded_at_ts: float`, `source: str` |
| **Serialization** | In-process only |
| **Failure** | See RT-005 |

---

### DTO-02 — `AccessRules.Snapshot`

| | |
|---|---|
| **Status** | CONFIRMED |
| **Fields** | `access_map: Dict[(chat_key, user_id), level]`, `commands_map: Dict[str, CommandRule]`, `stat_key`, `loaded_at_ts`, `source` |
| **chat_key** | `"private"` or `int` chat_id |

---

### DTO-03 — `CommandRule` (frozen)

| | |
|---|---|
| **Status** | CONFIRMED |
| **Fields** | `required_level: int`, `allow_private: bool`, `allow_groups: bool`, `enabled: bool` |

---

### DTO-04 — `AccessContext` (frozen)

| | |
|---|---|
| **Status** | CONFIRMED |
| **Fields** | `chat_type: str`, `chat_id: int`, `user_id: int` |

---

### DTO-05 — `ValidationResult`

| | |
|---|---|
| **Status** | CONFIRMED |
| **Fields** | `ok: bool`, `errors: List[str]`, `warnings: List[str]`, `df_norm: pd.DataFrame` |

---

### DTO-06 — `conversion.run` return dict

| | |
|---|---|
| **Status** | CONFIRMED |
| **Keys** | `summary` (dict with Russian keys), `workbook`, `problem_cards` (DataFrame), `report_path` |
| **summary keys** | `Карт в работе по партнёрам`, `Карт в работе по пулам`, `Max ошибки`, `Карты на отключение` |

---

### DTO-07 — `payout.run` return dict

| | |
|---|---|
| **Status** | CONFIRMED |
| **Keys** | `workbook`, `report_path`, `summary` with `Карты на перевод`, `Карты на проверку` (counts) |

---

### DTO-08 — Hourly fingerprint state

| | |
|---|---|
| **Status** | CONFIRMED |
| **Producer** | `_calc_fingerprint` in `raccoon_hourly_report` |
| **Shape** | `{ "day": str, "payin": {rows, total, max_dt}, "hash": sha256 }` |
| **Persistence** | `last_sent.json` |

---

### DTO-09 — `RateMonitorError`

| | |
|---|---|
| **Status** | CONFIRMED |
| **Fields** | `message`, optional `screenshot_path` |

---

### DTO-10 — Analyzer keys (string constants)

| Key | Module | Статус |
|-----|--------|--------|
| `"wallet"` | `wallet_analyzer` | CONFIRMED |
| `"raccoon_wallet"` | `raccoon_wallet_analyzer` | CONFIRMED |

Used in `exclude_time.analyzers` CSV matching.

---

## 7. Cache / State Contracts

| ID | Owner | Location | TTL / invalidation | Consumers | Stale data risk | Status |
|----|-------|----------|-------------------|-----------|-----------------|--------|
| **C-01** | `rules_provider` | `/tmp/rules_cache/rules.xlsx` | TTL `RULES_SYNC_MIN_INTERVAL_SEC`; invalidate on mtime/size change; fail-safe old snap | all rules readers | Medium — old rules served on Dropbox outage | CONFIRMED |
| **C-02** | `AccessRules` | in-process `_snap` | `invalidate()` or rules file stat change | guard, tg | Low per process | CONFIRMED |
| **C-03** | `config_manager` | `_EXCLUDE_TIME_CACHE` | per-path stat_key | wallet analyzers | Low | CONFIRMED |
| **C-04** | `raccoon_hourly_report` | `last_sent.json` | new content hash | hourly pipeline | Skip send if stale hash matches — by design | CONFIRMED |
| **C-05** | `bakai_monitor` | `/tmp/bakai_last_buy_rate.txt` | on rate change | rate monitor | False “no change” if file lost | CONFIRMED |
| **C-06** | Playwright | `auth_state*.json` | manual delete | downloaders | Expired session → re-login | CONFIRMED |
| **C-07** | `main` | `last_card_path` global | until new card file in **same process** | conversion/payout pairing | **High** across processes/invocations | CONFIRMED |
| **C-08** | Notify throttle | `_NOTIFY_STATES` in config_manager | 60 min cooldown per hash | exclude_time warnings | Repeated warnings suppressed | CONFIRMED |

---

## 8. Cross-contract inconsistencies

### X-01 — `TG_BOT_TOKEN` vs `TELEGRAM_BOT_TOKEN` (architecture U1)

| | |
|---|---|
| **Evidence** | `scheduler.py:138` uses `TG_BOT_TOKEN`; `telegram_bot.py:15` uses `TELEGRAM_BOT_TOKEN`; both required on full stack |
| **Runtime impact** | Polling identity may differ from outbound Bot API identity if env misconfigured |
| **Risk level** | **High** |
| **Next investigation** | Compare Railway env vars; send test message via analyzer while issuing `/status` |
| **Action** | Document only — do not fix in Stage A2 |

---

### X-02 — `PROJECT_REFERENCE` vs scheduler background jobs (architecture U3)

| | |
|---|---|
| **Evidence** | `PROJECT_REFERENCE.md` § scheduler: «не запускает job'ы автоматически»; `scheduler.py` starts 3 daemon threads + restart worker |
| **Runtime impact** | Operators/docs may underestimate background Raccoon load and restart behavior |
| **Risk level** | **Medium** (documentation) |
| **Next investigation** | Align `PROJECT_REFERENCE` in separate docs task |

---

### X-03 — `CONTRACT_BOT.md` vs `tg_commands` (architecture U4)

| | |
|---|---|
| **Evidence** | `CONTRACT_BOT.md` describes rule edit + audit events; handlers only `reload_rules` (re-read) |
| **Runtime impact** | Rules changes expected via bot may not exist; manual Dropbox edit only |
| **Risk level** | **Medium** (process) |
| **Next investigation** | Confirm operational SOP for rules edits |

---

### X-04 — `downloader.py` prod schedule (architecture U2)

| | |
|---|---|
| **Evidence** | Not imported by `scheduler`; `railway.toml` only starts `scheduler.py` |
| **Runtime impact** | Antares conversion/payout auto-upload may be off unless separate Railway cron/service |
| **Risk level** | **High** (business continuity) |
| **Next investigation** | Railway dashboard: additional services/cron for `integrations/downloader.py` |

---

### X-05 — Antares hourly dormant (architecture U7)

| | |
|---|---|
| **Evidence** | No imports of `hourly_downloader` / `hourly_report` |
| **Runtime impact** | `TELEGRAM_CHAT_ID_HOURLY` unused unless manual run |
| **Risk level** | **Low** if feature retired; **Medium** if still expected |
| **Next investigation** | Operator interview / prod logs |

---

### X-06 — Dual rules download paths (architecture U5)

| | |
|---|---|
| **Evidence** | `rules_provider` → `/tmp/rules_cache/` + `RULES_XLSX_PATH`; `download_rules_xlsx` → `RULES_LOCAL_PATH` + `DROPBOX_RULES_PATH` (uncalled) |
| **Runtime impact** | Low today (dead code); confusion if someone calls dormant API |
| **Risk level** | **Low** |
| **Next investigation** | Remove or wire in future refactor (out of scope) |

---

### X-07 — `rules.xlsx` `meta.version` enforcement (architecture U9)

| | |
|---|---|
| **Evidence** | `CONTRACT_RULES.md` requires version match; no `read_excel(..., sheet_name="meta")` in scanned Python |
| **Runtime impact** | Wrong rules version may still run |
| **Risk level** | **Medium** |
| **Next investigation** | Grep/deploy branch for `validate_rules`; tasks in `project_memory/active_tasks` |

---

### X-08 — Daily conversion not in restart job counter

| | |
|---|---|
| **Evidence** | `run_daily_conversion_loop` does not call `job_start`/`job_end` |
| **Runtime impact** | Container restart during 00:00 window may kill daily report mid-flight |
| **Risk level** | **Low–Medium** |
| **Next investigation** | Logs around restart times vs daily send |

---

### X-09 — `automation/` bytecode without sources

| | |
|---|---|
| **Evidence** | `automation/__pycache__` lists `engine`, `worker`, `tg_receiver`; no `.py` in workspace |
| **Runtime impact** | **UNKNOWN** — not part of `scheduler` entry |
| **Risk level** | **Low** (if not deployed) |
| **Next investigation** | Confirm not in `railway.toml` start path; remove stale pyc from repo separately |

---

### X-10 — `reporters/` package

| | |
|---|---|
| **Evidence** | Path does not exist in repository |
| **Runtime impact** | None |
| **Risk level** | N/A |
| **Status** | N/A (not applicable) |

---

## Critical contracts (quick reference)

| Tier | IDs | Why |
|------|-----|-----|
| **P0** | RT-005, RT-006, RT-007, FC-001, `RULES_XLSX_PATH`, Dropbox auth | Control plane + access; system unusable without |
| **P0** | `TG_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN`, RT-010 | All delivery and polling |
| **P0** | SCH-02, SCH-03, FC-004, FC-005 | Active Raccoon prod loops |
| **P1** | RT-001, RT-003, RT-004, FC-002 | Antares analytics path |
| **P1** | X-01, X-04 | Env and schedule unknowns with high ops impact |

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | Stage A2: создан из `architecture_map.md` + codebase scan |
