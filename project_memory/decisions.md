# Decisions — analizis

| Мета | Значение |
|------|----------|
| **KB версия** | v1.5 |
| **Последнее обновление** | 2026-06-15 |

---

## Status

| Поле | Значение |
|------|----------|
| **Документ** | draft — G1 + G2 + G5 data invariants |
| **Explicit decisions (E#)** | E1, E2, E4, E7, E9, **E-WE-01…E-WE-15**, **E-SEC-01**, **E-CONV-01…E-CONV-08**, **E-CONFIG-01…E-CONFIG-15**, **E-OPS-01…E-OPS-05**, **E-HOURLY-01** — CONFIRMED; E3, E5, E6, E8 — UNKNOWN |
| **Implicit invariants (I#)** | I1–I11 — см. таблицы |

---

## Purpose

Реестр архитектурных решений и инвариантов.

- **E_** — explicit decisions (архитектура, prod).
- **I_** — implicit invariants (из кода/ops, зафиксированные).

---

## Project Definition (G1)

| Поле | Значение | Статус |
|------|----------|--------|
| **PROJECT_NAME** | analizis | CONFIRMED |
| **PRIMARY_ENTRYPOINT** | `scheduler.py` | CONFIRMED |
| **DEPLOYMENT_TARGET** | Railway | CONFIRMED |
| **RUN_COMMAND** | `/opt/venv/bin/python scheduler.py` | CONFIRMED |

---

## Confirmed Facts

- **G2** runtime invariants — TASK-2026-05-31-02.
- **G5** data invariants — TASK-2026-05-31-03.
- Job dispatch централизован через `request_job()` + `JOB_REGISTRY`.
- Два независимых lock-механизма: job PID locks и dropbox pipeline lock.
- `rules.xlsx` REQUIRED_SHEETS: `meta`, `exclude_time`, `access`, `commands` — `contract_schema.py`.

---

## Unknowns

| ID | Описание |
|----|----------|
| E3 | Прочие архитектурные решения |
| E5 | Notification channel invariants (explicit target_id policy) |
| E6 | Config fail-safe on rules sync error — поведение требует верификации |
| E8 | Railway process restart policy |
| I1 | Max job lock hold duration — не задан явно в job_runner |

---

## Open Questions

- Fail-safe rules: last known good при ошибке sync?
- Restart policy на Railway при crash?
- Нужен ли formal E5 для всех TG send paths?

---

## Explicit decisions (E#)

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E1 | 2026-05-31 | Prod entry = `scheduler.py` | CONFIRMED | `railway.toml` L7; `scheduler.py` L280–281 |
| E2 | 2026-05-31 | Deploy on Railway | CONFIRMED | `railway.toml` |
| E3 | | `<DECISION_TEXT>` | UNKNOWN | |
| E4 | 2026-05-31 | Job single-flight lock at `{STATE_DIR}/locks/{job_type}.lock` (PID-based) | CONFIRMED | `core/job_runner.py` L57–109 |
| E5 | | Notification channels require explicit `chat_id` | UNKNOWN | partial: `transport/telegram_transport.send_text` requires `chat_id` |
| E6 | | Config fail-safe: last known good on sync error | UNKNOWN | |
| E7 | 2026-05-31 | Scheduler loop: job errors logged, process continues (not crash) | CONFIRMED | `scheduler.py` L218–220, L247–248 |
| E8 | | Process restart policy on Railway | UNKNOWN | |
| E9 | 2026-05-31 | Dropbox analyze pipeline lock at `/tmp/dropbox_pipeline.lock`, stale 600s | CONFIRMED | `run_once_guard.py` L6, L10 |

### WalletEditor decisions (E-WE-*)

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E-WE-01 | 2026-06-01 | Единый Telegram polling (`scheduler.py` + PTB); **нет** второго raw `getUpdates` в production | CONFIRMED | `scheduler.py`; `integrations/tg_commands.py`; legacy = `automation/tg_receiver.py` |
| E-WE-02 | 2026-06-01 | Отдельный allowlist `WALLET_EDITOR_ALLOWED_CHAT_IDS`; fail-closed; не использовать `TELEGRAM_ALLOWED_CHAT_IDS` | CONFIRMED | `integrations/wallet_editor_tg.py` |
| E-WE-03 | 2026-06-01 | Отдельные Antares credentials для WalletEditor; no fallback на shared `ANTARES_*` в production handler | CONFIRMED | `automation/runtime.py`; WE-3/WE-5 |
| E-WE-04 | 2026-06-01 | Routing по `telegram_user_id` → `operator_profile` через `WALLET_EDITOR_OPERATOR_MAP` | CONFIRMED | `integrations/wallet_editor_tg.py`; `automation/runtime.py` |
| E-WE-05 | 2026-06-01 | Per-operator queue + daemon worker: параллельно между профилями, sequentially внутри профиля | CONFIRMED | `automation/worker.py` |
| E-WE-06 | 2026-06-01 | Отдельный Playwright auth-state на профиль: `/tmp/auth_state_wallet_editor_<PROFILE>.json` | CONFIRMED | `automation/runtime.py`; worker passes per-task `auth_state_path` |
| E-WE-07 | 2026-06-03 | Cumulative WE results in Dropbox (`DROPBOX_WALLET_EDITOR_PATH`); sheets `all_results` + `runs`; append after successful `engine.run`, before TG send; best-effort; idempotent by `run_id`; in-process lock | CONFIRMED | `integrations/wallet_editor_registry.py`; `automation/worker.py` |
| E-WE-08 | 2026-06-03 | Registry lifecycle: sheets `hold`, `Отлёжка`; recalc all `all_results` on append; `Дата включения` / `Статус включения`; missing-Отлёжка TG warning once per partner; future auto-enable columns reserved (`Включено`, `Комментарий включения`) | CONFIRMED | `wallet_editor_registry_lifecycle.py` |
| E-WE-09 | 2026-06-03 | Registry format-safe write: openpyxl in-place updates (`wallet_editor_registry_xlsx.py`); preserve workbook formatting (column widths, freeze panes, header styles); **UX-A (2026-06-07):** new data rows copy template row styles; patch value-only updates preserve borders/alignment; `hold`/`Отлёжка` read-only if present; `value` removed from `all_results`; `card` as text; Dropbox rev conflict skips upload + TG warning | CONFIRMED | `wallet_editor_registry_xlsx.py`; `dropbox_watcher.upload_file_if_rev` |
| E-WE-10 | 2026-06-03 | Registry append async after Telegram result; timeout/warning/retry from Rules `job_params` (`registry_*_seconds`); staged result copy avoids cleanup race | CONFIRMED | `wallet_editor_registry_settings.py`; `wallet_editor_registry_async.py`; `automation/worker.py` |
| E-WE-11 | 2026-06-07 | WalletEditor Auto-Enable Phase A: plan-only from recalculated registry; eligibility + dedup + batch split + timeout estimate; TG report via `telegram_route_report`; no Antares in plan mode | CONFIRMED | `integrations/wallet_editor_auto_enable.py`; `wallet_editor_auto_enable_eligibility.py` |
| E-WE-12 | 2026-06-07 | Auto-Enable Phase B1/B1.1: Antares execution via dedicated worker batch queue; sequential `open_card` per candidate; settings `working_statuses`, `auto_return_statuses`, `auto_return_target_status`, `max_rows_per_batch`, `max_rows_per_run` | CONFIRMED | `wallet_editor_auto_enable_executor.py`; `automation/worker.py` |
| E-WE-13 | 2026-06-07 | Auto-Enable Phase B2: after batch execution patch registry `Включено` + `Комментарий включения`; lifecycle recalc maps OK→ВКЛЮЧЕНО, SKIP→ПРОПУЩЕНО, FAIL→ОШИБКА; `/auto_enable_plan` plan-only; `/auto_enable_run` fresh plan + execute (manual ignores `approval_required`) | CONFIRMED | `wallet_editor_auto_enable.py`; `wallet_editor_registry.py` `patch_enable_results_in_dropbox_registry` |
| E-WE-14 | 2026-06-07 | HOLD sheet enforces runtime block on `add_partner` before Antares: manual → SKIP; auto-enable → SKIP; hold-list unavailable → manual add_partner SKIP (fail-closed) / auto-enable FAIL; shared `wallet_editor_hold.py`; `remove_partner`/`set_status` unaffected | CONFIRMED | `automation/engine.py`; `wallet_editor_auto_enable_executor.py` |
| E-WE-15 | 2026-06-07 | Registry append maps `partner` from `value` for both `remove_partner` and `add_partner` rows (`partner_from_row`) | CONFIRMED | `wallet_editor_registry_lifecycle.py` |

### Conversion decisions (E-CONV-*)

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E-CONV-01 | 2026-06-02 | Conversion routing no longer depends on `analysis_map.yaml`; explicit routing in `selector.py` / pipeline | CONFIRMED | `analyzers/selector.py`; conversion YAML section deprecated |
| E-CONV-02 | 2026-06-02 | `run_conversion_pipeline` is the single source of truth for conversion lifecycle (download, fingerprint, run, move, observability) | CONFIRMED | `integrations/conversion_pipeline.py`; `downloader.py`; `main.py` delegation |
| E-CONV-03 | 2026-06-02 | Conversion observability via `event_log` + `state_store` (`jobs.conversion`); `/status` Conversion block | CONFIRMED | `conversion_pipeline.py`; `integrations/tg_commands.py` |
| E-CONV-04 | 2026-06-02 | Passive fingerprint rollout before real dedup skip — Phase 1A compute/compare/store only; Phase 1B skip gated separately | CONFIRMED | `integrations/conversion_fingerprint.py`; `CONVERSION_FINGERPRINT_ENABLED` |
| E-CONV-05 | 2026-06-02 | Phase 1B diagnostic observation in separate JSONL (`{STATE_DIR}/observability/conversion_fp_observation_*.jsonl`); flag `CONVERSION_FP_OBSERVATION_ENABLED` (default off); 14-day retention; best-effort; does not affect pipeline outcome | CONFIRMED | `observability/conversion_fp_observation.py`; hook in `conversion_pipeline.py` |
| E-CONV-06 | 2026-06-02 | Conversion → Wallet Editor hook is **best-effort** — errors logged/TG only; never fails conversion pipeline | CONFIRMED | `integrations/conversion_wallet_editor_bridge.py`; `analyzers/conversion.py` |
| E-CONV-07 | 2026-06-02 | Conversion → Wallet Editor: all valid `problem_cards` forwarded; invalid rows filtered; no per-run cap (10-card rollout removed 2026-06-03) | CONFIRMED | `integrations/conversion_wallet_editor_bridge.py` |
| E-CONV-08 | 2026-06-02 | Scheduled conversion WE credentials via direct env (`CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_LOGIN/PASSWORD`); profile `CONVERSION_AUTO`; no `WALLET_EDITOR_OPERATOR_MAP` | CONFIRMED | `integrations/conversion_wallet_editor_bridge.py` |
| E-INFRA-01 | 2026-06-02 | `STATE_DIR` default `/data/state` (Railway Volume); local persistent state (locks, events, observation JSONL) — no `/config/state` default | CONFIRMED | `job_runner.py`, `event_log.py`, `lock_status.py`, `conversion_fp_observation.py` |

### Config migration decisions (E-CONFIG-*)

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E-CONFIG-01 | 2026-06-02 | Config → Rules V2 migration uses shadow-first rollout: default `PAYOUT_CONFIG_FROM_RULES_V2=0` keeps YAML as source of truth; Rules V2 loaded in shadow and compared on each payout run; runtime switch `=1` promotes Rules V2 with YAML fallback | CONFIRMED | `analyzers/payout_config_loader.py`; `core/rules_v2/payout_rules_accessor.py` |
| E-CONFIG-02 | 2026-06-02 | **`rules.xlsx` — конечная точка миграции.** Во время CONFIG-MIGRATION-PHASE-1…N разрешены только изменения кода: schema, accessors, shadow compare, feature flags. **Production `rules.xlsx` не изменяется** до завершения всех этапов миграции и явного подтверждения готовности к переключению runtime на Rules V2 | CONFIRMED | `project_memory/decisions.md`; CONFIG-MIGRATION program |
| E-CONFIG-03 | 2026-06-02 | Analyzer routing moved from `analysis_map.yaml` to explicit code constants in `analyzers/selector.py` (`CONVERSION_FILE_PATTERN`, `PAYOUT_FILE_PATTERN`); `config/analysis_map.yaml` removed — routing **не** переносится в Rules V2 | CONFIRMED | `analyzers/selector.py`; CONFIG-MIGRATION-PHASE-2 |
| E-CONFIG-04 | 2026-06-02 | Raccoon wallet config migration Phase 3A: only scalar params (`window_minutes`, `offset_minutes`, `min_events`, `pending_payin_minutes`, `payin_days_back`) move to `job_params` with shadow-first env `RACCOON_WALLET_CONFIG_FROM_RULES_V2`; partner roster, group membership, column mapping remain YAML until Phase 3B | CONFIRMED | `analyzers/raccoon_wallet_config_loader.py`; CONFIG-MIGRATION-PHASE-3A |
| E-CONFIG-05 | 2026-06-02 | Phase 3B-1: partner roster shadow only — Rules V2 roster union compared to YAML on every resolve; **does not switch runtime roster**; non-empty `yaml_only` roster mismatch blocks final YAML removal | CONFIRMED | `core/rules_v2/raccoon_wallet_rules_accessor.py`; CONFIG-MIGRATION-PHASE-3B-1 |
| E-CONFIG-06 | 2026-06-02 | Phase 3B-2: Raccoon PayIn `columns.payin.*` moved to code constants (`RACCOON_PAYIN_COLUMN_MAP`); no Rules V2 sheet for static export headers; YAML columns retained for shadow compare only; runtime always uses constants | CONFIRMED | `analyzers/raccoon_wallet_columns.py`; CONFIG-MIGRATION-PHASE-3B-2 |
| E-CONFIG-07 | 2026-06-02 | Phase 3B-3: groups membership shadow (YAML vs `partner_groups`); dead YAML fields removed (`success_window_minutes`, `api_cancel_keyword`, payout dead paths); API-cancel detection stays hardcoded `API_CANCEL_INFO_KEYWORD` | CONFIRMED | `raccoon_wallet_rules_accessor.py`; CONFIG-MIGRATION-PHASE-3B-3 |
| E-CONFIG-08 | 2026-06-02 | Phase 3B-5: Rules V2 runtime cutover for roster + groups when `RACCOON_WALLET_CONFIG_FROM_RULES_V2=1`; roster requires `yaml_norms ⊆ rules_norms` or YAML fallback; groups fallback on load error; YAML file retained | CONFIRMED | `raccoon_wallet_config_loader.py`; CONFIG-MIGRATION-PHASE-3B-5 |
| E-CONFIG-09 | 2026-06-02 | Production cutover to `RACCOON_WALLET_CONFIG_FROM_RULES_V2=1` completed — production `rules.xlsx` deployed to Dropbox; Railway env enabled. Remaining requirement before YAML removal: production observation with clean logs (`source=rules_v2`) and successful Raccoon Wallet Telegram report generation | CONFIRMED | CONFIG-MIGRATION-PHASE-3B-6; `tasks.md` gate criteria |
| E-CONFIG-10 | 2026-06-02 | Raccoon Wallet YAML fully retired — `config/raccoon_wallet_config.yaml` removed; `resolve_raccoon_wallet_config()` is Rules V2 only; shadow compares and YAML fallback removed | CONFIRMED | `raccoon_wallet_config_loader.py`; CONFIG-MIGRATION-PHASE-3B-6 |
| E-CONFIG-11 | 2026-06-02 | Raccoon Wallet Rules V2 flag retired — `RACCOON_WALLET_CONFIG_FROM_RULES_V2` env removed; no alternative runtime mode; Rules V2 is the unconditional config source | CONFIRMED | `raccoon_wallet_config_loader.py`; CONFIG-MIGRATION-PHASE-3B-7 |
| E-CONFIG-12 | 2026-06-02 | Raccoon Wallet configuration migration completed — Rules V2 is the only supported configuration source; legacy YAML configuration path is retired and must not be reintroduced | CONFIRMED | CONFIG-MIGRATION-RACCOON-WALLET epic; Phases 3A, 3B-1…3B-7 |
| E-CONFIG-13 | 2026-06-02 | Payout runtime switched to Rules V2 in production — `PAYOUT_CONFIG_FROM_RULES_V2=1` on Railway; prod `rules.xlsx` with `payout_info_rules` / `payout_ignore_phrases` deployed to Dropbox; logs `[payout_config] source=rules_v2`; YAML retained as fallback; rollback via `PAYOUT_CONFIG_FROM_RULES_V2=0` | CONFIRMED | CONFIG-MIGRATION-PHASE-4C; `analyzers/payout_config_loader.py` |
| E-CONFIG-14 | 2026-06-15 | Legacy `config_manager` `ALLOWED_JOB_PARAMS` whitelist must stay synchronized with Rules V2 job registry — new `job_params` jobs/keys added to rules must be whitelisted in legacy validator or `get_job_params()` fails closed to `{}` and can break hourly gate / other consumers | CONFIRMED | `core/config_manager.py`; incident hourly `no_gate_config`; commit `e4b31fb` |
| E-CONFIG-15 | 2026-06-16 | Legacy validator whitelist expanded with `conversion` / `valid_status` (`str`) so multiple conversion status rows in `job_params` do not fail-closed the sheet and break hourly gate or raccoon_wallet scalars | CONFIRMED | `core/config_manager.py`; `tests/test_job_params_legacy_whitelist.py`; commit `194d99e` |
| E-TG-ROUTES-01 | 2026-06-03 | Telegram **delivery** destinations migrate via optional Rules V2 sheet `telegram_routes`; Phase 2 = model + validator + index + accessor + ENV↔rules shadow compare only; **no** `send_message` switch; `TELEGRAM_CHAT_ID_EMERGENCY` ENV-only (never business fallback); `access_rules.chat_id` ≠ delivery route | CONFIRMED | `core/rules_v2/*`; `integrations/telegram_routes.py`; Phase 3 = `send_to_route` cutover |
| E-TG-ROUTES-02 | 2026-06-03 | Phase 3A: first runtime route — `platform_hourly_report` behind `TELEGRAM_ROUTES_FROM_RULES_V2` (default `0`); `send_message_to_route` / `resolve_route_chat_id`; hourly job only; missing/disabled → skip (no emergency) | CONFIRMED | `integrations/telegram_routes.py`; `integrations/tg_commands.py` `run_hourly_job` |
| E-TG-ROUTES-03 | 2026-06-03 | Phase 3B: second runtime route — `platform_wallet_download_report` / `TELEGRAM_CHAT_ID_WALLET`; `downloader_wallets.run_wallet_cycle` → `send_message_to_route`; same flag; text reports (not files) | CONFIRMED | `integrations/telegram_routes.py`; `integrations/downloader_wallets.py` |
| E-TG-ROUTES-04 | 2026-06-03 | Phase 3C: `conversion_wallet_editor` / `CONVERSION_WALLET_EDITOR`; bridge notifications + config chat via `send_message_to_route` when flag on; WE `task.chat_id` unchanged semantically | CONFIRMED | `integrations/telegram_routes.py`; `integrations/conversion_wallet_editor_bridge.py` |
| E-TG-ROUTES-05 | 2026-06-03 | Phase 3D: `bakai_rate_current` / `bakai_rate_alert`; `bakai_monitor_playwright` route send helpers; lazy ENV (no import-time raise) | CONFIRMED | `integrations/telegram_routes.py`; `integrations/bakai_monitor_playwright.py` |
| E-OPS-01 | 2026-06-04 | Scheduled jobs use `dispatch_job_background` (executor submit, no `future.result()` in `schedule_loop`); single-flight preserved via `job_runner` lock; overlap → `job_rejected_busy` | CONFIRMED | `core/job_dispatch.py`; `scheduler.py` |
| E-OPS-02 | 2026-06-04 | Wallet downloader: explicit Playwright timeouts aligned with `hourly_downloader`; stage logs; timeout → exception → `job_failed` + lock release in `finally` | CONFIRMED | `integrations/downloader_wallets.py` |
| E-OPS-03 | 2026-06-04 | Job Health Guard v2 rollout: **C1** = observe only (`job_health` in `/status`, `job_health_degraded` events); **C2** planned = ghost_lock_only; **C3** planned = stuck recovery (manual/flag-gated) | CONFIRMED | `core/job_health.py`; `core/job_progress.py`; `tasks.md` JOB-HEALTH-GUARD-V2 |
| E-OPS-04 | 2026-06-07 | Wallet payout downloader datepicker: navigate calendar to target month (`Previous month` loop) then select `[data-date='YYYY-MM-DD']`; fixes prior-month wrong-date selection | CONFIRMED | `integrations/downloader_wallets.py` `_find_and_pick_date` |
| E-OPS-05 | 2026-06-11 | Hourly scheduler gate uses bucket-based eligibility (not `minute % interval == 0` only) so delayed cron ticks still fire; gate state exposed in `/status` | CONFIRMED | `scheduler.py`; `core/scheduler_health.py`; commit `f936078` |
| E-HOURLY-01 | 2026-06-15 | Hourly payins `group_break_after` boundaries are resolved in render model **before** `hide_inactive_rows` filtering; blank line between non-empty segments is presentation-only (no DTO/analyzer change) | CONFIRMED | `reporters/hourly_render_model.py`; `tests/test_hourly_hide_inactive_rows.py` |

### Security decisions (E-SEC-*)

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E-SEC-01 | 2026-06-07 | Telegram bot token must not appear in logs, exception text, or health alerts; sanitize via `sanitize_telegram_error()` — replace token and `/bot<TOKEN>/` URLs with `<redacted>` / `bot<redacted>` | CONFIRMED | `integrations/telegram_bot.py`; `tests/unit/test_telegram_token_sanitization.py` |

---

## Implicit invariants (I#)

| ID | Инвариант | Нарушение = | Статус |
|----|-----------|-------------|--------|
| I1 | Dropbox pipeline lock считается stale после 600s | duplicate analyze / skip | CONFIRMED |
| I2 | Side effects только через documented integrations | hidden coupling | CONFIRMED |
| I3 | DORMANT modules не активируются без E# record | scope creep | CONFIRMED |
| I4 | DOCS_ONLY не деплоится как prod | false expectations | CONFIRMED |
| I5 | Unknown `job_type` → `job_failed` event, no registry fn called | silent wrong job | CONFIRMED |
| I6 | `schedule_loop` reloads schedules each tick; inactive job types dropped from next timers | stale triggers | CONFIRMED |
| I7 | Startup fail-fast: broken rules → process exit before polling | `scheduler.py` L272 | CONFIRMED |
| I8 | `rules.xlsx` MUST contain sheets `meta`, `exclude_time`, `access`, `commands` | publish/validation fail | CONFIRMED |
| I9 | Hourly input xlsx MUST have columns `Партнер`, `Сумма`, `Статус`, `Дата/Время создания` | hourly job fail | CONFIRMED |
| I10 | `state.json` job fingerprints stored at `jobs.{job_type}.last_fingerprint` | dedup break if corrupted | CONFIRMED |
| I11 | Conversion `load_data` requires mapped columns `card`, `status`, `datetime` minimum | analyze fail | CONFIRMED |
| I12 | Conversion fingerprint Phase 1A: match does **not** skip `conversion.run`; dedup skip requires Phase 1B + explicit decision | false dedup / missed reports | CONFIRMED |
| I13 | Observation layer errors (write/retention/read) must not affect conversion pipeline outcome; `conversion_fingerprint_computed` payload unchanged | silent behavior change / contract drift | CONFIRMED |
| I14 | `schedule_loop` must not block on job completion; health tick runs every loop iteration when guard enabled | false “scheduler dead” on long wallet | CONFIRMED |
| I15 | Job Health Guard C1 must not clear locks or start duplicate jobs; recovery requires explicit C2/C3 + flag | split-brain wallet / zombie Chromium | CONFIRMED |

---

## DORMANT activation log

| Module | Decision | Date | Task |
|--------|----------|------|------|
| `analyzers/transactions.py` | pending | | |

---

## Отменённые / superseded

| ID | Было | Заменено на | Дата |
|----|------|-------------|------|
| | | | |

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-31 | G1 — TASK-2026-05-31-01; E1, E2 |
| 2026-05-31 | G2 — TASK-2026-05-31-02; E4, E7, E9; I5–I7 |
| 2026-05-31 | G5 — TASK-2026-05-31-03; I8–I11 |
| 2026-06-01 | WalletEditor E-WE-01…E-WE-06 |
| 2026-06-03 | WalletEditor E-WE-07 — Dropbox cumulative registry |
| 2026-06-03 | WalletEditor E-WE-08 — registry lifecycle (hold, Отлёжка, re-enable dates) |
| 2026-06-03 | WalletEditor E-WE-09 — format-safe registry + Dropbox rev protection |
| 2026-06-03 | WalletEditor E-WE-10 — registry timeout job_params + async append after TG |
| 2026-06-02 | Conversion E-CONV-01…E-CONV-05; I12 passive fingerprint invariant; I13 observation best-effort invariant |
| 2026-06-02 | CONV-WE-HOOK — E-CONV-06…E-CONV-08 |
| 2026-06-02 | Raccoon Wallet production cutover — E-CONFIG-09; Phase 3B-6 `WAITING_FOR_PROD_OBSERVATION` |
| 2026-06-02 | Raccoon Wallet YAML retired — E-CONFIG-10; CONFIG-MIGRATION-PHASE-3B-6 complete |
| 2026-06-02 | Raccoon Wallet feature flag retired — E-CONFIG-11; CONFIG-MIGRATION-PHASE-3B-7 complete |
| 2026-06-02 | CONFIG-MIGRATION-RACCOON-WALLET epic closed — E-CONFIG-12 |
| 2026-06-02 | Payout Rules V2 production cutover — E-CONFIG-13; CONFIG-MIGRATION-PHASE-4C complete |
| 2026-06-03 | Telegram routes Phase 2 — E-TG-ROUTES-01; shadow-only infrastructure |
| 2026-06-03 | Telegram routes Phase 3A — E-TG-ROUTES-02; `platform_hourly_report` behind feature flag |
| 2026-06-03 | Telegram routes Phase 3B — E-TG-ROUTES-03; `platform_wallet_download_report` / wallet download cycle |
| 2026-06-03 | Telegram routes Phase 3C — E-TG-ROUTES-04; `conversion_wallet_editor` / Conversion→WE bridge |
| 2026-06-03 | Telegram routes Phase 3D — E-TG-ROUTES-05; Bakai rate routes |
| 2026-06-04 | Wallet hang mitigation — E-OPS-01…03; Patch A/B + Job Health Guard C1 |
| 2026-06-07 | WalletEditor auto-enable E-WE-11…13; HOLD E-WE-14; partner mapping E-WE-15; registry UX-A; E-SEC-01; E-OPS-04 wallet datepicker |
| 2026-06-11 | Hourly gate bucket tolerance — E-OPS-05 (`f936078`) |
| 2026-06-15 | Hourly incident whitelist fix — E-CONFIG-14 (`e4b31fb`); payin group spacing — E-HOURLY-01 |
| 2026-06-16 | Conversion `valid_status` legacy whitelist — E-CONFIG-15 (`194d99e`) |
