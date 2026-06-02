# Decisions — analizis

| Мета | Значение |
|------|----------|
| **KB версия** | v1.3 |
| **Последнее обновление** | 2026-06-02 |

---

## Status

| Поле | Значение |
|------|----------|
| **Документ** | draft — G1 + G2 + G5 data invariants |
| **Explicit decisions (E#)** | E1, E2, E4, E7, E9, **E-WE-01…E-WE-06**, **E-CONV-01…E-CONV-05** — CONFIRMED; E3, E5, E6, E8 — UNKNOWN |
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

### Conversion decisions (E-CONV-*)

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E-CONV-01 | 2026-06-02 | Conversion routing no longer depends on `analysis_map.yaml`; explicit routing in `selector.py` / pipeline | CONFIRMED | `analyzers/selector.py`; conversion YAML section deprecated |
| E-CONV-02 | 2026-06-02 | `run_conversion_pipeline` is the single source of truth for conversion lifecycle (download, fingerprint, run, move, observability) | CONFIRMED | `integrations/conversion_pipeline.py`; `downloader.py`; `main.py` delegation |
| E-CONV-03 | 2026-06-02 | Conversion observability via `event_log` + `state_store` (`jobs.conversion`); `/status` Conversion block | CONFIRMED | `conversion_pipeline.py`; `integrations/tg_commands.py` |
| E-CONV-04 | 2026-06-02 | Passive fingerprint rollout before real dedup skip — Phase 1A compute/compare/store only; Phase 1B skip gated separately | CONFIRMED | `integrations/conversion_fingerprint.py`; `CONVERSION_FINGERPRINT_ENABLED` |
| E-CONV-05 | 2026-06-02 | Phase 1B diagnostic observation in separate JSONL (`{STATE_DIR}/observability/conversion_fp_observation_*.jsonl`); flag `CONVERSION_FP_OBSERVATION_ENABLED` (default off); 14-day retention; best-effort; does not affect pipeline outcome | CONFIRMED | `observability/conversion_fp_observation.py`; hook in `conversion_pipeline.py` |
| E-INFRA-01 | 2026-06-02 | `STATE_DIR` default `/data/state` (Railway Volume); local persistent state (locks, events, observation JSONL) — no `/config/state` default | CONFIRMED | `job_runner.py`, `event_log.py`, `lock_status.py`, `conversion_fp_observation.py` |

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
| 2026-06-02 | Conversion E-CONV-01…E-CONV-05; I12 passive fingerprint invariant; I13 observation best-effort invariant |
