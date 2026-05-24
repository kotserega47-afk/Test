# Current State — `analizis`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Снимок на дату** | 2026-05-24 |
| **Среда** | prod (Railway) + local dev |
| **Источники** | `architecture_map.md`, `contracts.md`, codebase, `CONTRACT_RULES.md`, `CONTRACT_BOT.md`, `ARCHITECTURE.md`, `PROJECT_REFERENCE.md` |
| **Метод** | Stage A3 — state extraction (без изменений runtime) |

---

## 1. Executive Summary

**Что проект делает сейчас (CONFIRMED):**  
Платформа автоматизирует операционную аналитику платежей: выгрузка Excel из кабинетов **Raccoon** (в production по расписанию) и **Antares** (по ручным командам / отдельным запускам), применение правил из **`rules.xlsx`** (Dropbox), отчёты и алерты в **Telegram**. Операторы управляют запусками через Telegram-команды с ACL из `rules.xlsx` (листы `access`, `commands`).

**Как запускается в production (CONFIRMED):**  
Railway service `file-analyzer` → `/opt/venv/bin/python scheduler.py` (`railway.toml`). Один long-running процесс: polling бота + фоновые daemon-потоки Raccoon + плановый рестарт контейнера.

**Активные подсистемы в prod entry:**

| ID | Подсистема | Статус |
|----|------------|--------|
| S1 | `scheduler.py` (entry + restart worker) | CONFIRMED active |
| S2 | Telegram polling + commands | CONFIRMED active |
| S3 | Raccoon wallet cycle (hourly :00 MSK) | CONFIRMED active |
| S4 | Raccoon hourly download + report (every N min) | CONFIRMED active |
| S5 | Raccoon daily conversion (00:00–00:02 MSK) | CONFIRMED active |
| S6 | Rules/access chain (Dropbox → cache → guard) | CONFIRMED active |
| S7 | `telegram_bot` outbound queue | CONFIRMED active (import side-effect) |

**Dormant / questionable:**

| ID | Подсистема | Статус |
|----|------------|--------|
| S8 | Antares full `integrations/downloader.py` | CONFIRMED code; **UNKNOWN** prod schedule |
| S9 | Antares hourly (`hourly_downloader` + `hourly_report`) | DORMANT (no scheduler binding) |
| S10 | Dropbox dispatch `main.py` в prod loop | CONFIRMED code; **не** в scheduler — только CLI / downloader chain |
| S11 | Telegram rules editing (`CONTRACT_BOT.md`) | DOCS_ONLY |
| S12 | Event model (`core/events.py`) | DORMANT (empty file) |
| S13 | `automation/` package | STALE_RISK (pyc only, no `.py` in workspace) |
| S14 | `tests/` sources | STALE_RISK (pytest artifacts only, no `.py` on disk) |

**Top risks (сейчас):**

1. **U1** — два env для Telegram-токена (`TG_BOT_TOKEN` vs `TELEGRAM_BOT_TOKEN`).  
2. **U2** — неизвестно, запускается ли `downloader.py` в production (Antares auto-pipeline).  
3. **U3** — `PROJECT_REFERENCE` описывает scheduler без auto-jobs — расходится с кодом.  
4. **U4** — операционный контракт редактирования rules через бота не реализован в handlers.  
5. **U9** — enforcement `rules.xlsx` `meta.version` в runtime не подтверждён кодом.

---

## 2. Active Runtime Systems

### ARS-01 — Production scheduler (`scheduler.py`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active (prod entry) |
| **Entry point** | `railway.toml` → `python scheduler.py` → `main()` |
| **Trigger** | Container start |
| **Dependencies** | `TG_BOT_TOKEN`, Dropbox (rules sync), `RULES_XLSX_PATH`, Raccoon credentials, Playwright/Chromium, per-pipeline chat IDs |
| **Outputs** | Long-running process; spawns background threads; blocks on `app.run_polling()` |
| **Critical contracts** | SCH-01..05 (`contracts.md`); RT-006 startup `RULES.get_snapshot(force_sync=True)` |
| **Known failure modes** | Missing `TG_BOT_TOKEN` → immediate `RuntimeError`; rules sync fail at startup → depends on cache (fail-safe in `rules_provider` if prior snap exists) |

---

### ARS-02 — Telegram polling & commands

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active |
| **Entry point** | `scheduler.main()` registers `integrations/tg_commands.get_handlers()` |
| **Trigger** | Incoming Telegram messages |
| **Dependencies** | `TG_BOT_TOKEN` (polling), `TELEGRAM_BOT_TOKEN` (if analyzers/jobs send — via `telegram_bot` import), `rules.xlsx` access sheets |
| **Outputs** | Command replies; manual job execution |
| **Critical contracts** | RT-007, RT-011, INT-01; commands: `start`, `help`, `status`, `whoami`, `reload_rules`, `run_wallet`, `run_rate`, `run_raccoon`, `run_hourly_raccoon` |
| **Known failure modes** | `rules_not_ready` / `rules_invalid` → deny; concurrent manual job → «Уже выполняется»; job exception → traceback to chat (tail 3500 chars) |

---

### ARS-03 — Raccoon wallet cycle

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active |
| **Entry point** | Scheduler thread `run_hourly_at_minute(run_raccoon_wallet_cycle, 0)`; TG `/run_raccoon` |
| **Trigger** | Every hour at **:00:05** MSK (second=5); manual |
| **Dependencies** | `RACCOON_LOGIN`, `RACCOON_PASSWORD`, `config/raccoon_wallet_config.yaml`, `rules.xlsx`, `TELEGRAM_CHAT_ID_RACCOON_WALLET` |
| **Outputs** | PayIn xlsx under `/tmp/raccoon_wallet/`; wallet analysis messages to Telegram |
| **Critical contracts** | RT-013, FC-007, ANALYZER_KEY `raccoon_wallet` |
| **Known failure modes** | Playwright/login failure; payout download **disabled in code** (`payout_path=None`); rules fatal validation stops analyzer |

---

### ARS-04 — Raccoon hourly (download + report)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active |
| **Entry point** | Scheduler `run_every_minutes(_hourly_job, RACCOON_HOURLY_EVERY_MIN)`; TG `/run_hourly_raccoon` |
| **Trigger** | Default every **5** minutes (`RACCOON_HOURLY_EVERY_MIN`); manual |
| **Dependencies** | Raccoon credentials, `/tmp/hourly_raccoon/`, `config/raccoon_hourly_report.yaml`, `TELEGRAM_CHAT_ID_HOURLY_RACCOON` |
| **Outputs** | Overwrites `/tmp/hourly_raccoon/payin.xlsx`; optional Telegram hourly text; updates `last_sent.json` |
| **Critical contracts** | RT-014, FC-004, FC-005, DTO-08 fingerprint dedup |
| **Known failure modes** | Missing payin file → `FileNotFoundError` in report; empty interval / unchanged hash → skip send (by design) |

---

### ARS-05 — Raccoon daily conversion

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active |
| **Entry point** | `scheduler.run_daily_conversion_loop` |
| **Trigger** | MSK **00:00–00:02**, once per calendar date |
| **Dependencies** | `/tmp/hourly_raccoon/payin.xlsx` (from hourly downloader), `TELEGRAM_CHAT_ID_RACCOON_WALLET` |
| **Outputs** | Telegram text summary for **yesterday** |
| **Critical contracts** | RT-015 |
| **Known failure modes** | Stale/missing payin if hourly job failed; loop **not** in `_active_jobs` → may be cut by restart (X-08) |

---

### ARS-06 — Rules & access chain

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active |
| **Entry point** | Import `tg_commands` → `AccessRules`; any `get_rules_snapshot()` consumer |
| **Trigger** | Startup force sync; `/reload_rules`; TTL on reads; analyzer runs |
| **Dependencies** | Dropbox auth, `RULES_XLSX_PATH`, `RULES_SYNC_MIN_INTERVAL_SEC` (default 60) |
| **Outputs** | `/tmp/rules_cache/rules.xlsx`; in-memory snapshots |
| **Critical contracts** | RT-005, RT-006, RT-007, FC-001, DTO-01/02 |
| **Known failure modes** | Dropbox down + no cache → guard denies commands; `exclude_time` fatal → wallet jobs stop |

---

### ARS-07 — Telegram outbound transport (`telegram_bot.py`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active (loaded by analyzers/downloaders on import) |
| **Entry point** | Module import starts background asyncio loop |
| **Trigger** | Any `send_message_sync` / `send_file_sync` |
| **Dependencies** | `TELEGRAM_BOT_TOKEN` (**required at import**) |
| **Outputs** | Queued messages/files to Telegram API |
| **Critical contracts** | RT-010, INT-01 |
| **Known failure modes** | Missing token → import `ValueError`; worker errors logged, no retry |

---

### ARS-08 — Bakai rate monitor (on-demand)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active (manual only, not scheduled) |
| **Entry point** | TG `/run_rate` → `run_rate_monitor_safe` |
| **Trigger** | Operator command |
| **Dependencies** | `CURRENT_RATE_BAKAI_CHAT_ID`, `NEW_RATE_BAKAI_CHAT_ID`, Playwright |
| **Outputs** | Telegram rate messages; `/tmp/bakai_last_buy_rate.txt` |
| **Critical contracts** | RT-017, INT-05 |
| **Known failure modes** | Outside 08:00–23:55 MSK → no-op; 3 retries then alert |

---

### ARS-09 — Antares wallet (manual / CLI)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED active (not in scheduler loops) |
| **Entry point** | TG `/run_wallet`; `python integrations/downloader_wallets.py` |
| **Trigger** | Manual |
| **Dependencies** | `ANTARES_*`, `wallet_config.yaml`, `TELEGRAM_CHAT_ID_WALLET`, rules |
| **Outputs** | Wallet analysis → Telegram |
| **Critical contracts** | RT-012, ANALYZER_KEY `wallet` |

---

### ARS-10 — Dropbox dispatch (`main.process_file`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED code path; **not** started by `scheduler.py` |
| **Entry point** | `python main.py <file>`; called from `downloader.run_download()` |
| **Trigger** | CLI or Antares downloader after upload |
| **Dependencies** | `DROPBOX_INPUT_PATH`, `DROPBOX_PROCESSED_PATH`, `analysis_map.yaml`, card pairing |
| **Outputs** | Processed files in Dropbox; conversion/payout Telegram + Excel |
| **Critical contracts** | RT-001..004, FC-002, FC-003 |
| **Known failure modes** | `last_card_path` in-process only (STALE_RISK across processes); lock `/tmp/dropbox_pipeline.lock` |

---

## 3. Dormant / Unconfirmed Systems

### DOR-01 — Antares full downloader (`integrations/downloader.py`)

| Поле | Значение |
|------|----------|
| **Status** | CONFIRMED implementation; **UNKNOWN** production binding |
| **Why** | Not imported by `scheduler.py`; `railway.toml` only starts scheduler |
| **Evidence** | `grep` — no imports from scheduler/tg_commands; architecture U2 |
| **Risk** | **High** — Antares conversion/payout auto-pipeline may be off in prod |
| **Next investigation** | Railway dashboard: extra services, cron, or manual ops runbook |

---

### DOR-02 — Antares hourly pipeline

| Поле | Значение |
|------|----------|
| **Status** | DORMANT |
| **Why** | `hourly_downloader`, `hourly_report` — no imports from prod entry |
| **Evidence** | No references in `scheduler.py`, `tg_commands.py` |
| **Risk** | **Medium** if operators still expect `TELEGRAM_CHAT_ID_HOURLY` reports |
| **Next investigation** | Confirm feature retired vs missing wiring |

---

### DOR-03 — Telegram rules editing (`CONTRACT_BOT.md`)

| Поле | Значение |
|------|----------|
| **Status** | DOCS_ONLY |
| **Why** | Handlers only `/reload_rules` (re-read); no write/upload to Dropbox |
| **Evidence** | `tg_commands.py`; CONTRACT_BOT vs code |
| **Risk** | **Medium** — process/audit gap; rules changed manually in Dropbox |
| **Next investigation** | Operational SOP; align doc or implement handlers (future task) |

---

### DOR-04 — Event / observability model

| Поле | Значение |
|------|----------|
| **Status** | DORMANT |
| **Why** | `core/events.py` is empty; `ARCHITECTURE.md` describes append-only events |
| **Evidence** | File read — zero bytes |
| **Risk** | **Low** for runtime; **Medium** for audit/reproducibility promises in docs |
| **Next investigation** | Decide if events are out-of-band (logs only) |

---

### DOR-05 — Alternate rules paths (`download_rules_xlsx`)

| Поле | Значение |
|------|----------|
| **Status** | DORMANT |
| **Why** | Function defined in `dropbox_watcher.py`, never called |
| **Evidence** | `grep` — no callers; uses `DROPBOX_RULES_PATH` / `RULES_LOCAL_PATH` |
| **Risk** | **Low** today; confusion if re-enabled without migration |
| **Next investigation** | Mark deprecated in docs or remove in future cleanup phase |

---

### DOR-06 — `automation/` package

| Поле | Значение |
|------|----------|
| **Status** | STALE_RISK |
| **Why** | Workspace has `automation/__pycache__` (`engine`, `worker`, `tg_receiver`, …) but **no** `.py` sources; not in Railway start command |
| **Evidence** | Directory listing 2026-05-24 |
| **Risk** | **Low** if not deployed; **Medium** if stale code deployed elsewhere |
| **Next investigation** | Git history / `.gitignore`; confirm not packaged in deploy |

---

### DOR-07 — `automation/tests/` (glob vs disk)

| Поле | Значение |
|------|----------|
| **Status** | UNKNOWN |
| **Why** | Index/glob lists `test_excel_contract.py`, `test_wallet_group.py`; files **not readable** on disk in workspace scan |
| **Evidence** | Glob vs `Read` failure |
| **Risk** | **Low** for prod |
| **Next investigation** | `git status`, sparse checkout, or ignored paths |

---

### DOR-08 — `reporters/` package

| Поле | Значение |
|------|----------|
| **Status** | N/A (not present) |
| **Why** | No directory in repository |
| **Evidence** | Glob — 0 files |

---

## 4. Production Entrypoint State

### Deploy (CONFIRMED)

| Поле | Значение | Статус |
|------|----------|--------|
| Platform | Railway | CONFIRMED |
| Builder | NIXPACKS (`nixpacks.toml`) | CONFIRMED |
| Service | `file-analyzer` | CONFIRMED |
| Start command | `/opt/venv/bin/python scheduler.py` | CONFIRMED |
| Playwright | Chromium installed in build phase | CONFIRMED |

`Procfile` contains only `postinstall` (apt + playwright) — **not** the process start command (**CONFIRMED**).

### Startup sequence (CONFIRMED)

1. Load `scheduler.py`; read `TG_BOT_TOKEN` — empty → **fail-fast**.  
2. Build `python-telegram-bot` `Application`, register handlers from `tg_commands`.  
3. `RULES.get_snapshot(force_sync=True)` — download/refresh `rules.xlsx`.  
4. Start daemon threads (wallet hourly, raccoon hourly job, daily loop, restart worker).  
5. `app.run_polling(close_loop=False)` — main thread blocked.

**Side effect on import chain:** Importing `tg_commands` pulls downloaders/analyzers → **`telegram_bot` import requires `TELEGRAM_BOT_TOKEN`** (**CONFIRMED**). Startup order matters for env completeness.

### Background threads (CONFIRMED)

| Thread | Function | Schedule |
|--------|----------|----------|
| T1 | `run_raccoon_wallet_cycle` | Hourly at minute 0 (:05 sec) MSK |
| T2 | `run_hourly_raccoon_cycle` + `run_hourly_report` | Every `RACCOON_HOURLY_EVERY_MIN` (default 5) |
| T3 | `run_daily_conversion_loop` | Poll 10s; fire 00:00–00:02 MSK once/date |
| T4 | `restart_worker` | Next slot in `RESTART_TIMES` MSK |

`job_start` / `job_end` wrap T1–T2 exceptions (log + continue). T3 **does not** use job counter (**CONFIRMED** — STALE_RISK with restart).

### Planned restart (CONFIRMED)

- Times (MSK, hardcoded): 10:30, 13:30, 16:30, 19:30, 22:30, 01:30.  
- Waits until `active_jobs()==0` or `RESTART_GRACE_MIN` (default **10** minutes).  
- Then `os._exit(1)` — Railway restarts container (**comment in code CONFIRMED**).

### Timezone (CONFIRMED)

- Scheduler loops use `ZoneInfo("Europe/Moscow")`.  
- `downloader.py` sets `os.environ["TZ"]="Europe/Moscow"` when that module runs (not at scheduler start).

### Remains UNKNOWN

- Whether Railway runs **additional** services for `downloader.py`.  
- Exact production values for all env vars (not documented here by design).  
- Whether both Telegram tokens are set and match the same bot.

---

## 5. Configuration / Env State

> **Значения секретов не фиксируются.** Только имена, required/optional, defaults, conflicts.

### 5.1 Required for prod entry (scheduler path)

| Variable | Consumer | If missing | Статус |
|----------|----------|------------|--------|
| `TG_BOT_TOKEN` | `scheduler.py` | `RuntimeError` at start | CONFIRMED |
| `TELEGRAM_BOT_TOKEN` | `telegram_bot.py` (import chain) | `ValueError` at import | CONFIRMED |
| `RULES_XLSX_PATH` | `rules_provider`, `AccessRules` | rules not ready / guard deny | CONFIRMED |
| Dropbox auth (see below) | `dropbox_watcher` | `ValueError` at import | CONFIRMED |
| `RACCOON_LOGIN`, `RACCOON_PASSWORD` | Raccoon downloaders | `RuntimeError` on cycle | CONFIRMED |
| `TELEGRAM_CHAT_ID_HOURLY_RACCOON` | `raccoon_hourly_report` | import `RuntimeError` | CONFIRMED |
| `TELEGRAM_CHAT_ID_RACCOON_WALLET` | raccoon wallet + daily | import `RuntimeError` | CONFIRMED |

### 5.2 Required when manual Antares / Bakai jobs run

| Variable | Consumer | Статус |
|----------|----------|--------|
| `ANTARES_LOGIN`, `ANTARES_PASSWORD` | Antares downloaders | CONFIRMED |
| `TELEGRAM_CHAT_ID_ANALIZ` | conversion, payout, downloader | CONFIRMED |
| `TELEGRAM_CHAT_ID_WALLET` | wallet analyzer (or fallback) | CONFIRMED |
| `DROPBOX_INPUT_PATH`, `DROPBOX_PROCESSED_PATH` | main, downloader, payout | CONFIRMED |
| `CURRENT_RATE_BAKAI_CHAT_ID`, `NEW_RATE_BAKAI_CHAT_ID` | bakai monitor | CONFIRMED |

### 5.3 Dropbox env

| Variable | Required | Default | Статус |
|----------|----------|---------|--------|
| `DROPBOX_ACCESS_TOKEN` | One of token **or** refresh trio | — | CONFIRMED |
| `DROPBOX_REFRESH_TOKEN` | With APP_KEY/SECRET if no access token | — | CONFIRMED |
| `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET` | With refresh token | — | CONFIRMED |
| `DROPBOX_INPUT_PATH` | For file pipelines | — | CONFIRMED |
| `DROPBOX_PROCESSED_PATH` | For archive moves | — | CONFIRMED |
| `DROPBOX_SPECIAL_PATH` | Optional | `/Ostin/platform/special` | CONFIRMED |
| `DROPBOX_RULES_PATH` | DORMANT path | `/rules.xlsx` | DORMANT |
| `RULES_LOCAL_PATH` | DORMANT path | `/tmp/rules/rules.xlsx` | DORMANT |

### 5.4 Optional / defaults

| Variable | Default | Effect | Статус |
|----------|---------|--------|--------|
| `RULES_SYNC_MIN_INTERVAL_SEC` | `60` | Min interval between rules Dropbox syncs | CONFIRMED |
| `RACCOON_HOURLY_EVERY_MIN` | `5` | Hourly Raccoon job interval | CONFIRMED |
| `RESTART_GRACE_MIN` | `10` | Minutes wait before forced restart | CONFIRMED |
| `PLAYWRIGHT_HEADLESS` | truthy `"1"` | Headless browser | CONFIRMED |
| `TMP` | `/tmp` | payout temp path | CONFIRMED |
| `TELEGRAM_CHAT_ID` | — | Fallback for wallet downloaders | CONFIRMED |

### 5.5 Known conflicts (STALE_RISK)

| ID | Conflict | Runtime effect | Статус |
|----|----------|----------------|--------|
| **U1** | `TG_BOT_TOKEN` ≠ `TELEGRAM_BOT_TOKEN` or only one set | Polling vs send may use different bots or fail import | STALE_RISK |
| **U5** | `RULES_XLSX_PATH` vs `DROPBOX_RULES_PATH` | Only former active; latter dormant | STALE_RISK |

---

## 6. Data / File State

| Artifact | Status | Owner | Reader | Writer | Stale risk | Failure impact |
|----------|--------|-------|--------|--------|------------|----------------|
| **`rules.xlsx` (Dropbox)** | CONFIRMED | Operations / manual | `rules_provider`, access, analyzers | **UNKNOWN** in code (manual Dropbox) | Medium on outage (cached) | Commands denied; wallet rules wrong |
| **Cache `/tmp/rules_cache/rules.xlsx`** | CONFIRMED | `rules_provider` | Same | `rules_provider` download | TTL 60s + stat_key | Stale rules until sync |
| **Dropbox input folder** | CONFIRMED | Ops / `downloader` | `main` | `downloader` upload | — | No analysis |
| **Dropbox processed** | CONFIRMED | — | — | `main`, `payout` | — | Files stuck in input |
| **`special_cards.xlsx`** | CONFIRMED | **UNKNOWN** writer | `conversion.run` | External | — | Wrong start_date logic |
| **`/tmp/hourly_raccoon/payin.xlsx`** | CONFIRMED | `raccoon_hourly_downloader` | hourly + daily reports | hourly downloader | High if job fails | No hourly/daily TG report |
| **`/tmp/hourly_raccoon/last_sent.json`** | CONFIRMED | `raccoon_hourly_report` | same | same | By design (dedup) | Duplicate or missed alerts if corrupted |
| **`/tmp/dropbox_pipeline.lock`** | CONFIRMED | `run_once_guard` | main, downloader | same | 600s stale | Skipped or parallel analyze |
| **`/tmp/bakai_last_buy_rate.txt`** | CONFIRMED | bakai monitor | same | same | Low | Spurious “new rate” alerts |
| **Playwright `auth_state*.json`** | CONFIRMED | per downloader | Playwright | Playwright | Session expiry | Re-login needed |
| **`config/*.yaml`** | CONFIRMED | Git/deploy | analyzers | Git only | Redeploy to change | Behavior change without rules |
| **`main.last_card_path`** | CONFIRMED | `main` (memory) | same process | same | **High** cross-process | Wrong card pairing |

---

## 7. Runtime Risk Register

| ID | Status | Evidence | Impact | Likelihood | Severity | Recommended next action |
|----|--------|----------|--------|------------|----------|-------------------------|
| **U1** | STALE_RISK | `scheduler.py` `TG_BOT_TOKEN`; `telegram_bot.py` `TELEGRAM_BOT_TOKEN` | Wrong bot, silent send failures, or import crash | Medium | **Critical** | Audit Railway env; document single canonical name |
| **U2** | UNKNOWN | `downloader.py` not in scheduler; only `scheduler` in `railway.toml` | Antares auto conversion/payout may not run | Medium | **High** | Confirm Railway services/cron/ops runbook |
| **U3** | STALE_RISK | `PROJECT_REFERENCE.md` vs `scheduler.py` threads | Misjudged load, missed on-call expectations | High | **Medium** | Update PROJECT_REFERENCE in docs task |
| **U4** | DOCS_ONLY | `CONTRACT_BOT.md` vs `tg_commands` | Operators assume bot edits rules | Medium | **Medium** | Reconcile contract vs SOP |
| **U5** | STALE_RISK | `download_rules_xlsx` + dormant env paths | Confusion on rules path | Low | **Low** | Deprecate in docs |
| **U6** | DORMANT | Empty `core/events.py` | No structured audit trail | N/A | **Medium** (governance) | Scope observability separately |
| **U7** | DORMANT | No hourly Antares imports | Missing hourly Antares reports | Low if retired | **Medium** | Confirm with stakeholders |
| **U8** | STALE_RISK | Daily loop outside `_active_jobs` | Missed daily report on restart window | Low | **Medium** | Log correlation at 00:00 MSK |
| **U9** | UNKNOWN | `CONTRACT_RULES` meta.version; no meta read in scanned code | Wrong rules version applied | Medium | **Medium** | Code search + runtime test for meta sheet |
| **U10** | STALE_RISK | `.env` in repo (grep in prior scan) | Secret leakage | Unknown | **High** (security) | Ensure secrets only in Railway, not git |
| **U11** | STALE_RISK | `tests/` only pycache, no `.py` | Regressions undetected | High | **Medium** | Restore/commit test sources |
| **U12** | STALE_RISK | `automation/` pyc without sources | Unknown deploy surface | Low | **Low–Medium** | Inspect git + deploy artifact |

---

## 8. Technical Debt / Cleanup Candidates

**Только перечисление — ничего не удалять в Stage A3.**

### Safe docs cleanup candidates

| Item | Статус | Notes |
|------|--------|-------|
| Align `PROJECT_REFERENCE.md` scheduler section with actual threads | STALE_RISK | Docs-only |
| Mark `CONTRACT_BOT` edit flows as DOCS_ONLY or future | DOCS_ONLY | Docs-only |
| Document canonical Telegram token env name | STALE_RISK | Docs-only |
| Deprecate `DROPBOX_RULES_PATH` / `download_rules_xlsx` in docs | DORMANT | Docs-only |
| `architecture_map.md` / `contracts.md` as new KB source of truth | CONFIRMED | Prefer over stale sections |

### Runtime-dangerous cleanup candidates

| Item | Статус | Notes |
|------|--------|-------|
| Remove `download_rules_xlsx` without checking callers | DORMANT | Currently uncalled — still risky if external |
| Delete `automation/__pycache__` without confirming deploy | STALE_RISK | May be harmless |
| Unify token env without Railway update | STALE_RISK | **Breaks prod** if wrong |
| Remove `hourly_downloader` / `hourly_report` | DORMANT | Confirm unused first |
| Delete `tests/` pycache only | STALE_RISK | Loses hint of past tests |

### Needs investigation before cleanup

| Item | Статус |
|------|--------|
| `automation/` historical package | STALE_RISK |
| `tests/` missing `.py` sources | STALE_RISK |
| `glob` vs disk for `automation/tests/*.py` | UNKNOWN |
| Second Railway service for `downloader.py` | UNKNOWN |
| `meta.version` validation location | UNKNOWN |

---

## 9. Current Testing State

### Repository layout (snapshot 2026-05-24)

| Location | `.py` sources | Artifacts | Статус |
|----------|---------------|-----------|--------|
| `tests/` (repo root) | **None on disk** | `__pycache__` for `test_wallet_time_logic`, `test_validate_rules_xlsx`, `test_normalization_datetime`, `test_datetime_utils`; `.pytest_cache` | STALE_RISK |
| `automation/tests/` | **Not found on disk** (glob index references files) | — | UNKNOWN |
| `automation/` | **None** | `__pycache__` (`engine`, `worker`, `tg_receiver`, …), `logs/app.log` | STALE_RISK |
| Root `tests/` in forbidden edit list | User rule: do not change `tests/*` in this stage | — | CONFIRMED policy |

**No `pytest` / `def test_` matches in readable `.py` files** in workspace grep (**CONFIRMED** for current checkout).

### Inferred past coverage (from `.pyc` names only — STALE_RISK)

| Area | Evidence | Current confidence |
|------|----------|-------------------|
| Wallet time logic | `test_wallet_time_logic.cpython-312-*.pyc` | STALE_RISK — sources missing |
| Rules xlsx validation | `test_validate_rules_xlsx.cpython-312-*.pyc` | STALE_RISK |
| Datetime utils / normalization | `test_normalization_datetime`, `test_datetime_utils` pyc | STALE_RISK |

### Critical paths **not** covered (in current workspace)

| Path | Priority for future tests |
|------|---------------------------|
| `scheduler.py` threads + restart interaction | High |
| `rules_provider` fail-safe + TTL | High |
| `access_guard` deny reasons | High |
| `raccoon_hourly_report` fingerprint dedup | Medium |
| `main.process_file` + lock | Medium |
| Playwright downloaders | Low (integration/e2e, flaky) |

### Suggested tests to add later (not in scope now)

1. Contract tests for `rules.xlsx` sheets (access, commands, exclude_time) — extend if `test_validate_rules_xlsx` restored.  
2. Unit tests for `get_rules_snapshot` cache/TTL/fail-safe.  
3. Scheduler job counter + daily loop registration (regression for X-08).  
4. Env contract test: single Telegram token policy.  
5. `selector.get_analyzer` routing table driven by fixture filenames.

---

## 10. Immediate Next Tasks

Draft tasks for `project_memory/tasks.md` — **no implementation in Stage A3**.

| Task ID draft | Goal | Why now | Risk reduced | Priority |
|---------------|------|---------|--------------|----------|
| **TASK-A3-01** | Verify `TG_BOT_TOKEN` and `TELEGRAM_BOT_TOKEN` in Railway (same bot, both set) | U1 blocks reliable TG | Critical Telegram split | **P0** |
| **TASK-A3-02** | Investigate production binding for `integrations/downloader.py` | U2 — Antares pipeline unknown | High ops gap | **P0** |
| **TASK-A3-03** | Verify `rules.xlsx` `meta.version` enforcement in runtime | U9 — contract vs code | Medium rules safety | **P1** |
| **TASK-A3-04** | Reconcile `CONTRACT_BOT.md` with actual `tg_commands` capabilities | U4 — docs-only editing | Medium process | **P1** |
| **TASK-A3-05** | Inspect `automation/` and `tests/` artifacts (pyc without `.py`) | U11, U12 | Medium quality/deploy clarity | **P1** |
| **TASK-A3-06** | Update `PROJECT_REFERENCE.md` scheduler section to match `scheduler.py` | U3 | Medium on-call confusion | **P2** |
| **TASK-A3-07** | Confirm Raccoon daily report at 00:00 vs restart window (logs) | U8 | Medium missed report | **P2** |
| **TASK-A3-08** | Document operational SOP: rules change path (Dropbox manual vs bot) | U4 | Medium governance | **P2** |
| **TASK-A3-09** | Decide fate of Antares hourly (`hourly_downloader` / `hourly_report`) | U7 | Low–Medium | **P3** |
| **TASK-A3-10** | Restore or re-create missing `tests/*.py` from VCS/history | U11 | Medium regression safety | **P2** |

---

## Capabilities snapshot (R#)

| ID | Capability | Active in prod? | Статус |
|----|------------|-----------------|--------|
| R1 | Raccoon wallet analytics | **Yes** (scheduled) | CONFIRMED |
| R2 | Raccoon hourly traffic report | **Yes** (scheduled) | CONFIRMED |
| R3 | Raccoon daily conversion | **Yes** (scheduled) | CONFIRMED |
| R4 | Antares conversion/payout via Dropbox | **Partial** (code yes; scheduler no) | CONFIRMED + UNKNOWN schedule |
| R5 | Antares wallet | **Manual** (TG/CLI) | CONFIRMED |
| R6 | Antares full downloader | **Unknown** | UNKNOWN |
| R7 | Bakai rate monitor | **Manual** (TG) | CONFIRMED |
| R8 | Antares hourly | **No** | DORMANT |
| R9 | Event audit trail | **No** | DORMANT |
| R10 | Bot rules editing | **No** | DOCS_ONLY |

---

## Integrations (operational)

| Integration | Works in prod? | Notes | Статус |
|-------------|----------------|-------|--------|
| Telegram | **Partial / risk** | Polling + send need two env vars | STALE_RISK (U1) |
| Dropbox | **Yes** (expected) | Required for rules + file pipelines | CONFIRMED |
| Raccoon (Playwright) | **Yes** | Core scheduled workloads | CONFIRMED |
| Antares (Playwright) | **On-demand** | Not in scheduler | CONFIRMED |
| Bakai (Playwright) | **On-demand** | `/run_rate` only | CONFIRMED |

---

## Known operational limits

| Limit | Value / policy | Источник | Статус |
|-------|----------------|----------|--------|
| Pipeline lock stale | 600 sec | `run_once_guard.py` | CONFIRMED |
| Rules sync TTL | 60 sec default | `RULES_SYNC_MIN_INTERVAL_SEC` | CONFIRMED |
| Scheduled job error | log + continue | `scheduler.run_every_minutes` | CONFIRMED |
| Manual TG job concurrency | 1 at a time | `tg_commands._running_lock` | CONFIRMED |
| Restart grace | 10 min default | `RESTART_GRACE_MIN` | CONFIRMED |
| Bakai monitor window | 08:00–23:55 MSK | `bakai_monitor_playwright` | CONFIRMED |

---

## DOCS_ONLY in prod

| Item | Why not in prod | Статус |
|------|-----------------|--------|
| Telegram bot edits `rules.xlsx` | No write handlers | DOCS_ONLY |
| Append-only `events` | Empty `core/events.py` | DOCS_ONLY |
| `config/access/roles.yaml` paths in `ARCHITECTURE.md` | Access via `rules.xlsx` sheets | STALE_RISK vs ARCHITECTURE |

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | Stage A3: initial current state snapshot |
