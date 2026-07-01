# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-07-01-02 |
| **Статус** | ready |
| **Приоритет** | medium |
| **KB версия** | v1.8 |
| **Связанные артефакты** | `TASK-2026-07-01-02_wallet_editor_manual_sync_prerun_gate_impact.md`, `CP-dev_task-TASK-2026-07-01-02-20260701.md`, `TASK-2026-07-01-01_wallet_editor_manual_sync_phase1.md` |
| **Impact** | required — см. `_impact.md` |
| **Context Pack** | `CP-dev_task-TASK-2026-07-01-02-20260701.md` |
| **Depends on** | TASK-2026-07-01-01 (Phase 1 library + schema v2 + tests) |
| **Program** | WalletEditor Registry v2 — manual Excel → PG sync → PG-only runtime |

---

## Goal

Подключить **mandatory pre-run manual snapshot gate** к опасным WalletEditor-операциям: перед стартом вызывать `ensure_manual_snapshot_current()`, при успехе фиксировать **RunSnapshotBinding** (I-MAN-10) на весь запуск, при failure — блокировать операцию без Playwright и с понятным сообщением оператору в Telegram.

**Phase 2 (этот Task):** gate + binding + `/registry_health` manual block + tests. **Не** переводит readers на PostgreSQL и **не** меняет export/Dropbox registry writes.

---

## Business Context

Phase 1 заложил sync `hold` / `Отлёжка` из `DROPBOX_WALLET_EDITOR_PATH` в PostgreSQL с rev/hash skip. Без gate оператор может изменить Excel в Dropbox, а runtime продолжит читать устаревшие данные из полного workbook download — drift между manual input и enforcement (hold, lifecycle, auto-enable eligibility).

Операторы и ops должны:

1. видеть, что опасная операция **не стартует**, пока manual snapshot не синхронизирован;
2. работать в рамках **одного зафиксированного snapshot** на весь run (mid-run Excel edits → только следующий run);
3. диагностировать sync через `/registry_health` (manual block).

Контур: **P-WE**, **R6**. Потребители: disable, add_partner (hold), auto-enable, registry replay/refresh.

---

## Current Behavior (post Phase 1)

| Aspect | Сейчас |
|--------|--------|
| Manual sync library | `ensure_manual_snapshot_current()`, `RunSnapshotBinding`, health builders — **реализовано**, не подключено к runtime |
| Feature flag | `WALLET_EDITOR_MANUAL_SYNC_ENABLED=0` (default) — gate **не активен** |
| Disable / add_partner | TG ingest → `wallet_editor_tg.py` → worker queue → `engine.run()`; hold read из Dropbox в engine |
| add_wallet | routing + queue; hold check при `add_partner`-подобных сценариях — **не** для чистого add_wallet |
| edit_wallet | `edit_wallet_engine` — **без** hold/Отлёжка dependency |
| Auto-enable plan/run | `wallet_editor_auto_enable.py` → `load_registry_frames_for_planning()` (Dropbox full workbook) |
| Registry refresh | `wallet_editor_registry_refresh.py` → Dropbox download + `recalculate_all_results()` |
| Registry replay | `wallet_editor_registry.py` outbox replay path / `tg_commands.py` `/registry_replay` |
| `/registry_health` | `build_registry_health_report()` — outbox, mirror, postgres; **без** manual snapshot block |
| Snapshot isolation | **нет** — mid-run Excel может повлиять на повторные Dropbox reads в том же run |

KB: `contracts.md` § Wallet Editor registry; TASK-2026-07-01-01; E-WE-14, E-WE-21.

---

## Desired Behavior

### Pre-run gate (mandatory when flag on)

**Architecture (I-MAN-10):** authoritative snapshot binding is captured at **execution start**, not at Telegram enqueue. Tasks may wait in the per-profile worker queue; gating before enqueue would allow manual Excel edits during queue wait and violate snapshot consistency.

**Correct flow (disable / queued WalletEditor tasks):**

```text
Telegram ingest → enqueue task → worker picks task
→ run_manual_sync_prerun_gate() → capture RunSnapshotBinding
→ start Playwright / runtime operation
```

**Telegram ingest** may perform lightweight validation (routing, allowlist, file type) but **must not** capture authoritative `RunSnapshotBinding`.

Перед стартом **gated operation** (at execution boundary):

```text
run_manual_sync_prerun_gate(...) → ensure_manual_snapshot_current(...)
```

| Gate result | Поведение |
|-------------|-----------|
| `SKIPPED_DISABLED` | Legacy path (flag `0`); log bypass |
| `SKIPPED_REV` / `SKIPPED_HASH` / `SYNCED` | **OK** — операция продолжается |
| `FAILED` (`blocked=True`) | Операция **не стартует**; Playwright **не** вызывается; TG сообщение оператору; error logged; last good PG **не** портится (I-MAN-01) |

При gate OK run получает binding:

| Field | Source |
|-------|--------|
| `manual_sync_run_id` | `RunSnapshotBinding.manual_sync_run_id` |
| `manual_snapshot_hash` | `RunSnapshotBinding.manual_snapshot_hash` |
| `manual_snapshot_synced_at` | `RunSnapshotBinding.manual_snapshot_synced_at` |

Binding передаётся в task/job context и **не перечитывается** до конца run (I-MAN-10).

Binding передаётся в run context при **execution start** и **не перечитывается** до конца run (I-MAN-10). The run must use the captured binding for its entire execution.

### Gated operations (обязательный gate при `ENABLED=1`)

| # | Operation | Gate location |
|---|-----------|---------------|
| 1 | **Disable flow** | `automation/worker.py` immediately before `_run_disable_task` execution |
| 2 | **Add Wallet / add_partner if hold-relevant** | `automation/worker.py` immediately before add-wallet execution **if** task requires hold |
| 3 | **Auto-enable plan** | Start of `run_auto_enable_plan()` (command/job — no worker queue) |
| 4 | **Auto-enable run** | Start of `run_auto_enable()` (command/job — before plan/load) |
| 5 | **Registry replay** | Start of replay command/job before mutations |
| 6 | **Lifecycle refresh** | Job start before recalculation (`refresh_wallet_editor_registry_lifecycle`) |

**Not gated Phase 2:**

| Operation | Rationale |
|-----------|-----------|
| **edit_wallet** | Не зависит от hold/Отлёжка |
| **add_wallet** (create-only, no hold) | Нет hold enforcement |
| **remove_partner-only disable** | Hold не блокирует; **но disable flow в целом gated** как единый ingest path для `WalletEditorTask` |

> **Clarification:** disable flow gated целиком (включая remove_partner rows) — sync нужен для lifecycle consistency и единообразия ops; hold check внутри run по-прежнему только для `add_partner`.

### Snapshot isolation (I-MAN-10 — wire in Phase 2)

For one WalletEditor run:

- authoritative binding captured at **worker execution start** (queued tasks) or **job/command start** (sync jobs);
- **forbidden** to call `ensure_manual_snapshot_current()` again or re-bind during the same run;
- mid-run Excel edits apply only to the next run.

Implementation: log and retain `RunSnapshotBinding` on the execution path; optional field on task dataclasses for downstream audit.

### Feature flag

| Env | Default | Behavior |
|-----|---------|----------|
| `WALLET_EDITOR_MANUAL_SYNC_ENABLED` | `0` | Gate bypass; legacy behavior; log `[ManualSync] skipped: disabled` |
| `1` | — | Gate active for § Gated operations |

Emergency bypass: D8 — explicit flag off + logged (не silent).

### Telegram operator messaging (gate failed)

```text
WalletEditor не запущен: не удалось синхронизировать hold/Отлёжка.

Причина: <error>

Проверьте Dropbox-книгу и повторите запуск.
```

- Использовать `ManualSyncResult.error` или нормализованный текст;
- Для `SKIPPED_REV` / `SKIPPED_HASH` — **не** спамить оператору (достаточно logs).

### `/registry_health` — manual snapshot block

Подключить Phase 1 library:

- `build_manual_snapshot_health_report()`
- `format_manual_snapshot_health_report()`

**Не менять** смысл существующих блоков (outbox, mirror, postgres).

Новый блок **дополнительно** показывает:

| Field | Notes |
|-------|-------|
| manual sync enabled/disabled | from `manual_sync_enabled()` |
| current Dropbox rev | live `get_dropbox_file_rev` |
| last synced rev | meta |
| snapshot hash short | first 12 chars |
| last successful sync time | ISO |
| snapshot age | seconds |
| active hold rows | PG count |
| active Отлёжка rows | PG count |
| last status | success / failed / skipped_* |
| stale / failed | booleans |

---

## Suggested implementation architecture

### New / extended helper (recommended)

Централизовать в `integrations/wallet_editor_registry_db/manual_sync.py` (или `manual_sync_gate.py`):

```python
def run_manual_sync_prerun_gate(
    *,
    triggered_by: str,
    actor: str | None = None,
) -> tuple[bool, RunSnapshotBinding | None, str | None]:
    """Returns (ok, binding, operator_error_message)."""
```

- If disabled → `(True, None, None)` immediately;
- If failed → `(False, None, formatted_message)`;
- If OK → `(True, binding, None)`.

### Binding propagation

| Artifact | Change |
|----------|--------|
| `WalletEditorTask` | optional `manual_snapshot: RunSnapshotBinding \| None` |
| `WalletEditorAutoEnableBatchTask` | same (or parent plan context) |
| Refresh/replay job | thread binding through job-local context / function arg |
| Outbox / run metadata | log binding fields on gated runs (optional Phase 2 — no schema migration required) |

### Files (expected touch)

| File | Action |
|------|--------|
| `integrations/wallet_editor_registry_db/manual_sync.py` | `run_manual_sync_prerun_gate()`, message formatter |
| `automation/runtime.py` | optional binding fields on task dataclasses |
| `integrations/wallet_editor_tg.py` | ingest only — **no authoritative gate** |
| `automation/worker.py` | gate at execution start; binding + logs; block Playwright on failure |
| `integrations/wallet_editor_auto_enable.py` | gate plan + execute |
| `integrations/wallet_editor_registry_refresh.py` | gate refresh job |
| `integrations/wallet_editor_registry.py` | gate replay job entry |
| `integrations/tg_commands.py` | `/registry_health` append manual block; gate failure messages for TG commands |
| `integrations/wallet_editor_registry.py` | `build_registry_health_report()` compose manual section |
| **new** `tests/unit/test_wallet_editor_manual_sync_gate.py` | gate matrix tests |
| existing `tests/unit/test_wallet_editor_*.py` | update mocks where enqueue/worker touched |

---

## Constraints

- **I-MAN-01:** Sync validation failure → no PG mutation (Phase 1; preserve in gate).
- **I-MAN-03 / I-MAN-10:** Mid-run Excel must not change in-flight snapshot — **wire in Phase 2**.
- **I-MAN-04:** Do not activate DORMANT (`analyzers/transactions.py`).
- **I-MAN-05:** **No** Playwright/Antares logic changes.
- **I-MAN-06:** **No** removal of Dropbox registry write path / `schedule_excel_export`.
- **I-MAN-08:** Manual sync reads only `hold` + `Отлёжка`.
- **I-MAN-09:** No Dropbox sheet cleanup.
- **Phase 2 scope fence:** readers (`wallet_editor_hold.py`, `load_registry_frames_for_planning`, lifecycle DataFrames) **остаются на Dropbox download** — gate syncs PG, но runtime enforcement still reads Excel until Phase 3.

---

## Affected Modules

| Модуль | Действие Phase 2 |
|--------|------------------|
| `manual_sync.py` | gate helper + operator message |
| `manual_sync_state.py` | health wiring (read-only reuse) |
| `wallet_editor_tg.py` | ingest only — no authoritative binding |
| `automation/worker.py` | gate before disable / hold-relevant add-wallet execution |
| `wallet_editor_auto_enable.py` | gate plan/run |
| `wallet_editor_registry_refresh.py` | gate refresh |
| `wallet_editor_registry.py` | gate replay + health compose |
| `tg_commands.py` | health output; command-level failure replies |
| `automation/runtime.py` | optional binding fields |
| `wallet_editor_hold.py` | **не менять** read path (still Dropbox) |
| `excel_export.py` | **не трогать** |

---

## Affected Pipelines

| ID | Phase 2 change |
|----|----------------|
| **P-WE** | Pre-run sync latency on rev change; fail-closed on structural Excel errors when flag on |
| P3 → WE | **нет** (conversion bridge unchanged) |

---

## Affected Contracts

| Контракт | Breaking? | Phase 2 |
|----------|-----------|---------|
| `WALLET_EDITOR_MANUAL_SYNC_ENABLED` | additive | `1` enables gate |
| Gated TG flows | behavior when flag on | fail-closed on sync error |
| `/registry_health` | additive block | manual snapshot section |
| `hold`/`Отлёжка` read SoT | **unchanged** | still Dropbox in readers — PG synced but not yet read |

---

## Known Risks

| ID | Risk | Mitigation |
|----|------|------------|
| R-P2-01 | Dropbox outage blocks all gated ops | D8 bypass flag; ops runbook |
| R-P2-02 | Gate OK but readers still use Dropbox — transient drift if PG≠Excel read path | Phase 3 reader migration; document in deliverable |
| R-P2-03 | Double gate (TG + worker) | single helper; test idempotency |
| R-P2-04 | Auto-enable multi-batch re-sync | binding at plan/run start only |
| R-P2-05 | Refresh/replay long jobs vs rev drift | binding at job start; no mid-job re-sync |

---

## Success Criteria

### Phase 2 (this Task)

- [x] `TASK-2026-07-01-02_impact.md` verdict `proceed` or `proceed with caution`
- [x] CP created at `ready`
- [x] `run_manual_sync_prerun_gate()` centralizes gate logic
- [x] All § Gated operations call gate when `ENABLED=1` (worker execution start for queued tasks)
- [x] Gate failure: no Playwright, clear TG message, logged error
- [x] `RunSnapshotBinding` attached to gated runs (I-MAN-10)
- [x] No mid-run re-sync in gated flows
- [x] `/registry_health` includes manual snapshot block
- [x] `edit_wallet` / non-hold `add_wallet` **not** gated
- [x] All § Required tests pass
- [x] `WALLET_EDITOR_MANUAL_SYNC_ENABLED=0` default — no behavior change
- [x] No reader migration to PG; no export/Dropbox write changes

---

## Required tests (Phase 2)

| # | Test | Assert |
|---|------|--------|
| 1 | flag disabled | operation starts; `ensure_manual_snapshot_current` not called |
| 2 | flag enabled + sync OK | operation starts; binding present |
| 3 | flag enabled + sync failed | operation blocked |
| 4 | sync failed | Playwright engine **not** called |
| 5 | disable task | receives/records `RunSnapshotBinding` |
| 6 | auto-enable plan/run | gated |
| 7 | registry replay | gated |
| 8 | lifecycle refresh | gated |
| 9 | edit_wallet | **not** gated |
| 10 | `/registry_health` | includes manual snapshot block |
| 11 | snapshot isolation | one run uses binding from start; no second sync in run |
| 12 | TG failure message | human-readable Russian template |

**Run:**

```bash
pytest tests/unit/test_wallet_editor_manual_sync_gate.py tests/unit/test_wallet_editor_manual_sync.py -q
```

Plus targeted updates in existing `test_wallet_editor_*` as needed.

---

## Out Of Scope (Phase 2)

- Reader migration: `wallet_editor_hold.py`, `load_registry_frames_for_planning`, lifecycle hold/otlezka DataFrames from PG
- `/registry_sync_manual` command
- `/registry_export` TG-only (no Dropbox upload)
- Removal of `schedule_excel_export` / legacy Dropbox registry append
- Dropbox workbook cleanup (`all_results`/`runs` deletion)
- Playwright / Antares changes
- Production cutover (`ENABLED=1` on Railway) — ops decision after merge
- KB update (`contracts.md`, `decisions.md` E-WE-23) — post-program cutover

---

## Operations still reading Dropbox after Phase 2

(Explicit deliverable for acceptance — **expected**, not a defect)

| Consumer | What it reads from Dropbox workbook |
|----------|--------------------------------------|
| `wallet_editor_hold.load_hold_pairs_from_dropbox()` | `hold` sheet |
| `wallet_editor_auto_enable.load_registry_frames_for_planning()` | full workbook (`all_results`, `runs`, `hold`, `Отлёжка`) |
| `wallet_editor_registry_refresh` / `recalculate_all_results` | `hold`, `Отлёжка` (+ history frames) |
| `engine.run` hold enforcement | via hold loader (Dropbox) |
| `excel_export` / registry append projection | full workbook upload path |
| Manual sync gate | `hold` + `Отлёжка` only → **PG** |

**Phase 3** (TASK-2026-07-01-03): migrate readers to PG; gate ensures PG fresh before run.

---

## Follow-on (Phase 3+ recommendation)

| ID | Scope |
|----|-------|
| TASK-2026-07-01-03 | Reader migration — hold, auto-enable planning, lifecycle from PG |
| TASK-2026-07-01-04 | `/registry_export` TG-only + `/registry_sync_manual` |
| TASK-2026-07-01-05 | Deprecate combined Dropbox registry writes |
| TASK-2026-07-01-06 | Workbook cleanup (backup, PG verify, operator approval) |

**Phase 3 priority:** after Phase 2 prod validation with `ENABLED=1`, switch `wallet_editor_hold` + planning reads to PG so gate sync and enforcement use same SoT.

---

## Workflow decisions

| Decision | Value |
|----------|-------|
| Impact required? | **yes** (P-WE hot path, fail-closed, TG UX) |
| Context Pack required? | **yes** at `ready` |
| Status target after review | `ready` → implementation |

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Task created from Phase 1 completion + Initiator spec |
| 2026-07-01 | Initiator + Cursor | I-MAN-10 fix: gate at worker execution start, not TG enqueue; status → `ready`; Phase 2 implemented |
