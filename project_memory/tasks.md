# Tasks & Gaps — analizis

| Мета | Значение |
|------|----------|
| **KB версия** | v1.3 |
| **Последнее обновление** | 2026-06-02 |

---

## Status

| Поле | Значение |
|------|----------|
| **Документ** | draft — G1, G2, G5 closed |
| **Open gaps** | G3, G4 (partial) |
| **STALE_RISK** | S1–S3 |
| **Active workflow tasks** | CONV-OPTIMIZATION-PHASE-1B (READY, not started) |

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
| S2 | Raccoon in docs, absent in code | `EXPERT_REVIEW.md` | low | DOCS_ONLY |
| S3 | Partner column spelling in conversion mapping | resolved — single source `main.py` `CONVERSION_COLUMNS` (`Партнёр`) | low | closed (E-CONFIG-03) |

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
| R-WE-07 | Concurrent Dropbox registry writes (multi-profile) | corrupt/missing rows in `wallet_editor.xlsx` | in-process `threading.Lock` in `wallet_editor_registry.py`; full download/upload per append |
| ~~R-TG-01~~ | ~~Silent Telegram sender failure (enqueue ≠ delivery)~~ | **mitigated** | Option B: health-state + periodic log in `telegram_bot.py` |

### Telegram delivery risks (R-TG-*)

| ID | Risk | Mitigation |
|----|------|------------|
| R-TG-02 | Dead sender worker thread (no auto-restart) | `queue_depth` + `last_success_age` in periodic health log; manual restart |
| R-TG-03 | Polling alive but outbound sender broken | Compare `/status` telegram_sender block vs scheduler; health logs |
| R-TG-04 | Business jobs commit state after enqueue (not delivery) | **open** — separate task; health does not fix state semantics |
| R-TG-05 | No external alert channel besides Railway logs | Railway log alert on `[TelegramSender/health] DEGRADED` |

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

Detail: `active_tasks/WALLET_EDITOR_WE-0-6_completed.md`

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
| **CONFIG-MIGRATION-PHASE-4D** | **OPEN** | Payout observation period — monitor prod logs for clean `[payout_config] source=rules_v2`; no `[config_shadow] payout mismatch`; gate YAML removal | CONFIG-MIGRATION-PHASE-4C complete |
| **CONV-OPTIMIZATION-PHASE-1B** | **READY** | Real dedup skip on fingerprint match — skip `conversion.run` when inputs unchanged | Collect 7–14 days observation data (`CONVERSION_FP_OBSERVATION_ENABLED=1`); GO/NO-GO from observation JSONL |

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
