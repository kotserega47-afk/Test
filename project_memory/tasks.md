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
| S3 | Partner column spelling yaml vs main | `analysis_map.yaml` vs `main.py` | low | align or document |

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

---

## Open workflow tasks

| Task ID | Status | Goal (1 line) | Prerequisite |
|---------|--------|---------------|--------------|
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
