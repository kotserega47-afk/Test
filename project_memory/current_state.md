# Current State — analizis

| Мета | Значение |
|------|----------|
| **KB версия** | v1.5 |
| **Снимок на дату** | 2026-06-07 |
| **Среда** | repo snapshot (live prod — UNKNOWN) |

---

## Status

| Поле | Значение |
|------|----------|
| **Документ** | draft — G1 + G2 + G5 заполнены |
| **Prod runtime** | structure CONFIRMED из repo; live prod — UNKNOWN |
| **Staging / local** | UNKNOWN |

---

## Purpose

Снимок того, что **реально работает** в prod/staging/local: capabilities, subsystems, integrations, tests и operational limits. Обновляется после merge, меняющего runtime.

---

## Project Definition (G1)

| Поле | Значение | Источник | Статус |
|------|----------|----------|--------|
| **PROJECT_NAME** | analizis | `EXPERT_REVIEW.md` L1 | CONFIRMED |
| **ONE_LINE_PURPOSE** | Автоматизация загрузки данных, аналитических расчётов и доставки результатов в Telegram на основе управляемых правил | `PROJECT_REFERENCE.md` | CONFIRMED |
| **PRIMARY_ENTRYPOINT** | `scheduler.py` | `railway.toml`; `scheduler.py` L280–281 | CONFIRMED |
| **DEPLOYMENT_TARGET** | Railway | `railway.toml` | CONFIRMED |
| **DEPLOY_CONFIG_FILE** | `railway.toml`; `Procfile` | repo root | CONFIRMED |
| **RUN_COMMAND** | `/opt/venv/bin/python scheduler.py` | `railway.toml` | CONFIRMED |

---

## Confirmed Facts

- **G2** закрыт TASK-2026-05-31-02; **G5** закрыт (material) TASK-2026-05-31-03.
- Data contracts: см. `contracts.md` (env, files, Excel/YAML/JSON, data flows).
- Runtime-код в корне репозитория; KB в `project_memory/`.
- Prod process model: single long-running `scheduler.py` with Telegram polling + background schedule loop.
- Active job types in code: `wallet`, `hourly`, `rate`, `download`.
- Tests in `tests/` (pytest).
- **WalletEditor** integrated into main Telegram runtime (`scheduler.py`); status **ACTIVE**, production-ready (WE-0…WE-8 complete).
- **Conversion Modernization Program** complete — layered architecture, orchestrator, observability, passive fingerprint Phase 1A deployed in code.
- **Conversion → Wallet Editor hook** active in code — best-effort bridge from `problem_cards` after `conversion.run()`; requires `CONVERSION_WALLET_EDITOR*` env; all valid cards per run (10-card rollout limit removed).
- **Telegram routes Phase 2** — optional `rules.xlsx` sheet `telegram_routes` parsed into snapshot; ENV↔rules shadow compare on `/status` (E-TG-ROUTES-01).
- **Telegram routes Phase 3A** — `TELEGRAM_ROUTES_FROM_RULES_V2=0` (default): hourly → `TELEGRAM_CHAT_ID_HOURLY`. `=1`: hourly → `platform_hourly_report` from rules; other routes unchanged (E-TG-ROUTES-02).
- **Telegram routes Phase 3A.1** — `/status`: `shadow_mismatches` = ENV↔rules diff for **non-migrated** routes only; `migrated_route_differences` = informational legacy ENV drift for routes already on Rules V2 runtime (e.g. `platform_hourly_report` when flag `1`).
- **Telegram routes Phase 3B** — `platform_wallet_download_report` in `MIGRATED_RUNTIME_ROUTES`; `downloader_wallets.run_wallet_cycle` uses `send_message_to_route` when `TELEGRAM_ROUTES_FROM_RULES_V2=1` (E-TG-ROUTES-03).
- **Job lock stale recovery** — persistent `{STATE_DIR}/locks/*.lock` cleared when PID dead, ghost PID-1 after redeploy (lock pid == process but job not in `_RUNNING`), or lock age > `JOB_LOCK_STALE_SEC` (default 600).
- **Telegram routes Phase 3C** — `conversion_wallet_editor` in `MIGRATED_RUNTIME_ROUTES`; `conversion_wallet_editor_bridge` notifications via `send_message_to_route` when `TELEGRAM_ROUTES_FROM_RULES_V2=1` (E-TG-ROUTES-04). `WalletEditorTask.chat_id` still from resolved route/config (not `access_rules`).
- **Telegram routes Phase 3D** — `bakai_rate_current` / `bakai_rate_alert` in `MIGRATED_RUNTIME_ROUTES`; `bakai_monitor_playwright` uses route helpers when flag `1` (E-TG-ROUTES-05).
- **Wallet hang Patch A** — `downloader_wallets.py`: explicit Playwright timeouts (`GOTO`/`networkidle`/`calendar` 60s/15s); stage logs + `record_progress("wallet", …)` (E-OPS-02).
- **Wallet hang Patch B** — `schedule_loop` uses `dispatch_job_background`; scheduler `tick_age` independent of long jobs; TG/manual dispatch still blocking (E-OPS-01).
- **Job Health Guard C1** — observe-only: `JOB_HEALTH_GUARD_ENABLED=1` → `/status` `job_health:` with `state`/`stage`/`progress_age`; recovery **off**; enable on Railway for ops (E-OPS-03).
- **WalletEditor Auto-Enable** — Phase A plan/dry-run; Phase B1 Antares execution; Phase B1.1 `max_rows_per_run`; Phase B2 registry patch (`Включено` / `Комментарий включения`); TG `/auto_enable_plan` (plan-only) + `/auto_enable_run` (fresh plan + execute); settings via Rules `job_params` `wallet_editor_auto_enable` (E-WE-11…E-WE-13).
- **WalletEditor HOLD enforcement** — `hold` sheet blocks `add_partner` before Antares (manual SKIP; auto-enable SKIP); fail-closed on hold-list read failure (manual SKIP / auto-enable FAIL); shared helper `integrations/wallet_editor_hold.py` (E-WE-14).
- **WalletEditor registry UX-A** — new `all_results` / `runs` rows copy style from template data row; patch updates values without losing border/alignment (E-WE-09 extended).
- **WalletEditor registry `add_partner`** — `partner=value` written to `all_results` on append (E-WE-15).
- **Telegram bot token sanitization** — token redaction in error paths/logs/alerts; no raw `/bot<TOKEN>/` URLs in app output (E-SEC-01).
- **Wallet downloader datepicker fix** — payout calendar navigates to target month/year; selects by `data-date=YYYY-MM-DD` (E-OPS-04).

---

## Raccoon Wallet Configuration

| Aspect | State |
|--------|-------|
| **Status** | `RULES_V2_ONLY` |
| **Source of truth** | `rules.xlsx` |
| **Epic** | CONFIG-MIGRATION-RACCOON-WALLET — **COMPLETE** |

**Removed:**

- Legacy YAML configuration file
- YAML loader path
- YAML runtime fallback
- YAML shadow compare
- Feature flag env (retired)

**Current runtime sources:**

| Config area | Rules V2 sheet / source |
|-------------|-------------------------|
| Scalars (`window_minutes`, `offset_minutes`, `min_events`, `pending_payin_minutes`, `payin_days_back`) | `job_params` |
| Partner roster | `thresholds_partner` ∪ `wallet_limits` ∪ `partner_groups` |
| Group membership | `partner_groups` |
| PayIn column mapping | code constants (`raccoon_wallet_columns.py`) |
| Thresholds / limits / exclude overlay | `thresholds_partner`, `wallet_limits`, `exclude_time` |

**Runtime path:** `resolve_raccoon_wallet_config()` → Rules V2 only; logs `[raccoon_wallet_config] source=rules_v2`.

---

## Payout Rules V2

| Aspect | State |
|--------|-------|
| **Status** | `PROD_MODE_1` |
| **Current source** | Rules V2 |
| **Rollback** | `PAYOUT_CONFIG_FROM_RULES_V2=0` |

**Rules V2 sheets:** `payout_info_rules` (10 error phrases + thresholds), `payout_ignore_phrases` (5 ignore phrases).

**Runtime path:** `resolve_payout_config()` with `PAYOUT_CONFIG_FROM_RULES_V2=1` → logs `[payout_config] source=rules_v2`; YAML retained as fallback only.

**Production cutover (2026-06-02):** prod `rules.xlsx` deployed to Dropbox; Railway env enabled; payout report verified; rollback not required.

**YAML:** `config/payout_config.yaml` retained — removal gated on CONFIG-MIGRATION-PHASE-4D observation period.

---

## Conversion modernization (completed)

| Phase | Deliverable | Status |
|-------|-------------|--------|
| Characterization | Golden tests for `conversion.run` behavior | **DONE** |
| DTO extraction | `analyzers/conversion_dto.py` | **DONE** |
| Analyzer extraction | `analyzers/conversion_analyzer.py` | **DONE** |
| Reporter extraction | `reporters/conversion_reporter.py` | **DONE** |
| RulesAccessor extraction | `ConversionRulesAccessor` in `core/rules_v2/accessors.py` | **DONE** |
| Original Partner Name | Column preserved in conversion output | **DONE** |
| Explicit routing | Conversion + payout routing in `selector.py` code constants; `analysis_map.yaml` removed (E-CONFIG-03) | **DONE** |
| Orchestrator B1 | `integrations/conversion_pipeline.py` | **DONE** |
| Orchestrator B2 | Downloader wired to `run_conversion_pipeline` | **DONE** |
| Orchestrator B3 | `main.process_file` delegates conversion to pipeline | **DONE** |
| Observability Phase 1 | `conversion_*` events + `jobs.conversion` state + `/status` block | **DONE** |
| Observability Phase 2 | Downloader tiered final TG; conversion failure no longer masked | **DONE** |
| Fingerprint Phase 1A | Passive fingerprint (compute/compare/store; no dedup skip) | **DONE** |

**Production conversion path:**

```
downloader → run_conversion_pipeline → conversion.run
main.process_file(conversion) → run_conversion_pipeline → conversion.run
```

**Next (not started):** Conversion Phase 1B — real dedup skip on fingerprint match (`CONV-OPTIMIZATION-PHASE-1B`). Prerequisite: observe passive fingerprint stability in production.

---

## Unknowns

| ID / поле | Описание |
|-----------|----------|
| Live prod | Railway service health, uptime |
| Staging | Отдельная среда не подтверждена |
| Schedule config in prod | Enabled rows in prod `rules.xlsx` |
| Integration prod status | Works in prod? для каждой I# — не верифицировано |

---

## Open Questions

- Подтверждён ли live prod deploy?
- Какие schedules enabled в prod rules?
- Есть ли staging?

---

## Конфигурация deploy

| Поле | Значение | Статус |
|------|----------|--------|
| **Deploy target** | Railway | CONFIRMED |
| **Entry command** | `/opt/venv/bin/python scheduler.py` → `scheduler.py` | CONFIRMED |
| **Config files** | `railway.toml`; `Procfile` (postinstall) | CONFIRMED |
| **Deploy service name** | `file-analyzer` | CONFIRMED |

---

## Capabilities (R#)

| ID | Capability | Active in code | Active in prod? | Статус KB |
|----|------------|----------------|-----------------|-----------|
| R1 | Hourly analytics | да (`hourly` job) | UNKNOWN | CONFIRMED |
| R2 | Wallet analytics | да (`wallet` job) | UNKNOWN | CONFIRMED |
| R3 | Download + conversion/payout | да (`download` job) | UNKNOWN | CONFIRMED |
| R4 | Bakai rate monitor | да (`rate` job) | UNKNOWN | CONFIRMED |
| R5 | Telegram control plane | да (`scheduler` + commands) | UNKNOWN | CONFIRMED |
| R6 | WalletEditor (Excel ingest → Antares editing) | да (P-WE pipeline) | UNKNOWN | CONFIRMED |

Подробности: `architecture_map.md` § Pipelines.

---

## Subsystems / modules (S#)

| ID | Subsystem | Runtime status | Статус KB |
|----|-----------|----------------|-----------|
| S1 | Control plane (rules, access, schedules) | active | CONFIRMED |
| S2 | Job runner + locks | active | CONFIRMED |
| S3 | Playwright downloaders (Antares, Bakai) | active | CONFIRMED |
| S4 | Analyzers + reporters | active | CONFIRMED |
| S5 | Dropbox IO | active | CONFIRMED |
| S6 | State + event log | active | CONFIRMED |
| S7 | `analyzers/transactions.py` | dormant | CONFIRMED |
| S8 | WalletEditor (`automation/*`, `wallet_editor_tg`) | **active** | CONFIRMED |
| S9 | Conversion pipeline (`conversion_pipeline`, fingerprint, layered analyzer/reporter) | **active** | CONFIRMED |

### Conversion (S9) — operational snapshot

| Aspect | State |
|--------|-------|
| **Status** | ACTIVE — modernization complete; Phase 1A passive fingerprint in code |
| **Orchestrator** | `integrations/conversion_pipeline.py` — single lifecycle source of truth |
| **Analyzer stack** | `conversion.py` (facade) → `ConversionAnalyzer` + `ConversionReporter` + DTO |
| **Observability** | `event_log` (`conversion_*`, `conversion_fingerprint_computed`) + `state_store` (`jobs.conversion`) + **Phase 1B observation JSONL** (`observability/conversion_fp_observation.py`) |
| **Fingerprint** | Passive only — `CONVERSION_FINGERPRINT_ENABLED` (default on); no skip yet |
| **Phase 1B Observation** | **IMPLEMENTED** — diagnostic JSONL; flag `CONVERSION_FP_OBSERVATION_ENABLED` (default off); 14-day retention |
| **Phase 1B Dedup Skip** | **NOT STARTED** — real dedup skip when fingerprint matches |
| **Conversion → Wallet Editor** | **IMPLEMENTED** — `integrations/conversion_wallet_editor_bridge.py`; env-gated; all valid cards/run; best-effort |

### WalletEditor (S8) — operational snapshot

| Aspect | State |
|--------|-------|
| **Status** | ACTIVE — integrated, production-ready |
| **Architecture** | One PTB polling loop via `scheduler.py` |
| **Ingest** | Telegram `.xlsx` → `integrations/wallet_editor_tg.py` |
| **Access control** | Dedicated allowlist `WALLET_EDITOR_ALLOWED_CHAT_IDS` (fail-closed) |
| **Credentials** | Per-operator via `WALLET_EDITOR_OPERATOR_MAP` + `WALLET_EDITOR_OPERATOR_<PROFILE>_*` |
| **Execution** | Per-profile queue + daemon worker (`automation/worker.py`) |
| **Cumulative registry** | Dropbox `DROPBOX_WALLET_EDITOR_PATH`; lifecycle + format-safe openpyxl write (UX-A row style copy); async append **after** TG result; `job_params` timeout/warning/retry (`registry_*_seconds`); staged result copy (E-WE-08…E-WE-10, UX-A) |
| **Auto-enable** | Rules `job_params` `wallet_editor_auto_enable`; eligibility from recalculated registry; plan `/auto_enable_plan`; execute `/auto_enable_run`; B2 patches `Включено`/`Комментарий включения`; HOLD check before `open_card` |
| **HOLD enforcement** | `integrations/wallet_editor_hold.py`; manual engine pre-pass; auto-enable executor batch check; fail-closed |
| **Auth state** | Per-profile `/tmp/auth_state_wallet_editor_<PROFILE>.json` |
| **Telegram outbound health** | `integrations/telegram_bot.py` — enqueue vs delivery counters, periodic health log via `scheduler.schedule_loop` |
| **Legacy** | `automation/main.py`, `automation/tg_receiver.py` — not production |

---

## Integrations (operational)

| Integration | In active runtime | Works in prod? | Notes |
|-------------|-------------------|----------------|-------|
| Telegram | да | UNKNOWN | polling + send |
| Antares (Playwright) | да | UNKNOWN | 3 download modules |
| Dropbox | да | UNKNOWN | rules + file pipeline |
| Bakai (Playwright) | да | UNKNOWN | rate job only |
| rules.xlsx | да | UNKNOWN | `RULES_XLSX_PATH` |
| WalletEditor / Antares (Playwright) | да (separate operator credentials) | UNKNOWN | P-WE; not shared with download jobs |
| Raccoon | нет (DOCS_ONLY) | no | legacy doc only |

---

## Tests (что существует)

| Тип | Где | Покрывает |
|-----|-----|-----------|
| Unit / integration | `tests/` | rules_v2, analyzers, scheduler health, lock status, hourly/wallet render, **WalletEditor** (`test_wallet_editor_*`, `test_wallet_editor_dropbox_registry`), **conversion** (`test_conversion_*`, characterization, observability, fingerprint, **fp observation** `test_conversion_fp_observation`) |
| CLI tools | `tools/validate_rules_xlsx.py` | rules validation (DEV_ONLY) |
| Manual scripts | `scripts/test_wallet_pipeline.py`, `scripts/test_bridge_legacy.py` | DEV_ONLY |

---

## Known operational limits

| Limit | Value / policy | Источник |
|-------|----------------|----------|
| Job PID lock | `{STATE_DIR}/locks/{job_type}.lock` | `core/job_runner.py` |
| Dropbox pipeline lock stale | 600 sec | `run_once_guard.py` L10 |
| Rate monitor window | 08:00–23:55 MSK | `bakai_monitor_playwright.py` L63–66 |
| Rate monitor retries | 3 attempts, delays 5/15/30s | `bakai_monitor_playwright.py` L171–173 |
| Scheduler tick | ~5 sec | `scheduler.py` L255 |
| Job error in scheduler | log.exception, loop continues | `scheduler.py` L218–220 |

---

## DOCS_ONLY in prod

| Item | Почему не в prod |
|------|------------------|
| Raccoon data source | Нет в `.py`; только `EXPERT_REVIEW.md` |
| `analyzers/transactions.py` | DORMANT — stale import of removed `analysis_map.yaml` |
| `automation/main.py`, `automation/tg_receiver.py` | Legacy WalletEditor standalone path |

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-31 | Initial snapshot |
| 2026-05-31 | G1 — TASK-2026-05-31-01 |
| 2026-05-31 | G2 — TASK-2026-05-31-02 |
| 2026-05-31 | G5 — TASK-2026-05-31-03 |
| 2026-06-01 | WalletEditor WE-0…WE-6 integrated — S8 ACTIVE |
| 2026-06-03 | WalletEditor Dropbox cumulative registry (WE-7 / E-WE-07) |
| 2026-06-03 | Registry lifecycle hold/Отлёжка/re-enable (WE-8 / E-WE-08) |
| 2026-06-03 | Registry format-safe openpyxl + Dropbox rev protection (E-WE-09) |
| 2026-06-03 | Registry async append + job_params timeout (E-WE-10) |
| 2026-06-03 | Conversion → Wallet Editor: 10-card rollout limit removed (CONV-WE-LIMIT-REMOVAL) |
| 2026-06-01 | Telegram outbound sender health (Option B) |
| 2026-06-02 | Conversion Modernization + Observability + Fingerprint Phase 1A — S9 ACTIVE; Phase 1B Observation Layer implemented (dedup skip NOT STARTED) |
| 2026-06-02 | STATE_DIR Volume Migration Phase A — default `/data/state` (E-INFRA-01) |
| 2026-06-02 | Conversion → Wallet Editor hook — `conversion_wallet_editor_bridge.py`; best-effort; max 10 cards/run |
| 2026-06-02 | Raccoon Wallet Rules V2 — production cutover (`RACCOON_WALLET_CONFIG_FROM_RULES_V2=1`); status `PROD_OBSERVATION`; Phase 3B-6 waiting for prod observation |
| 2026-06-02 | Raccoon Wallet config migration complete — YAML removed; Rules V2 only (E-CONFIG-10) |
| 2026-06-02 | Raccoon Wallet feature flag removed — E-CONFIG-11; CONFIG-MIGRATION-PHASE-3B-7 complete |
| 2026-06-02 | CONFIG-MIGRATION-RACCOON-WALLET epic closed — E-CONFIG-12; Rules V2 sole config source |
| 2026-06-02 | Payout Rules V2 production cutover — `PAYOUT_CONFIG_FROM_RULES_V2=1`; status `PROD_MODE_1` (E-CONFIG-13) |
| 2026-06-07 | WalletEditor Auto-Enable Phase A/B1/B1.1/B2 + split TG commands — E-WE-11…E-WE-13 |
| 2026-06-07 | WalletEditor HOLD enforcement for `add_partner` — E-WE-14 |
| 2026-06-07 | WalletEditor registry UX-A row formatting preservation — E-WE-09 extended |
| 2026-06-07 | WalletEditor `add_partner` → registry `partner` mapping — E-WE-15 |
| 2026-06-07 | Telegram bot token sanitization in error paths — E-SEC-01 |
| 2026-06-07 | Wallet downloader payout datepicker navigation fix — E-OPS-04 |
