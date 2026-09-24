# Tasks & Gaps — analizis

| Мета | Значение |
|------|----------|
| **KB версия** | v1.10 |
| **Последнее обновление** | 2026-09-17 |

---

## Status

| Поле | Значение |
|------|----------|
| **Документ** | draft — G1, G2, G5 closed |
| **Open gaps** | G3, G4 (partial) |
| **STALE_RISK** | S1–S3 |
| **Active workflow tasks** | CONV-OPTIMIZATION-PHASE-1B (READY); **TASK-2026-09-17-01..09** Draft PR #4–#12 (не слиты; 02–09 review пройден) |
| **Telegram routes** | Phase 3A–3D **done** (+ Bakai `bakai_rate_current`/`bakai_rate_alert`); Phase 3E+ routes pending (e.g. ANALIZ family) |
| **Job locks** | Ghost PID-1 stale lock fix **done** (2026-06-03) — `core/job_runner.py` |
| **Wallet hang** | Patch A (PW timeouts) + Patch B (non-blocking scheduler) **done** (2026-06-04) |
| **Job Health Guard** | C1 observe **done**; C2/C3 **planned** — see roadmap below |
| **WalletEditor Auto-Enable** | Phase A/B1/B1.1/B2 **complete**; Scheduler Phase C (daily 08:00) **open** |
| **WalletEditor registry UX** | UX-A formatting **complete**; UX-B `Дата операции` **open**; UX-C filenames **open** |
| **WalletEditor Add Wallet** | Phase 1 / 1.1 / 2 **complete**; real UI verified 2026-06-21 |

---

## Purpose

Реестр gaps (**G_**), расхождений docs vs code (**S_**), операционных точек и индекс workflow-задач.

---

## Confirmed Facts

- **G5** closed (material) TASK-2026-05-31-03 — env, file, Excel/YAML/JSON/CSV inventories, data flows in `contracts.md`.
- Residual UNKNOWN: prod rules rows, full identity registry schema, prod CSV usage — see `contracts.md` § Remaining UNKNOWN.

---

## Unknowns

| ID | Gap / UNKNOWN |
|----|---------------|
| G3 | Integrations inventory — formal SLAs, auth rotation, prod verification |
| G4 | Deployment model — staging, rollback runbook, live prod status |
| G5 (residual) | Prod rules row content; full identity registry JSON schema; CSV-in-prod |

---

## Open gaps (G#)

| ID | Gap / UNKNOWN | Impact | Owner | Статус |
|----|---------------|--------|-------|--------|
| G3 | Integrations inventory (formal) | security / contracts | UNKNOWN | open |
| G4 | Deployment model — staging, rollback, live prod | deploy / incident | UNKNOWN | open (partial) |

---

## STALE_RISK (S#)

| ID | Расхождение | Где | Severity | Action |
|----|-------------|-----|----------|--------|
| S1 | Two lock systems | `run_once_guard.py` vs `job_runner.py` | med | Impact before unification |
| S2 | Raccoon in docs vs code | `EXPERT_REVIEW.md` / `architecture_map` | low | Код Test **содержит** `raccoon_*`. Это не доказательство prod execution на сервисе Test (U12). Prod Raccoon-only = Platform `develop` (F26). `ops/MODULAR_REORG_SURVEY.md` |
| S3 | Partner column spelling in conversion mapping | resolved — single source `main.py` `CONVERSION_COLUMNS` (`Партнёр`) | low | closed (E-CONFIG-03) |
| S4 | Dual validation path: legacy `config_manager` whitelist vs Rules V2 validator — different strictness can reject valid `job_params` rows and silently break consumers (`get_job_params` → `{}`, hourly `no_gate_config`) | `core/config_manager.py` vs `core/rules_v2` | **high** | Keep `ALLOWED_JOB_PARAMS` synced with Rules V2 job registry (E-CONFIG-14, E-CONFIG-15); regression `tests/test_job_params_legacy_whitelist.py` |

---

## WalletEditor operational risks (R-WE-*)

| ID | Risk | Impact | Mitigation / notes |
|----|------|--------|-------------------|
| R-WE-01 | Playwright memory при параллельных профилях | OOM / slowdown на Railway | Мониторинг RAM; ограничить число operator_profile |
| R-WE-02 | Рост worker threads ∝ числу `operator_profile` | thread / resource pressure | Lazy workers; один thread на профиль |
| R-WE-03 | Railway restart очищает `/tmp` auth-state | re-login Antares per profile после deploy | Expected; sessions ephemeral |
| R-WE-04 | Legacy `automation/main.py` / `tg_receiver.py` | double polling / broken `add_task` API if запущены | **Не** использовать в production; path = `scheduler.py` only |
| R-WE-05 | Missing / incomplete `WALLET_EDITOR_OPERATOR_MAP` | все ingest отклоняются (fail-closed) | Railway env checklist per operator |
| R-WE-06 | Result file name collision in `/tmp/wallet_editor` | suffix `_2` / `_<uuid8>` appended | `build_wallet_editor_result_path()` |
| R-WE-07 | ~~Concurrent Dropbox registry writes (multi-profile)~~ | ~~corrupt/missing rows in `wallet_editor.xlsx`~~ | **closed** — runtime no longer writes registry history to Dropbox (E-WE-26) |
| ~~R-TG-01~~ | ~~Silent Telegram sender failure (enqueue ≠ delivery)~~ | **mitigated** | Option B: health-state + periodic log in `telegram_bot.py` |
| R-WE-08 | Add Wallet optional fields skip silently when Antares label/control not found | operator believes field set when UI control missing | Monitor `[WalletEditorAdd]` logs; extend label map after Antares UI changes |
| R-WE-09 | Add Wallet UI labels/select options drift after Antares updates | fill skipped or wrong option | Contract tests + real UI smoke; tune `ADD_WALLET_LOWER_FORM_CONTROL_TYPES` |
| R-WE-10 | KYC depends on real label `KYC`/`КУС` in lower form; non-standard DOM → unchecked | `kyc=да` ignored with `kyc_not_found` log | Real UI retest after Antares modal changes; scoped search may miss KYC above anchor rows |

### Telegram delivery risks (R-TG-*)

| ID | Risk | Mitigation |
|----|------|------------|
| R-TG-02 | Dead sender worker thread (no auto-restart) | `queue_depth` + `last_success_age` in periodic health log; manual restart |
| R-TG-03 | Polling alive but outbound sender broken | Compare `/status` telegram_sender block vs scheduler; health logs |
| R-TG-04 | Business jobs commit state after enqueue (not delivery) | **open** — separate task; health does not fix state semantics |
| R-TG-05 | No external alert channel besides Railway logs | Railway log alert on `[TelegramSender/health] DEGRADED` |

### Job Health Guard risks (R-JHG-*)

| ID | Risk | Mitigation |
|----|------|------------|
| R-JHG-01 | False-positive `stuck` (slow Antares vs zombie) | Progress registry + per-job `JOB_HEALTH_*_SECONDS`; C1 observe rollout before C2/C3 recovery; tune from `/status` `progress_age` / `stage` |

---

## JOB-HEALTH-GUARD-V2 (roadmap)

| Phase | Status | Goal |
|-------|--------|------|
| **C1** | **COMPLETE** | Observe-only: `job_progress` + `job_health` + `/status` `job_health:`; `JOB_HEALTH_GUARD_ENABLED`; recovery **off** |
| **C2** | **PLANNED** | **ghost_lock_only** — clear stale lock file when provably ghost (not in `_RUNNING`, dead PID / PID-1 pattern / age > `JOB_LOCK_STALE_SEC`); no `_RUNNING` mutation |
| **C3** | **PLANNED** | **Stuck recovery** — manual TG command and/or `JOB_HEALTH_RECOVERY_MODE=stuck_release` flag; never default; requires progress+runtime proof; ops runbook for Chromium zombie |

**Dependencies:** Patch A + Patch B deployed; enable `JOB_HEALTH_GUARD_ENABLED=1` on Railway for C1 validation.

**Tests (C1):** `tests/unit/test_job_health_c1.py`, `test_downloader_wallets_timeouts.py`, `test_scheduler_dispatch_background.py`.

---

## Закрыто — WalletEditor (WE-0 … WE-6)

| ID | Status | Summary |
|----|--------|---------|
| WE-0 | **complete** | Import-safe migration from Platform_2.0; `automation/` in main repo |
| WE-1 | **complete** | Single PTB polling via `scheduler.py` + `wallet_editor_tg` handler |
| WE-2 | **complete** | Isolated auth-state path (per-profile in WE-5/WE-6) |
| WE-3 | **complete** | Isolated Antares credentials; no shared fallback |
| WE-4 | **complete** | Dedicated `WALLET_EDITOR_ALLOWED_CHAT_IDS` allowlist |
| WE-5 | **complete** | Operator routing by `telegram_user_id` → credentials + `WalletEditorTask` |
| WE-6 | **complete** | Per-profile queue + worker; parallel across operators |
| WE-7 | **complete** | Dropbox cumulative registry (`DROPBOX_WALLET_EDITOR_PATH`); sheets `all_results`, `runs`; E-WE-07 |
| WE-8 | **complete** | Registry lifecycle: `hold`, `Отлёжка`, re-enable date/status, warnings; E-WE-08 |
| WE-9 | **complete** | Registry format-safe write + Dropbox rev conflict protection; E-WE-09 |
| WE-10 | **complete** | Registry async append after TG; job_params timeout/warning/retry; E-WE-10 |
| WE-AE | **complete** | Auto-enable Phase A (plan) + B1 (Antares) + B1.1 (`max_rows_per_run`) + B2 (registry patch); `/auto_enable_plan` + `/auto_enable_run` |
| WE-HOLD | **complete** | HOLD enforcement for `add_partner` (manual + auto-enable); fail-closed |
| WE-UX-A | **complete** | Registry row formatting preservation on append/patch |
| WE-ADD-1 | **complete** | Add Wallet Phase 1: Excel routing (`detect_excel_routing`), contract, worker dispatch, create modal engine, strict post-save verification, result xlsx + TG summary; commits `6a28897`…`3de0b81` |
| WE-ADD-1.1 | **complete** | Aggregate nested block: single checkbox, field-presence detection, nested account/merchant/card fill |
| WE-ADD-2 | **complete** | Phase 2 optional columns (29 fields); status default `Тест`; no direction/state/pool/aggregate defaults; KYC scoped checkbox fix (`b68ded7`) |

Detail: `active_tasks/WALLET_EDITOR_WE-0-6_completed.md`

---

## Operational maintenance — closed

| ID | Status | Summary | Verdict / date |
|----|--------|---------|----------------|
| **OPS-WE-HISTORY-CLEANUP-20260828** | **complete** | Targeted Wallet Editor registry history cleanup for **5998** cards (Excel input list). Phase 1: PostgreSQL `we_registry_results` on prod `charismatic-optimism` / `Postgres-X9pi` — **4039** cards found, **14174** historical rows deleted, PG backup `.local/wallet_editor_card_cleanup/run_20260828/we_registry_results_backup_20260828T160316Z.csv`. Phase 2: durable `{STATE_DIR}/wallet_editor/results/` on prod `Test` service — **1421** xlsx scanned, **190** changed, **15680** rows removed, durable backup `/data/state/wallet_editor/cleanup_backup_20260828/`. Unchanged: `we_registry_runs`, hold, Отлёжка, `processed_run_ids` (**1870**), rules, Telegram, Antares. Recovery verified: backfill/repair/outbox cannot restore pre-cleanup fingerprints; prod health OK. **Not** a blacklist — future disable/enable may create new history. | **`CLEANUP VERIFIED`** — 2026-08-28 |

Artifacts: `.local/wallet_editor_card_cleanup/run_20260828/FINAL_VERDICT.json` (local ops; not application source).

---

## Закрыто — WalletEditor Registry v2 (2026-07-01)

| ID | Status | Summary |
|----|--------|---------|
| TASK-2026-07-01-01 | **complete** | Manual sync foundation — PG schema v2, snapshot hash, sync library |
| TASK-2026-07-01-02 | **complete** | Pre-run manual sync gate before dangerous ops |
| TASK-2026-07-01-03 | **complete** | Runtime readers `hold`/`Отлёжка` from PostgreSQL |
| TASK-2026-07-01-04 | **complete** | `/registry_export` via `RegistryExportBuilder` → Telegram |
| TASK-2026-07-01-05 | **complete** | Remove Dropbox projection; `excel` source removed; CLI on builder |

Detail: `active_tasks/TASK-2026-07-01-0[1-5]_*.md`; ADR E-WE-23…E-WE-27

---

## Закрыто — Conversion Modernization Program

| ID | Status | Summary |
|----|--------|---------|
| Conversion Characterization | **DONE** | Golden tests — `tests/test_conversion_characterization.py` |
| Conversion DTO extraction | **DONE** | `analyzers/conversion_dto.py` |
| Conversion Reporter extraction | **DONE** | `reporters/conversion_reporter.py` |
| Conversion RulesAccessor extraction | **DONE** | `ConversionRulesAccessor` in `core/rules_v2/accessors.py` |
| Conversion Original Partner Name | **DONE** | Original partner column preserved in output |
| Conversion Routing migration | **DONE** | Explicit routing; YAML conversion section deprecated |
| Conversion Orchestrator B1 | **DONE** | `integrations/conversion_pipeline.py` |
| Conversion Orchestrator B2 | **DONE** | Downloader → `run_conversion_pipeline` |
| Conversion Orchestrator B3 | **DONE** | `main.process_file` conversion delegation |
| Conversion Observability Phase 1 | **DONE** | `conversion_*` events + `jobs.conversion` state + `/status` |
| Conversion Observability Phase 2 | **DONE** | Downloader tiered final TG; failure propagation fixed |
| Conversion Fingerprint Phase 1A | **DONE** | Passive fingerprint — compute/compare/store; no dedup skip |
| Conversion Fingerprint Phase 1B Observation | **DONE** | Diagnostic JSONL — full hashes, changed_components, would_skip; flag default off |
| Conversion → Wallet Editor hook | **DONE** | `conversion_wallet_editor_bridge.py`; best-effort; all valid cards/run; direct scheduled credentials env |

---

## Follow-up (open)

| ID | Goal | Status |
|----|------|--------|
| CONV-WE-LIMIT-REMOVAL | Remove 10-card rollout limit; process all valid `problem_cards` | **complete** (2026-06-03) |

---

## Закрыто в v1.1 (gaps)

| ID | Закрыто | Как |
|----|---------|-----|
| G1 | 2026-05-31 | TASK-2026-05-31-01 |
| G2 | 2026-05-31 | TASK-2026-05-31-02 |
| G5 | 2026-05-31 | TASK-2026-05-31-03 — Data Contracts (material) |

---

## Active workflow tasks (index)

| Task ID | Status | Goal (1 line) |
|---------|--------|---------------|
| TASK-2026-05-31-01 | complete | Close G1 Project Definition |
| TASK-2026-05-31-02 | complete | Close G2 Runtime Architecture |
| TASK-2026-05-31-03 | complete | Close G5 Data Contracts |
| WALLET_EDITOR WE-0…WE-6 | complete | Integrate WalletEditor into main runtime |
| WALLET_EDITOR ADD-WALLET | complete | Add Wallet Phase 1 / 1.1 / 2 — Excel ingest routing, create flow, optional fields |
| TELEGRAM-SENDER-HEALTH-B | complete | Option B outbound delivery health + periodic log |
| CONV-WE-HOOK | complete | ConversionAnalyzer problem_cards → Wallet Editor bridge |
| CONFIG-MIGRATION-PHASE-1 | complete | `payout_config.yaml` → Rules V2 sheets + shadow compare + runtime switch |
| CONFIG-MIGRATION-PHASE-2 | complete | Remove `analysis_map.yaml`; explicit routing in `selector.py` |
| CONFIG-MIGRATION-PHASE-3A | complete | Raccoon wallet scalar params → `job_params` + shadow compare |
| CONFIG-MIGRATION-PHASE-3B-1 | complete | Raccoon wallet partner roster shadow (YAML primary unchanged) |
| CONFIG-MIGRATION-PHASE-3B-2 | complete | PayIn columns → code constants + YAML shadow |
| CONFIG-MIGRATION-PHASE-3B-3 | complete | Groups membership shadow + dead YAML fields cleanup |
| CONFIG-MIGRATION-PHASE-3B-4 | complete | Cutover readiness audit + manual rules prep checklist |
| CONFIG-MIGRATION-PHASE-3B-5 | complete | Rules V2 runtime cutover for roster/groups (`RACCOON_WALLET_CONFIG_FROM_RULES_V2=1`) |
| CONFIG-MIGRATION-PHASE-3B-6 | complete | Delete `raccoon_wallet_config.yaml`; remove YAML fallback; retire shadow compares |
| CONFIG-MIGRATION-PHASE-3B-7 | complete | Remove `RACCOON_WALLET_CONFIG_FROM_RULES_V2` env flag; Rules V2 unconditional |
| **CONFIG-MIGRATION-RACCOON-WALLET** | **complete** | Epic: Raccoon Wallet config YAML → Rules V2 (Phases 3A, 3B-1…3B-7) |
| CONFIG-MIGRATION-PHASE-4A | complete | Payout rules workbook prep (`payout_info_rules`, `payout_ignore_phrases`) |
| CONFIG-MIGRATION-PHASE-4B | complete | Validate Payout Rules V2 mode=1 — semantic equality + shadow clean |
| CONFIG-MIGRATION-PHASE-4C | complete | Payout Rules V2 production cutover (`PAYOUT_CONFIG_FROM_RULES_V2=1`) |
| WALLET-HANG-PATCH-A | complete | Playwright timeouts + wallet stage logs + progress hooks |
| WALLET-HANG-PATCH-B | complete | Scheduler `dispatch_job_background` |
| JOB-HEALTH-GUARD-C1 | complete | Observe-only job health in `/status` |
| TASK-2026-06-23-01 | complete | WalletEditor registry Postgres Phase 1 — mirror, health, postgres-first append/patch/refresh |
| TASK-2026-06-24-01 | complete | Registry mirror observation + reconcile; Stage D diagnostics |
| TASK-2026-07-01-01 | complete | Manual sync foundation — schema v2, snapshot hash, sync library |
| TASK-2026-07-01-02 | complete | Pre-run manual sync gate |
| TASK-2026-07-01-03 | complete | Runtime readers hold/Отлёжка from PostgreSQL |
| TASK-2026-07-01-04 | complete | `/registry_export` + RegistryExportBuilder (Telegram delivery) |
| TASK-2026-07-01-05 | complete | Remove Dropbox registry projection; excel source removed |
| TASK-2026-07-01-06 | ready | KB finalization + ops cutover checklist |

**TASK-2026-06-23-01 completed subtasks:**

- ✓ `diagnose_processed_without_rows` CLI (B1/B2/B3/B4 classification)
- ✓ Postgres backfill tool (`backfill_processed_without_rows`)
- ✓ Lifecycle refresh tool (`refresh_registry_lifecycle_fields`)
- ✓ Registry export CLI (`export_wallet_editor_registry`)
- ✓ Telegram `/registry_export`
- ✓ Postgres-aware registry warnings in auto-enable (`processed_without_rows` vs historical `outbox_failed`)

---

## Closed epics

### CONFIG-MIGRATION-RACCOON-WALLET

**Status:** COMPLETE

**Outcome:**

- Raccoon Wallet migrated from YAML to Rules V2
- YAML configuration removed
- Runtime fallback removed
- Shadow compare removed
- Feature flag removed
- Rules V2 is sole source of truth

**Phases:** 3A, 3B-1, 3B-2, 3B-3, 3B-4, 3B-5, 3B-6, 3B-7 — all COMPLETE

---

## Open workflow tasks

| Task ID | Status | Goal (1 line) | Prerequisite |
|---------|--------|---------------|--------------|
| **TASK-2026-09-17-01** | **in_progress** | Docs: survey + ADR + migration for Antares/Raccoon/WR modular reorg | — |
| **TASK-2026-09-17-02** | **review** | Parser + empty packages + unit tests; review passed; **not merged** | Draft PR #5 |
| **TASK-2026-09-17-03** | **review** | Early profile gate; review passed (HEAD `48a2a82`); **not merged** | Draft PR #6 |
| **TASK-2026-09-17-04** | **review** | Freeze current behavior; review passed (HEAD `443ba70e`); **not merged** | Draft PR #7 |
| **TASK-2026-09-17-05** | **review** | Six Antares JOB_REGISTRY keys in `modules.antares.jobs.register_jobs`; review passed (HEAD `a6d7ebcf`); **not merged** | Draft PR #8 |
| **TASK-2026-09-17-06** | **review** | Handler-split plan; review passed (HEAD `380a4bb`); **not merged** | Draft PR #9 |
| **TASK-2026-09-17-07** | **review** | Four Antares run-commands + three helpers; review passed (HEAD `94be3127`); **not merged** | Draft PR #10 |
| **TASK-2026-09-17-08** | **review** | operator_wallets_ready + wallet_editor_refresh; review passed (HEAD `7169cd48`); **not merged** | Draft PR #11 |
| **TASK-2026-09-17-09** | **review** | registry_health + registry_replay (direct ops); review passed (HEAD `b8085a28`); **not merged** | Draft PR #12 |
| **TASK-2026-09-17-10** | **review** | registry_export (direct ops via executor); review passed (HEAD `8c37766`); **not merged** | Draft PR #13 |
| **TASK-2026-09-17-11** | **review** | auto_enable_plan + auto_enable_run (direct ops via executor); review passed (HEAD `0fa283eb`); **not merged** | Draft PR #14 |
| **TASK-2026-09-17-12** | **review** | Plan to isolate WE document ingest; review passed (HEAD `428d50fe`); **not merged** | Draft PR #15 |
| **TASK-2026-09-17-13** | **review** | Move WE document ingest to `modules.antares.document_ingest`; review passed (HEAD `4a1e7796`); **not merged** | Draft PR #16 |
| **TASK-2026-09-17-14** | **review** | Plan isolated Antares handler/job assembly; review passed (HEAD `f123a4bf`); closed `99d2db55`; **not merged** | Draft PR #17 |
| **TASK-2026-09-17-15** | **review** | Selective script JOB_REGISTRY bind; review passed (HEAD `6e4c6c4`); **not merged** | Draft PR #18 |
| **TASK-2026-09-17-16** | **review** | Isolated Antares handler/job assembly without start; review passed (HEAD `f29cc89`); assembly not released; **not merged** | Draft PR #19 |
| **TASK-2026-09-17-17** | **review** | Plan isolated Antares process entry; review passed (HEAD `ed3cbaf`); **not merged** | Draft PR #20 |
| **TASK-2026-09-17-18** | **review** | Isolated Antares boot without polling; review passed (HEAD `2c6eeac`); 79 passed Cursor 3.12.10; GPT code/diff only; **not released / not merged** | Draft PR #21 |
| **TASK-2026-09-17-19** | **review** | Plan Antares lifecycle; review passed (HEAD `b76a377`); runtime unchanged; service lifecycle not implemented; **not merged** | Draft PR #22 |
| **TASK-2026-09-17-20** | **review** | Isolated `run`: local workbook snapshot + exit 0; review passed (HEAD `8b42e4d`); **not released / not merged** | Draft PR #23 |
| **TASK-2026-09-17-21** | **review** | Plan Application + handlers without polling; review passed (HEAD `777a52f`); runtime unchanged; pytest not run; **not merged** | Draft PR #24 |
| **TASK-2026-09-17-22** | **review** | Isolated `run` builds Application + handlers without polling; review passed (HEAD `d9592ff`); 81/33 passed Cursor 3.12.10; GPT code/diff only; **not released / not merged** | Draft PR #25 |
| **TASK-2026-09-17-23** | **review** | Plan initialize/start/stop; review passed (HEAD `6540a36`); close `94951c6`; runtime unchanged; **not merged** | Draft PR #26 |
| **TASK-2026-09-17-24** | **review** | Sandbox `run_ptb_lifecycle`; review passed (HEAD `34ef7af`); close `3649764`; 15/96 Cursor 3.12.10 PTB 22.8; GPT code/diff only; historical 91/10 `e737281`, 94/13 `d842912`; **not released / not merged** | Draft PR #27 |
| **TASK-2026-09-17-25** | **review** | Isolated admission contract; review passed (HEAD `eda7144`); close `a33df9c`; 1d=8h; mixed-stop verify 2–4d separate; cutover plan not executable; **not merged** | Draft PR #28 |
| **TASK-2026-09-17-26** | **review** | WorkAdmission + isolated `/run_wallet`; review passed (HEAD `960bf69`); Cursor 62/52 exit 0; GPT code/tests + AdmittedJob 3.12.14 PASS, full pytest not run; not released / **not merged** | Draft PR #29 |
| **TASK-2026-09-17-27** | **review** | Isolated admission for six dispatch commands; review passed (`a84e9cd…`); close `08132f2…`; Cursor 90/52; GPT code/diff, these pytest not run; not released / **not merged** | Draft PR #30 |
| **TASK-2026-09-17-28** | **review** | Isolated admission for export/Auto-Enable plan/run; review passed (`f0bd06b…`); close `a81aa69…`; Cursor 120/21 on review HEAD; 52 lifecycle/boot historical `a5836de…`; GPT sources, pytest not run; enqueue bypass; not released / **not merged** | Draft PR #31 |
| **TASK-2026-09-17-29** | **review** | Isolated `/registry_replay` admission; review passed (`71fce3b…`); close `d3eecc0…`; Cursor 129/52; GPT code/diff, pytest not run; not released / **not merged** | Draft PR #32 |
| **TASK-2026-09-17-30** | **review** | Isolated `/reload_rules` admission; review passed on combined PR #35 `c420b590…` (GPT code/diff, pytest not run; Cursor 161; historical 130 on `bb25f734…`); PR #33 `1eefc54` not independently accepted; not released / **not merged** | Draft PR #35 (accepted combined); Draft PR #33 leftover |
| **TASK-2026-09-17-31** | **review** | Publish-generation contract; GPT reviewed `8ef2838…`; close `3d56791…`; runtime unchanged; pytest not run; **not merged** | Draft PR #34 |
| **TASK-2026-09-17-32** | **review** | Rules publish generation; GPT reviewed code/diff `bb25f734…` (pytest not run by GPT); Cursor 70 on `bb25f734…`, 123/34/30/52 on `5bbde8f…`; docs close `7d3a463…`; PR #35 Draft; not released / **not merged** | Draft PR #35 |
| **TASK-2026-09-17-33** | **review** | Isolated Telegram document ingest admission contract; GPT reviewed `4682e399…`; docs close `cecb336…`; runtime unchanged; pytest not run by GPT; impl TASK-34; **not merged** | Draft PR #36 |
| **TASK-2026-09-17-34** | **review** | Isolated Telegram document ingest admission code; GPT reviewed `f76f9c9…`; docs close `0576144…`; Cursor 28/95 on `f76f9c9…`; 123/52 historical `5362a93…`; not released / **not merged** | Draft PR #37 |
| **TASK-2026-09-17-35** | **review** | Isolated schedules admission contract; GPT reviewed `ce5326a…`; docs close `0be29ec…`; runtime unchanged; pytest not run by GPT; impl TASK-36; **not merged** | Draft PR #38 |
| **TASK-2026-09-17-36** | **review** | Isolated schedule tick admission; GPT reviewed `3f6d3e7…`; docs close `c9c7533…`; Cursor 31/7/84/52; mixed two-tick expectation defect preserved; not wired to boot/run; **not merged** | Draft PR #39 |
| **TASK-2026-09-17-37** | **review** | Isolated Auto-Enable enqueue continuation contract; GPT reviewed `741f5cc…`; docs close `8efa1ec…`; runtime unchanged; pytest not run by GPT; impl TASK-38; **not merged** | Draft PR #40 |
| **TASK-2026-09-17-38** | **review** | Isolated Auto-Enable enqueue continuation; GPT reviewed code/diff/tests `f128110…` (pytest not run by GPT); docs close `9221f05…`; Cursor 25/120/52; two prior failures 40+1 / 36+1 same as base `8efa1ec…`; not released / **not merged** | Draft PR #41 |
| **TASK-2026-09-17-39** | **review** | Isolated drain/stop contract; GPT reviewed `69ae53c…` (pytest not run by GPT); docs close this commit; matrix D1–D30; O1–O9 chosen; runtime unchanged; **not merged** | Draft PR #42 |
| **TASK-2026-09-17-40** | **ready** | Accepted executor Future registry + `wait_accepted_executor_work`; not full drain; not helper/WE/sender/registry | TASK-39 close |
| **CONFIG-MIGRATION-PHASE-4D** | **OPEN** | Payout observation period — monitor prod logs for clean `[payout_config] source=rules_v2`; no `[config_shadow] payout mismatch`; gate YAML removal | CONFIG-MIGRATION-PHASE-4C complete |
| **CONV-OPTIMIZATION-PHASE-1B** | **READY** | Real dedup skip on fingerprint match — skip `conversion.run` when inputs unchanged | Collect 7–14 days observation data (`CONVERSION_FP_OBSERVATION_ENABLED=1`); GO/NO-GO from observation JSONL |
| **WE-UX-B** | **OPEN** | Add registry column `Дата операции` (leftmost); conditional `Дата отключения` by action; lazy migration on next write | UX-A complete |
| **WE-UX-C** | **OPEN** | Readable WalletEditor result filenames (manual / conversion / auto-enable / registry staging) | Independent of UX-B |
| **WE-AE-SCHEDULER-C** | **OPEN** | Scheduled daily auto-enable at 08:00 MSK via Rules `schedules` | Phase B2 complete; prod `rules.xlsx` row + ACL for commands |
| **WE-REGISTRY-OUTBOX-PHASE1** | **complete** | Durable STATE_DIR outbox + replay + registry health (E-WE-20) |
| **TASK-2026-06-23-01** | **complete** | WalletEditor registry Postgres Phase 1 — postgres-first ops (E-WE-21) |
| **TASK-2026-06-24-01** | **complete** | Registry mirror observation / reconcile (Stage D) |
| **WE-REG-WATCHDOG** | **OPEN** | Registry append timeout/watchdog alerts; stale append detection | E-WE-10 partial coverage via job_params; **partial:** outbox + `/registry_health` (E-WE-20) |
| **WE-LIFECYCLE-REFRESH** | **complete** | Lifecycle refresh job + `tools/refresh_registry_lifecycle_fields.py` | `wallet_editor_registry_refresh` job; postgres lifecycle patch CLI |
| **WE-AE-SUMMARY** | **OPTIONAL** | Final aggregated auto-enable summary message across batches | Per-batch reports exist today |
| **WE-POSTGRES-HISTORY** | **complete** | Postgres registry source of truth for `all_results`/`runs` | E-WE-23; rollback Git/deploy only (E-WE-27) |
| **TASK-2026-07-01-06** | **ready** | Ops cleanup — backup Dropbox workbook, delete `all_results`/`runs` sheets, keep `hold`+`Отлёжка`; see `ops/WALLET_EDITOR_POSTGRES_CUTOVER.md` | TASK-2026-07-01-05 complete |

**Phase 1B scope (planned, not started):**

- Skip `conversion.run` when `current_fingerprint == last_fingerprint`
- Emit `conversion_skipped reason=no_changes`
- Downloader final message nuance for skip case
- Feature flag / rollout gate separate from passive fingerprint

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-31 | G1 closed — TASK-2026-05-31-01 |
| 2026-05-31 | G2 closed — TASK-2026-05-31-02 |
| 2026-05-31 | G5 closed (material) — TASK-2026-05-31-03 |
| 2026-06-01 | WalletEditor WE-0…WE-6 closed; R-WE-* risks added |
| 2026-06-01 | Telegram sender health Option B; R-TG-01 mitigated |
| 2026-06-02 | Conversion Modernization Program closed (12 items DONE); Phase 1B Observation Layer DONE; CONV-OPTIMIZATION-PHASE-1B dedup skip open (READY) |
| 2026-06-02 | CONV-WE-HOOK complete — Conversion → Wallet Editor bridge; follow-up CONV-WE-LIMIT-REMOVAL |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-1 complete — payout_config.yaml → Rules V2 (shadow-first); Phase 2 candidate: analysis_map.yaml |
| 2026-06-02 | E-CONFIG-02 — prod `rules.xlsx` changes deferred until all CONFIG-MIGRATION phases complete |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-2 complete — `analysis_map.yaml` removed; routing in `selector.py` (E-CONFIG-03); Phase 3 candidate: raccoon_wallet_config.yaml |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-3A complete — Raccoon wallet scalar params shadow-ready via `job_params`; Phase 3B: roster/groups/columns |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-3B-1 complete — partner roster shadow; Phase 3B-2: columns + groups |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-3B-2 complete — PayIn columns code constants + YAML shadow; Phase 3B-3: groups shadow + dead fields |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-3B-3 complete — groups membership shadow + dead fields cleanup; Phase 3B-4: cutover readiness + YAML removal plan |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-3B-5 complete — Rules V2 runtime cutover for roster/groups; Phase 3B-6: YAML deletion |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-3B-6 — production cutover deployed; status `WAITING_FOR_PROD_OBSERVATION`; YAML removal gated on prod observation |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-3B-6 complete — `raccoon_wallet_config.yaml` removed; Rules V2 only; shadow/fallback retired |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-3B-7 complete — `RACCOON_WALLET_CONFIG_FROM_RULES_V2` env flag removed; Rules V2 unconditional |
| 2026-06-02 | CONFIG-MIGRATION-RACCOON-WALLET epic closed — Raccoon Wallet Rules V2 migration complete (E-CONFIG-12) |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-4A/4B complete — payout rules workbook + mode=1 validation |
| 2026-06-02 | CONFIG-MIGRATION-PHASE-4C complete — Payout Rules V2 production cutover (E-CONFIG-13); Phase 4D observation open |
| 2026-06-07 | WE-AE, WE-HOLD, WE-UX-A closed; WE-UX-B/C, WE-AE-SCHEDULER-C added to open tasks |
| 2026-06-21 | WE-ADD-1 / WE-ADD-1.1 / WE-ADD-2 closed; R-WE-08…R-WE-10 Add Wallet operational risks |
| 2026-06-22 | WE-REGISTRY-OUTBOX-PHASE1 complete — durable outbox, replay, registry health (E-WE-20) |
| 2026-06-23 | TASK-2026-06-23-01 / TASK-2026-06-24-01 closed — WalletEditor registry Postgres SoT + ops tooling (E-WE-21, E-WE-22) |
| 2026-07-01 | TASK-2026-07-01-01…05 closed — WalletEditor Registry v2 (ManualSync, PG readers, TG export, projection removed); TASK-2026-07-01-06 KB + ops checklist (E-WE-23…E-WE-27) |
| 2026-08-28 | OPS-WE-HISTORY-CLEANUP-20260828 complete — targeted Wallet Editor PG + durable history cleanup; **5998** cards; **CLEANUP VERIFIED**; no runtime/code changes |
| 2026-09-17 | Modular reorg program opened — TASK-2026-09-17-01 docs (`ops/MODULAR_REORG_*.md`); S2 annotated stale; runtime unchanged |
