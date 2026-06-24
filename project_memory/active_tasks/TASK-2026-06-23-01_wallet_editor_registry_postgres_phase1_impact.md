# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-2026-06-23-01 |
| **Связанная задача** | TASK-2026-06-23-01 |
| **KB версия** | v1.7 |
| **Триггер** | P-WE pipeline, new persistence, medium risk |

---

## Current Runtime Behavior

**Entry:** `railway.toml` → `scheduler.py` (E1, E2).

**WalletEditor (S8, R6):** ACTIVE — Telegram Excel ingest → per-operator worker queue → Antares; cumulative registry in Dropbox xlsx.

**Registry write model:** download/upload with Dropbox rev; in-process lock; async append after TG delivery; STATE_DIR outbox + durable results (E-WE-20).

**Postgres today:** absent in codebase (`tasks.md` WE-POSTGRES-HISTORY = OPTIONAL future).

---

## Runtime Paths

| Путь | Entry | Модули | Изменяется? |
|------|-------|--------|-------------|
| Manual disable | TG document → worker | `worker._run_disable_task` → `registry_async` → `registry.append` | **hook only** after Excel SUCCESS |
| Conversion → WE | `conversion_wallet_editor_bridge` → `add_task` | same disable path | **hook only** |
| Auto-enable plan/run | `/auto_enable_*` | `auto_enable.load_registry_frames_for_planning` | **нет** |
| Auto-enable B2 patch | orchestrator | `patch_enable_results_in_dropbox_registry` | **hook only** after Excel SUCCESS |
| Lifecycle refresh | job / `/wallet_editor_refresh` | `registry_refresh` | **hook only** after upload SUCCESS |
| Registry replay | `/registry_replay` | `replay_pending_outbox_records` → append | **hook only** (same append hook) |
| Registry health | `/registry_health` | `build_registry_health_report` | **extend** output (mirror section) |
| Hold check | engine / auto-enable executor | `wallet_editor_hold` | **нет** |
| add_wallet / edit_wallet | worker | no registry | **нет** |

---

## Contracts Impact

| Контракт | Тип изменения | Breaking | Потребители |
|----------|---------------|----------|-------------|
| Dropbox `wallet_editor.xlsx` | none (SoT) | **нет** | all registry readers |
| Outbox `index.json` | none | **нет** | replay, health |
| `registry_processed_run_ids.json` | none | **нет** | append idempotency |
| `DROPBOX_WALLET_EDITOR_PATH` | none | **нет** | registry module |
| `DATABASE_URL` | **additive env** | **нет** (optional) | new db module only |
| `WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED` | **additive env** | **нет** | mirror hooks |
| Fingerprint algorithm | reuse unchanged | **нет** | dedup, reconcile |

STALE_RISK: **S4** not affected. **R-WE-07** — mirror must use same fingerprint UNIQUE constraint.

---

## Pipeline Impact

| P# | Happy path | Failure path | Побочные эффекты |
|----|------------|--------------|------------------|
| **P-WE** disable | engine.run → TG → outbox → Excel append → **+mirror** | Excel fail unchanged; mirror fail → log/event only | extra latency **after** Excel success (async mirror recommended) |
| **P-WE** auto-enable B2 | Antares → Excel patch → **+mirror** | patch fail unchanged; mirror fail logged | none on Antares rollback policy |
| P-WE refresh | recalc → conditional Excel upload → **+mirror on upload** | unchanged | none |
| P3 conversion→WE | bridge queues disable | unchanged | indirect mirror via append |

**Observed behavior for operators:** unchanged when flag off or mirror fails.

---

## Integration Impact

| Интеграция | Поведение сейчас | Влияние |
|------------|------------------|---------|
| **Dropbox** | SoT read/write | unchanged; still primary |
| **Railway Postgres** | not used | **new** optional dependency when flag=1 |
| **Telegram** | results + warnings | unchanged; optional mirror drift in `/registry_health` |
| **Antares / Playwright** | disable/auto-enable | **нет** |
| **Rules V2** | registry timeouts via `job_params` | unchanged |

---

## Cache Impact

| Артефакт | Путь | Влияние |
|----------|------|---------|
| STATE_DIR outbox | `/data/state/wallet_editor/outbox/` | **нет** |
| Durable results | `.../results/{run_id}.xlsx` | **нет** |
| processed_run_ids | `.../registry_processed_run_ids.json` | **нет** |
| In-memory lock | `wallet_editor_registry._lock` | mirror hook should run **inside** or **immediately after** locked Excel success to avoid race |

Fail-safe: mirror errors must not corrupt STATE_DIR files (I-REG-03).

---

## Background / Scheduler Impact

| Элемент | Default | Влияние |
|---------|---------|---------|
| `wallet_editor_registry_refresh` | scheduled | +mirror on successful upload; optional reconcile job (new) |
| `wallet_editor_registry_replay` | manual/scheduled | append hook fires mirror |
| Scheduler error policy | log + continue (E7) | mirror exceptions swallowed |
| Worker threads | per-profile queue | **нет** change to queue semantics |

**Recommendation:** run mirror write in **daemon thread** after Excel success (same pattern as registry append) to avoid blocking lock hold time.

---

## Data Impact

| Данные | Источник истины | Изменение |
|--------|-----------------|-----------|
| `all_results` / `runs` | Dropbox xlsx | **none** (SoT) |
| `hold` / `Отлёжка` | Excel operator edits | **not mirrored** Phase 1 |
| Postgres tables | **new mirror** | additive; populated by import + shadow-write |
| `we_registry_reconcile` | new audit | drift records only |

DORMANT: `analyzers/transactions.py` — not activated.

---

## Regression Risks

| # | Риск | Область | Вероятность | Обнаружение |
|---|------|---------|-------------|-------------|
| 1 | Mirror exception breaks append success path | P-WE | med | unit: mirror raises → append still SUCCESS |
| 1b | **Excel FAIL + Postgres SUCCESS** (I-REG-08 violation) | P-WE | med | unit: upload fails → mirror never called; integration ordering test |
| 2 | Lock held longer → rev conflict spike | registry | low | integration timing; async mirror |
| 3 | Duplicate mirror rows on replay | replay | med | UNIQUE fingerprint tests |
| 4 | Health report regression | tg_commands | low | existing health tests + snapshot |
| 5 | Flag=1 without DATABASE_URL → import-time crash | deploy | med | lazy connect; flag check first |
| 6 | Reconcile false positives (hold/otlezka not mirrored) | ops | med | scope reconcile to results/runs only |

---

## Rollback Strategy

| Уровень | Действие | Условие |
|---------|----------|---------|
| **Config** | `WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED=0` | immediate; zero Postgres calls |
| **Code** | revert deploy | if hook causes regression |
| **Data** | truncate Postgres mirror tables OR ignore DB | Excel remains SoT |
| **Runtime** | restart `scheduler.py` | standard Railway redeploy |

No Excel rollback needed — SoT unchanged.

---

## Вердикт impact

| Поле | Значение |
|------|----------|
| **Уровень риска** | **medium** |
| **Рекомендация** | **proceed with caution** |

**Условия proceed:**

1. Railway Postgres provisioned before Stage C
2. Mirror hooks are **best-effort async** after Excel SUCCESS
3. Reconcile limited to `all_results` + `runs` (not hold/otlezka)
4. All I-REG-01…08 verified in Review (I-REG-08: mirror strictly post-Excel-upload-success)

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-06-23 | GPT Architect | Impact created from discovery + Task draft |
