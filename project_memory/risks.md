# Risk Register — `analizis`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Последнее обновление** | 2026-05-24 |
| **Метод** | Stage A5 — extraction из `architecture_map.md`, `contracts.md`, `current_state.md`, `decisions.md`, codebase, repo docs |
| **Связанные документы** | `decisions.md` (E#/DOC#), `contracts.md` (X-#), `current_state.md` (U#) |

**Статусы риска:** CONFIRMED | DORMANT | UNKNOWN | DOCS_ONLY | STALE_RISK

---

## 1. Executive Summary

| Метрика | Значение |
|---------|----------|
| **Всего рисков в реестре** | **32** (R01–R32) |
| **Critical** | **4** |
| **High** | **11** |
| **Medium** | **13** |
| **Low** | **4** |

### Top 5 наиболее опасных для production

| Rank | ID | Title | Severity |
|------|-----|-------|----------|
| 1 | **R01** | Split Telegram bot tokens (`TG_BOT_TOKEN` vs `TELEGRAM_BOT_TOKEN`) | **Critical** |
| 2 | **R06** | Single container / `scheduler.py` single point of failure | **Critical** |
| 3 | **R07** | Hard dependency on Dropbox + `rules.xlsx` availability | **Critical** |
| 4 | **R04** | Secrets exposure via `.env` in repository history | **Critical** |
| 5 | **R02** | Antares `downloader.py` production binding unknown | **High** |

**Сводка:** Production — один Railway-контейнер с ботом, Raccoon scheduled jobs и жёстким рестартом. Наибольшие угрозы: **неправильная конфигурация Telegram**, **полная остановка при падении scheduler**, **недоступность Dropbox/rules**, **возможная остановка Antares auto-pipeline**, **утечка секретов**. Документация частично описывает систему, которой нет в runtime (bot rule edit, events, thin scheduler).

---

## 2. Risk Register

### R01 — Split Telegram bot token environment variables

| Field | Value |
|-------|-------|
| **Risk ID** | R01 |
| **Title** | `TG_BOT_TOKEN` vs `TELEGRAM_BOT_TOKEN` mismatch |
| **Status** | STALE_RISK |
| **Category** | Telegram, Security, Runtime |

**Description**  
Polling (`scheduler.py`) and outbound API (`telegram_bot.py`) read **different** env variable names. Misconfiguration can cause startup failure, silent use of two bots, or commands on one bot while reports go to another.

**Evidence**  
`scheduler.py:138` (`TG_BOT_TOKEN`); `telegram_bot.py:15` (`TELEGRAM_BOT_TOKEN`); `contracts.md` X-01; `decisions.md` Q1; import chain requires both on boot.

**Impact**  
No alerts to operators; bot appears up but reports fail; or container fails on import before polling.

**Likelihood** | **Medium** (if env not audited)  
**Severity** | **Critical**

**Detection**  
`/status` works but no analyzer messages; import `ValueError`; Railway env audit shows only one variable set.

**Mitigation** (investigation only — not implemented)  
Canonical single env name; document Railway vars; smoke test send + command.

**Recommended Investigation**  
Railway env: both vars set and equal; send test from scheduled job path.

**Affected Contracts** | RT-010, INT-01, E5  
**Affected Runtime Paths** | All pipelines using `send_message_sync`; `scheduler` startup

---

### R02 — `integrations/downloader.py` production schedule unknown

| Field | Value |
|-------|-------|
| **Risk ID** | R02 |
| **Title** | Antares full downloader may not run in production |
| **Status** | UNKNOWN |
| **Category** | Operations, Scheduler, Runtime |

**Description**  
Full Antares export → Dropbox → `main.process_file` exists but is **not** started by `scheduler.py` or `railway.toml`.

**Evidence**  
`railway.toml` only `scheduler.py`; no import of `downloader` in scheduler; `architecture_map.md` U2; `current_state.md` DOR-01.

**Impact**  
Antares conversion/payout automation absent if no separate Railway service/cron/manual ops — **business continuity gap**.

**Likelihood** | **Medium** (if ops assumed automated)  
**Severity** | **High**

**Detection**  
No new `conversion_*` files in Dropbox input; no downloader Telegram messages; gap in expected report times.

**Mitigation**  
Confirm runbook; add second service or TG-scheduled trigger if required.

**Recommended Investigation**  
Railway dashboard: additional services; ops interview; Dropbox input folder activity logs.

**Affected Contracts** | RT-016, P2 (`architecture_map.md`)  
**Affected Runtime Paths** | Antares conversion/payout pipeline

---

### R03 — `rules.xlsx` `meta.version` enforcement not confirmed in code

| Field | Value |
|-------|-------|
| **Risk ID** | R03 |
| **Title** | Contracted rules version gate may not run at runtime |
| **Status** | UNKNOWN |
| **Category** | Rules, Runtime |

**Description**  
`CONTRACT_RULES.md` states version mismatch → job refusal. No confirmed Python reader for `meta` sheet in KB scan.

**Evidence**  
`CONTRACT_RULES.md` §3.1; `contracts.md` X-07, DOC2; grep — no `sheet_name="meta"` in scanned paths.

**Impact**  
Incompatible rules applied → wrong limits/thresholds/exclusions; silent behavioral drift.

**Likelihood** | **Medium** (on rules file updates)  
**Severity** | **Medium**

**Detection**  
Behavior change after rules edit without deploy; no logged version rejection.

**Mitigation**  
Implement meta check or formalize manual verification SOP.

**Recommended Investigation**  
Full codebase search `meta` sheet; test upload wrong version in staging.

**Affected Contracts** | FC-001, DOC2, E4  
**Affected Runtime Paths** | Wallet/raccoon analyzers, guard (partial)

---

### R04 — Secrets exposure in repository (`.env`)

| Field | Value |
|-------|-------|
| **Risk ID** | R04 |
| **Title** | Credentials may exist in tracked or historical `.env` |
| **Status** | STALE_RISK |
| **Category** | Security |

**Description**  
Prior codebase scan indicated tokens present in `.env`. Values must not be in git; rotation may be required if ever committed.

**Evidence**  
`architecture_map.md` U10; `current_state.md` U10; `.env` referenced by `telegram_bot` `load_dotenv()`.

**Impact**  
Token compromise → unauthorized Dropbox/Telegram/Antares/Raccoon access.

**Likelihood** | **Unknown** (depends on git history)  
**Severity** | **Critical** (if committed)

**Detection**  
`git log` / secret scanning; unexpected API usage.

**Mitigation**  
Secrets only in Railway; rotate tokens; `.gitignore` audit; remove from history if leaked.

**Recommended Investigation**  
`git log -p -- .env`; GitHub secret scanning; confirm `.env` not in remote.

**Affected Contracts** | All INT-* auth env  
**Affected Runtime Paths** | Entire system

---

### R05 — Documentation vs runtime divergence (aggregate)

| Field | Value |
|-------|-------|
| **Risk ID** | R05 |
| **Title** | Multiple docs describe non-runtime behavior |
| **Status** | STALE_RISK |
| **Category** | Documentation, Operations |

**Description**  
`PROJECT_REFERENCE`, `ARCHITECTURE.md`, `CONTRACT_BOT.md` partially contradict scheduler, bot capabilities, observability.

**Evidence**  
`decisions.md` DOC1–DOC4, Q2; `contracts.md` X-02, X-03; `current_state.md` U3, U4.

**Impact**  
Wrong operational assumptions; missed automation; incorrect incident response.

**Likelihood** | **High** (docs actively used)  
**Severity** | **Medium**

**Detection**  
Incident confusion; new operator onboarding gaps.

**Mitigation**  
KB (`architecture_map`, `current_state`, `contracts`, `decisions`) as primary; deprecate stale sections.

**Recommended Investigation**  
Doc reconciliation task against KB.

**Affected Contracts** | DOC1–4  
**Affected Runtime Paths** | Governance (all)

---

### R06 — Scheduler single point of failure

| Field | Value |
|-------|-------|
| **Risk ID** | R06 |
| **Title** | Single process hosts bot + all Raccoon schedules + restart |
| **Status** | CONFIRMED |
| **Category** | Architecture, Scheduler, Operations |

**Description**  
One Railway container, one Python process. Failure or `os._exit(1)` stops polling and all background threads together.

**Evidence**  
`railway.toml`; `scheduler.py` `main()`; `decisions.md` E2, E9.

**Impact**  
Total loss of Raccoon reports, TG commands, and scheduled downloads until platform restart.

**Likelihood** | **Medium** (Playwright/OOM/crash)  
**Severity** | **Critical**

**Detection**  
Railway service down; no TG responses; gap in hourly messages.

**Mitigation**  
Railway health checks; external heartbeat alert; future split services (out of scope).

**Recommended Investigation**  
Railway uptime metrics; correlate restarts with `RESTART_TIMES` and OOM.

**Affected Contracts** | SCH-01..05, E2, E9  
**Affected Runtime Paths** | All ARS-* in `current_state.md`

---

### R07 — `rules.xlsx` / Dropbox availability dependency

| Field | Value |
|-------|-------|
| **Risk ID** | R07 |
| **Title** | Control plane unavailable blocks ACL or applies stale rules |
| **Status** | CONFIRMED |
| **Category** | Dropbox, Rules, Runtime |

**Description**  
Rules sync from Dropbox; fail-closed guard on TG when rules broken; fail-safe serves **cached** rules for jobs when sync fails.

**Evidence**  
`rules_provider.py`; `access_guard.py`; `dropbox_watcher` import-time auth; `decisions.md` E3, E4, E8.

**Impact**  
Commands denied; or analytics run on **outdated/wrong** rules during outage.

**Likelihood** | **Low–Medium**  
**Severity** | **Critical** (wrong financial/ops decisions)

**Detection**  
`rules_not_ready` messages; prolonged unchanged behavior after rules edit.

**Mitigation**  
Monitor Dropbox API; alert on sync failures; manual `/reload_rules` after fix.

**Recommended Investigation**  
Log patterns for rules download failures; DR for rules file.

**Affected Contracts** | RT-005, RT-006, RT-007, FC-001, C-01  
**Affected Runtime Paths** | P8, wallet/raccoon analyzers

---

### R08 — Stale `__pycache__` / bytecode without sources

| Field | Value |
|-------|-------|
| **Risk ID** | R08 |
| **Title** | `automation/` and `tests/` pyc without `.py` in workspace |
| **Status** | STALE_RISK |
| **Category** | Testing, Architecture |

**Description**  
`automation/__pycache__` (engine, worker, tg_receiver, …) and `tests/**/__pycache__` exist; source `.py` missing or unreadable on disk.

**Evidence**  
`current_state.md` §9, DOR-06/07; shell listing 2026-05-24.

**Impact**  
Unknown deploy surface; false belief tests exist; accidental execution of orphan bytecode — **UNKNOWN**.

**Likelihood** | **Low**  
**Severity** | **Low**

**Detection**  
Repo audit; deploy artifact inspection.

**Mitigation**  
Investigate git history; confirm not in start path.

**Recommended Investigation**  
`.gitignore`; Railway build output; whether pyc committed.

**Affected Contracts** | —  
**Affected Runtime Paths** | **UNKNOWN** (not in `railway.toml`)

---

### R09 — Missing / absent automated test sources

| Field | Value |
|-------|-------|
| **Risk ID** | R09 |
| **Title** | No runnable test suite confirmed in workspace |
| **Status** | STALE_RISK |
| **Category** | Testing |

**Description**  
`tests/` has pytest cache and `.pyc` names (`test_validate_rules_xlsx`, etc.) but no `.py` sources. No `pytest`/`def test_` in readable `.py` grep.

**Evidence**  
`current_state.md` §9; workspace scan.

**Impact**  
Regressions in rules validation, scheduler, Raccoon pipelines undetected until production.

**Likelihood** | **High** (on any change)  
**Severity** | **Medium**

**Detection**  
CI absent or empty; manual-only verification.

**Mitigation**  
Restore tests from VCS; add contract tests for rules and env (future).

**Recommended Investigation**  
Git history for `tests/`; CI configuration.

**Affected Contracts** | FC-001, RT-005, SCH-*  
**Affected Runtime Paths** | All critical paths

---

### R10 — Full operational dependency on Telegram

| Field | Value |
|-------|-------|
| **Risk ID** | R10 |
| **Title** | Primary output channel and control plane is Telegram |
| **Status** | CONFIRMED |
| **Category** | Telegram, Operations |

**Description**  
Reports, alerts, manual jobs, and status all via Telegram. No secondary channel in code.

**Evidence**  
All analyzers use `send_message_sync`; `tg_commands` for control; `decisions.md` E5.

**Impact**  
Telegram outage/API block → operators blind; cannot trigger jobs.

**Likelihood** | **Low–Medium**  
**Severity** | **High**

**Detection**  
API errors in logs; user reports.

**Mitigation**  
External monitoring; optional webhook/backup channel (not in scope).

**Recommended Investigation**  
Telegram API status procedures; rate limits.

**Affected Contracts** | INT-01, RT-010  
**Affected Runtime Paths** | All report pipelines

---

### R11 — Hard restart `os._exit(1)` interrupts work

| Field | Value |
|-------|-------|
| **Risk ID** | R11 |
| **Title** | Scheduled restart kills process without graceful shutdown |
| **Status** | CONFIRMED |
| **Category** | Scheduler, Runtime |

**Description**  
`restart_worker` calls `os._exit(1)` after grace period; daily conversion loop not in `_active_jobs`.

**Evidence**  
`scheduler.py`; `decisions.md` E9, I5; `current_state.md` U8.

**Impact**  
Mid-job Playwright/download killed; missed 00:00 daily report; polling drop.

**Likelihood** | **Medium** (6× daily restart windows)  
**Severity** | **Medium**

**Detection**  
Logs «Restarting process now» near :30 MSK slots; missing daily message.

**Mitigation**  
Include daily loop in job counter; adjust restart times (future).

**Recommended Investigation**  
Log correlation at `RESTART_TIMES` and 00:00 MSK.

**Affected Contracts** | SCH-05, E9  
**Affected Runtime Paths** | ARS-04, ARS-02, polling

---

### R12 — Playwright UI fragility

| Field | Value |
|-------|-------|
| **Risk ID** | R12 |
| **Title** | Browser automation breaks on portal UI changes |
| **Status** | CONFIRMED |
| **Category** | Runtime, Operations |

**Description**  
Raccoon/Antares/Bakai flows depend on selectors, calendars, Export buttons.

**Evidence**  
`integrations/*_downloader*.py`, `bakai_monitor_playwright.py`; `PROJECT_REFERENCE` high-risk zones.

**Impact**  
Failed downloads; silent skip until next cycle; incomplete reports.

**Likelihood** | **Medium**  
**Severity** | **High**

**Detection**  
Exceptions in logs; screenshots in `/tmp` or `/app/logs`.

**Mitigation**  
Visual regression monitoring; alert on consecutive failures.

**Recommended Investigation**  
Failure rate per cycle in logs.

**Affected Contracts** | INT-03, INT-04, INT-05, E14  
**Affected Runtime Paths** | P4, P5, P7, manual Antares jobs

---

### R13 — Dropbox API wrapper without retry

| Field | Value |
|-------|-------|
| **Risk ID** | R13 |
| **Title** | Transient Dropbox failures return False without retry |
| **Status** | CONFIRMED |
| **Category** | Dropbox |

**Description**  
`download_file` / `upload_file` / `move_file` log and return False.

**Evidence**  
`dropbox_watcher.py`; `contracts.md` INT-02.

**Impact**  
Skipped file processing; rules sync failure; stuck input files.

**Likelihood** | **Medium**  
**Severity** | **Medium**

**Detection**  
Error logs `Ошибка download_file`; files remain in input.

**Mitigation**  
Retry wrapper; alert on repeated failures.

**Recommended Investigation**  
Dropbox error rate in production logs.

**Affected Contracts** | INT-02, RT-001  
**Affected Runtime Paths** | P1, P2, rules sync

---

### R14 — Fail-safe serves stale rules during outage

| Field | Value |
|-------|-------|
| **Risk ID** | R14 |
| **Title** | Jobs continue with last cached `rules.xlsx` when sync fails |
| **Status** | CONFIRMED |
| **Category** | Rules |

**Description**  
Intentional `rules_provider` behavior per E8 — not fail-closed for analyzers.

**Evidence**  
`rules_provider.py` except branch; `decisions.md` E8.

**Impact**  
Analytics contradict latest business policy during incident.

**Likelihood** | **Low–Medium**  
**Severity** | **High**

**Detection**  
Rules changed in Dropbox but behavior unchanged; sync errors in logs.

**Mitigation**  
Alert on stale `loaded_at_ts`; optional max staleness (future).

**Recommended Investigation**  
Define acceptable staleness SLA with business.

**Affected Contracts** | RT-005, C-01, E8  
**Affected Runtime Paths** | Wallet/raccoon analyzers

---

### R15 — In-memory `last_card_path` pairing

| Field | Value |
|-------|-------|
| **Risk ID** | R15 |
| **Title** | Card/conversion pairing not shared across processes |
| **Status** | CONFIRMED |
| **Category** | Runtime, Architecture |

**Description**  
`main.last_card_path` global only within single process.

**Evidence**  
`main.py`; `architecture_map.md` U8, INV10; `decisions.md` Q3.

**Impact**  
Wrong or missing card pairing on separate CLI runs; payout/conversion skipped.

**Likelihood** | **Medium** (multi-invocation ops)  
**Severity** | **Medium**

**Detection**  
Warning «не найден вспомогательный файл» in Telegram.

**Mitigation**  
Always pass `aux_filename`; document ops procedure.

**Recommended Investigation**  
How `downloader.py` invokes `process_file` (passes aux — CONFIRMED).

**Affected Contracts** | RT-001, C-07  
**Affected Runtime Paths** | P1, P2

---

### R16 — Ephemeral `/tmp` state lost on container restart

| Field | Value |
|-------|-------|
| **Risk ID** | R16 |
| **Title** | Hourly dedup state and payin artifact not durable across restarts |
| **Status** | CONFIRMED |
| **Category** | Architecture, Scheduler |

**Description**  
`/tmp/hourly_raccoon/payin.xlsx`, `last_sent.json`, auth states, rules cache live on container disk.

**Evidence**  
`raccoon_hourly_report.py`; `contracts.md` C-04, FC-004/005; Railway restart E9.

**Impact**  
Duplicate hourly report after restart; daily report uses missing/stale payin until next hourly job.

**Likelihood** | **Medium** (6 restarts/day)  
**Severity** | **Medium**

**Detection**  
Duplicate TG message after deploy; `FileNotFoundError` in daily path.

**Mitigation**  
Persistent volume — **UNKNOWN** if configured on Railway.

**Recommended Investigation**  
Railway volume mounts; post-restart behavior.

**Affected Contracts** | FC-004, FC-005, E11  
**Affected Runtime Paths** | P5, P6

---

### R17 — Concurrent Playwright jobs without global lock

| Field | Value |
|-------|-------|
| **Risk ID** | R17 |
| **Title** | Scheduled Raccoon jobs and manual TG jobs can overlap |
| **Status** | CONFIRMED |
| **Category** | Scheduler, Runtime |

**Description**  
`_running_lock` only for manual TG jobs; wallet hourly thread and hourly raccoon thread can run concurrently with `/run_raccoon`.

**Evidence**  
`scheduler.py` threads; `tg_commands.py`; `decisions.md` E12.

**Impact**  
Session/auth file contention; resource exhaustion; flaky downloads.

**Likelihood** | **Medium**  
**Severity** | **Medium**

**Detection**  
Overlapping Playwright errors; high CPU.

**Mitigation**  
Global Playwright semaphore (future).

**Recommended Investigation**  
Log timestamps of concurrent job starts.

**Affected Contracts** | SCH-02/03, RT-011  
**Affected Runtime Paths** | Raccoon downloaders

---

### R18 — `exclude_time` fatal validation stops wallet analyzers

| Field | Value |
|-------|-------|
| **Risk ID** | R18 |
| **Title** | Invalid `exclude_time` sheet aborts wallet analysis |
| **Status** | CONFIRMED |
| **Category** | Rules, Runtime |

**Description**  
`get_exclude_time_df` raises `RuntimeError` on fatal validation.

**Evidence**  
`config_manager.py`; `wallet_analyzer.py`; `architecture_map.md` INV9.

**Impact**  
No wallet report for cycle; operator must fix Excel.

**Likelihood** | **Low–Medium** (on bad edit)  
**Severity** | **Medium**

**Detection**  
Telegram «rules exclude_time остановил» messages.

**Mitigation**  
Pre-validate rules before enable; staging sheet copy.

**Recommended Investigation**  
Frequency of fatal validation in logs.

**Affected Contracts** | RT-008, FC-001  
**Affected Runtime Paths** | P3, P4

---

### R19 — Raccoon wallet payout download disabled

| Field | Value |
|-------|-------|
| **Risk ID** | R19 |
| **Title** | `run_raccoon_wallet_cycle` always passes `payout_path=None` |
| **Status** | CONFIRMED |
| **Category** | Runtime, Operations |

**Description**  
Payout download code commented out in `raccoon_wallet_downloader.py`.

**Evidence**  
`raccoon_wallet_downloader.py`; `architecture_map.md` INV10.

**Impact**  
Incomplete wallet analytics vs Antares wallet path (which downloads payout).

**Likelihood** | **Certain** (by design in code)  
**Severity** | **Medium** (if payout expected)

**Detection**  
Code review; missing payout metrics in reports.

**Mitigation**  
Confirm intentional; update ops docs.

**Recommended Investigation**  
Business requirement for Raccoon payout in wallet report.

**Affected Contracts** | RT-013  
**Affected Runtime Paths** | P4

---

### R20 — Bot rules editing documented but not implemented

| Field | Value |
|-------|-------|
| **Risk ID** | R20 |
| **Title** | `CONTRACT_BOT` / `RULES_EDITING` promise bot-only rule changes |
| **Status** | DOCS_ONLY |
| **Category** | Documentation, Rules, Operations |

**Description**  
No write handlers; only `/reload_rules`. Manual Dropbox edit likely in practice.

**Evidence**  
`CONTRACT_BOT.md`, `RULES_EDITING.md`; `tg_commands.py`; `decisions.md` DOC1.

**Impact**  
No audit trail per contract; human Excel errors; concurrent edit conflicts.

**Likelihood** | **High** (manual edits occur)  
**Severity** | **Medium**

**Detection**  
Ops workflow interview; rules changed without bot events.

**Mitigation**  
SOP for manual edit; or implement bot (future).

**Recommended Investigation**  
How rules are edited today.

**Affected Contracts** | DOC1, RT-DOCS-01  
**Affected Runtime Paths** | Control plane (external)

---

### R21 — Observability / events plane not implemented

| Field | Value |
|-------|-------|
| **Risk ID** | R21 |
| **Title** | No structured event log despite architecture contract |
| **Status** | DORMANT |
| **Category** | Architecture, Documentation |

**Description**  
`core/events.py` empty; `ARCHITECTURE.md` describes append-only events and cold storage.

**Evidence**  
`core/events.py`; `decisions.md` D2; `architecture_map.md` R9.

**Impact**  
Post-incident reconstruction relies on unstructured logs only.

**Likelihood** | **High** (on incidents)  
**Severity** | **Medium**

**Detection**  
No `events_*.jsonl` artifacts.

**Mitigation**  
Implement events or downgrade ARCHITECTURE claims in docs.

**Recommended Investigation**  
Logging adequacy for audits.

**Affected Contracts** | D2, DOC3  
**Affected Runtime Paths** | N/A

---

### R22 — Antares hourly pipeline dormant

| Field | Value |
|-------|-------|
| **Risk ID** | R22 |
| **Title** | `hourly_downloader` + `hourly_report` not wired to prod |
| **Status** | DORMANT |
| **Category** | Runtime, Documentation |

**Description**  
Modules exist; `TELEGRAM_CHAT_ID_HOURLY` required if imported.

**Evidence**  
No scheduler imports; `architecture_map.md` R8, U7.

**Impact**  
Missing Antares hourly reports if still expected by stakeholders.

**Likelihood** | **Low** if retired; **Medium** if expected  
**Severity** | **Medium**

**Detection**  
Stakeholder expectation mismatch.

**Recommended Investigation**  
Confirm feature retirement.

**Affected Contracts** | RT-D03  
**Affected Runtime Paths** | Dormant Antares hourly

---

### R23 — Dual exclude mechanisms (YAML vs rules)

| Field | Value |
|-------|-------|
| **Risk ID** | R23 |
| **Title** | Conversion uses YAML exclude; wallet uses `rules.xlsx` exclude_time |
| **Status** | CONFIRMED |
| **Category** | Rules, Architecture |

**Description**  
Two sources of exclusion policy — operational confusion.

**Evidence**  
`conversion.py` pools exclude; `config_manager` exclude_time; `decisions.md` E15.

**Impact**  
Operator fixes wrong layer; inconsistent partner treatment.

**Likelihood** | **Medium**  
**Severity** | **Medium**

**Mitigation**  
Document which analyzer uses which source (KB).

**Affected Contracts** | RT-003, RT-008, E15  
**Affected Runtime Paths** | P1 vs P3/P4

---

### R24 — `PROJECT_REFERENCE` understates scheduler automation

| Field | Value |
|-------|-------|
| **Risk ID** | R24 |
| **Title** | Docs say scheduler does not run jobs automatically |
| **Status** | STALE_RISK |
| **Category** | Documentation |

**Description**  
Contradicts `scheduler.py` daemon threads.

**Evidence**  
`PROJECT_REFERENCE.md` § scheduler; `contracts.md` X-02; `decisions.md` DOC4.

**Impact** | On-call underestimates background load |  
**Severity** | **Low–Medium**

**Affected Contracts** | DOC4

---

### R25 — Import-time failure if `TELEGRAM_BOT_TOKEN` missing at boot

| Field | Value |
|-------|-------|
| **Risk ID** | R25 |
| **Title** | Scheduler import chain requires outbound token even before first send |
| **Status** | CONFIRMED |
| **Category** | Telegram, Runtime |

**Description**  
`tg_commands` imports analyzers/downloaders → `telegram_bot` → raises without token.

**Evidence**  
`current_state.md` §4 startup; `decisions.md` I4.

**Impact**  
Container crash loop if only `TG_BOT_TOKEN` set.

**Likelihood** | **Medium** (misconfigured env)  
**Severity** | **High**

**Related** | R01

---

### R26 — No structured health endpoint

| Field | Value |
|-------|-------|
| **Risk ID** | R26 |
| **Title** | No HTTP health check for Railway liveness |
| **Status** | CONFIRMED |
| **Category** | Operations, Architecture |

**Description**  
Process is polling-only; platform may not detect hung event loop.

**Evidence**  
`scheduler.py` — no HTTP server in scanned entry.

**Impact**  
Zombie process — alive to Railway but not processing.

**Likelihood** | **Low**  
**Severity** | **Medium**

**Affected Runtime Paths** | SCH-01

---

### R27 — `special_cards.xlsx` writer unknown

| Field | Value |
|-------|-------|
| **Risk ID** | R27 |
| **Title** | Conversion depends on Dropbox special file with no in-repo writer |
| **Status** | UNKNOWN |
| **Category** | Dropbox, Runtime |

**Description**  
`conversion.run` downloads `special_cards.xlsx`; writer not in codebase.

**Evidence**  
`conversion.py`; `architecture_map.md` FC-003.

**Impact**  
Stale special rules → wrong card disable logic.

**Likelihood** | **Medium**  
**Severity** | **Medium**

**Affected Runtime Paths** | P1

---

### R28 — Railway platform vendor lock-in

| Field | Value |
|-------|-------|
| **Risk ID** | R28 |
| **Title** | Deploy and restart semantics tied to Railway |
| **Status** | CONFIRMED |
| **Category** | Operations |

**Description**  
`os._exit(1)` comment assumes Railway restart; Nixpacks build.

**Evidence**  
`scheduler.py`; `railway.toml`; `decisions.md` E1.

**Impact**  
Migration requires redeploy/config rewrite.

**Likelihood** | **N/A**  
**Severity** | **Low**

---

### R29 — `Procfile` misleading deploy config

| Field | Value |
|-------|-------|
| **Risk ID** | R29 |
| **Title** | Procfile only postinstall — not process start |
| **Status** | STALE_RISK |
| **Category** | Documentation, Operations |

**Description**  
May confuse Heroku-style deployments.

**Evidence**  
`Procfile`; `architecture_map.md` U11.

**Severity** | **Low**

---

### R30 — Manual rules edit without version gate

| Field | Value |
|-------|-------|
| **Risk ID** | R30 |
| **Title** | Direct Excel edit of `rules.xlsx` bypasses bot atomicity |
| **Status** | CONFIRMED |
| **Category** | Rules, Operations |

**Description**  
Operational reality given DOC1 not implemented.

**Evidence**  
No upload handler; Dropbox as SoT; R20.

**Impact** | Broken sheet → guard deny or fatal analyzer stop |  
**Severity** | **High**

**Related** | R03, R18, R20

---

### R31 — Scheduled job errors only logged

| Field | Value |
|-------|-------|
| **Risk ID** | R31 |
| **Title** | Background cycle exception does not alert operators |
| **Status** | CONFIRMED |
| **Category** | Scheduler, Operations |

**Description**  
`run_every_minutes` catches Exception, logs, continues — no Telegram escalation.

**Evidence**  
`scheduler.py`; `decisions.md` E16.

**Impact**  
Repeated silent failure until someone reads logs.

**Likelihood** | **Medium**  
**Severity** | **Medium**

---

### R32 — Git-tracked secrets in `how --name-only` artifact

| Field | Value |
|-------|-------|
| **Risk ID** | R32 |
| **Title** | Stray file `how --name-only --oneline HEAD` in git status |
| **Status** | STALE_RISK |
| **Category** | Security, Operations |

**Description**  
Unusual untracked/staged filename from accidental command redirect — hygiene risk.

**Evidence** | Git status snapshot in conversation context |  
**Severity** | **Low**

---

## 3. Critical Risks

Dedicated view of minimum set **R01–R10** plus cross-links.

| ID | Title | Severity | Status | One-line impact |
|----|-------|----------|--------|-----------------|
| **R01** | Split Telegram tokens | Critical | STALE_RISK | Bot incoherence or boot failure |
| **R02** | `downloader.py` binding unknown | High | UNKNOWN | Antares automation may be off |
| **R03** | `meta.version` not confirmed | Medium | UNKNOWN | Wrong rules version may run |
| **R04** | `.env` secrets exposure | Critical | STALE_RISK | Credential compromise |
| **R05** | Docs/runtime divergence | Medium | STALE_RISK | Wrong ops decisions |
| **R06** | Scheduler SPOF | Critical | CONFIRMED | Total outage on process death |
| **R07** | Dropbox + rules dependency | Critical | CONFIRMED | Control plane failure / stale rules |
| **R08** | Stale pycache artifacts | Low | STALE_RISK | Unknown code surface |
| **R09** | Missing test sources | Medium | STALE_RISK | Undetected regressions |
| **R10** | Telegram operational dependency | High | CONFIRMED | No reports/control if TG down |

**Critical cluster for incident response:** R01 + R06 + R07 + R04 → verify env, container alive, Dropbox/rules reachable, rotate secrets if leak.

---

## 4. Architecture Risks

| Theme | Risk IDs | Summary | Severity |
|-------|----------|---------|----------|
| **Single container strategy** | R06, R16, R28 | One process = SPOF; ephemeral `/tmp`; Railway lock-in | Critical / Medium |
| **Restart via `os._exit(1)`** | R11, R06 | Hard kill 6×/day MSK; daily job outside job counter | Medium |
| **Excel rules engine** | R03, R07, R14, R18, R23, R30 | External Excel + cache + dual exclude; validation failures | Critical / High |
| **Dropbox dependency** | R07, R13, R27 | Files + rules + special_cards; no retry | Critical / Medium |
| **In-memory state** | R15, R16, C-07 | `last_card_path`, dedup state, auth cookies on disk only | Medium |

**Architectural strength (CONFIRMED):** clear separation Raccoon scheduled vs manual Antares; fail-closed ACL; content-hash hourly dedup.

**Architectural weakness (CONFIRMED):** monolith scheduler; no events; no health endpoint (R26).

---

## 5. Documentation Risks

| Doc ID | Title | Risk IDs | Status | Runtime truth |
|--------|-------|----------|--------|---------------|
| **DOC1** | Bot-only rules editing | R20, R30 | DOCS_ONLY | Manual Dropbox + `/reload_rules` only |
| **DOC2** | `meta.version` job refusal | R03 | DOCS_ONLY | Not confirmed in code |
| **DOC3** | Execution plane does not interpret rules | R21, R23 | DOCS_ONLY / STALE | Analyzers read `rules.xlsx` |
| **DOC4** | Scheduler does not auto-run jobs | R24, R05 | STALE_RISK | Three daemon threads + restart |
| *(extra)* | `ARCHITECTURE.md` roles.yaml path | R05, D4 | STALE_RISK | ACL in `rules.xlsx` sheets |
| *(extra)* | `RULES_EDITING.md` bot commands | R20 | DOCS_ONLY | Commands like `/limit set` not in `tg_commands` |
| *(extra)* | Observability events / jsonl | R21 | DORMANT | `core/events.py` empty |

**Mitigation class:** Documentation-only fixes — update `PROJECT_REFERENCE`, `ARCHITECTURE.md`, `CONTRACT_BOT.md` to point at `project_memory/*` or mark deprecated sections.

---

## 6. Testing Risks

| Risk ID | Issue | Status | Impact |
|---------|-------|--------|--------|
| **R09** | No `.py` test sources in workspace | STALE_RISK | No automated regression gate |
| **R08** | Pycache hints at removed tests | STALE_RISK | False confidence |
| — | No CI reference in KB scan | UNKNOWN | Drift to prod |
| — | Critical paths untested: scheduler threads, rules_provider fail-safe, access_guard, hourly fingerprint | CONFIRMED gap | High change risk |
| — | Playwright e2e absent | CONFIRMED | UI breaks found in prod only |
| — | `automation/tests` indexed but missing on disk | UNKNOWN | — |

**Inferred past tests (from `.pyc` names only — not runnable):**  
`test_validate_rules_xlsx`, `test_wallet_time_logic`, `test_normalization_datetime`, `test_datetime_utils` — **STALE_RISK**.

**Tests to add later (backlog only):** rules contract, env token contract, `get_rules_snapshot` TTL/fail-safe, guard deny matrix, fingerprint dedup unit tests.

---

## 7. Operational Risks

| Platform | Risk IDs | Key failure modes |
|----------|----------|-------------------|
| **Railway** | R06, R11, R16, R28, R26 | Container down; restart; no volume; no health URL |
| **Telegram** | R01, R10, R25, R31 | API down; split tokens; import boot fail; silent job errors |
| **Dropbox** | R07, R13, R27 | API outage; no retry; rules/file stuck |
| **Playwright** | R12, R17, R19 | UI change; concurrent browsers; partial Raccoon export |
| **Scheduler** | R02, R11, R17, R31 | Missing Antares job; hard exit; overlap; log-only errors |

**Operational dependency chain (CONFIRMED):**  
Railway → scheduler → (Telegram polling ∥ Raccoon Playwright → `/tmp` → Telegram send) all require Dropbox for rules at startup.

---

## 8. Prioritized Risk Backlog

> **Investigation backlog only** — no fixes in Stage A5.

### Priority P0

| Goal | Risk reduced | Complexity |
|------|--------------|------------|
| Audit Railway: `TG_BOT_TOKEN` + `TELEGRAM_BOT_TOKEN` same bot; smoke send + `/status` | R01, R25 | **Low** |
| Confirm whether `downloader.py` runs in prod (second service/cron/manual) | R02 | **Medium** |
| Verify `.env` not in git remote; rotate if ever committed | R04 | **Medium** |
| Validate Dropbox + `rules.xlsx` reachable from prod container; failure alerting | R07 | **Low** |

### Priority P1

| Goal | Risk reduced | Complexity |
|------|--------------|------------|
| Search/implement confirmation of `meta.version` enforcement | R03 | **Medium** |
| Reconcile `CONTRACT_BOT` / `RULES_EDITING` with actual ops + handlers | R20, R30, DOC1 | **Medium** |
| Correlate restart times with missed daily/hourly reports | R11, R16 | **Low** |
| Log review: silent scheduled exceptions → need alert? | R31 | **Low** |
| Restore/locate `tests/*.py` from git; assess CI | R09 | **Medium** |

### Priority P2

| Goal | Risk reduced | Complexity |
|------|--------------|------------|
| Update `PROJECT_REFERENCE` scheduler section | R24, R05, DOC4 | **Low** (docs) |
| Document Raccoon payout disabled vs Antares wallet | R19 | **Low** (docs) |
| Investigate `automation/` pyc artifacts | R08 | **Low** |
| Clarify `special_cards.xlsx` ownership/update process | R27 | **Low** |
| Stakeholder check: Antares hourly retired? | R22 | **Low** |

### Priority P3

| Goal | Risk reduced | Complexity |
|------|--------------|------------|
| Deprecate `download_rules_xlsx` path in docs | R05, X-06 | **Low** (docs) |
| Clarify `Procfile` vs `railway.toml` | R29 | **Low** (docs) |
| Remove stray `how --name-only` file from repo | R32 | **Low** |
| Events plane: implement or remove from ARCHITECTURE | R21 | **High** (if implement) |

### Risks closable by documentation only (no code)

| Risk ID | Doc action |
|---------|------------|
| R24, DOC4 | Fix PROJECT_REFERENCE scheduler text |
| R05 (partial) | KB as canonical; mark stale doc sections |
| R19 | State Raccoon payout intentionally disabled |
| R22 | Mark Antares hourly DORMANT/retired |
| R29 | Note Procfile is postinstall-only |
| R08 (partial) | Note pyc artifacts pending cleanup investigation |

---

## Cross-reference index

| Source ID | Risk IDs |
|-----------|----------|
| architecture U1 | R01, R25 |
| architecture U2 | R02 |
| architecture U3 | R24, R05 |
| architecture U4 | R20 |
| architecture U9 | R03 |
| architecture U10 | R04 |
| contracts X-01..X-12 | R01–R32 as mapped above |
| decisions Q1 | R01 |
| decisions DOC1–4 | R20, R03, R21, R24 |

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | Stage A5: risk register created from KB + codebase |
