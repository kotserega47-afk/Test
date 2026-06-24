# TASK-2026-06-24-01 — WalletEditor Registry Mirror Observation & Reconcile

| Поле         | Значение                    |
| ------------ | --------------------------- |
| Status       | **in_progress** (code done; observation pending) |
| Priority     | medium                      |
| Impact       | required                    |
| Depends On   | TASK-2026-06-23-01          |
| Phase        | Postgres Mirror Observation |

## Implementation (code complete)

### Reconcile utility
- `integrations/wallet_editor_registry_db/reconcile.py`
- CLI: `python tools/reconcile_wallet_editor_registry.py path/to/wallet_editor.xlsx`
- Dropbox: `--dropbox`
- Persist drift: `--record` → `we_registry_reconcile` + `we_registry_meta`
- JSON summary: `--json`

### Drift criteria
| Status  | Condition |
|---------|-----------|
| **OK**  | zero issues |
| **WARNING** | ≤3 isolated issues (missing rows, content mismatch, duplicates) |
| **ERROR** | >3 Excel rows missing in DB, or ≥5% / ≥10 mass mismatch |

### Mirror health (extended)
`/registry_health` now includes:
- mirror enabled
- last success / last failure
- last operation
- recent failures (since last success)
- failure count (process lifetime)
- last error

### Invariants preserved
- I-OBS-01: Excel = source of truth
- I-OBS-02: no DB reads in runtime (`build_registry_health_report` unchanged)
- I-OBS-03…05: observation tooling only; no operator/replay/lifecycle changes

## Ops runbook

```bash
# After mirror enabled in production:
python tools/reconcile_wallet_editor_registry.py --dropbox --record --json
```

Schedule daily (cron / Railway job). Target: **≥7 days** with status OK or isolated WARNING only.

## Observation report template

Fill after observation period:

| Metric | Value |
|--------|-------|
| Observation period | YYYY-MM-DD → YYYY-MM-DD |
| Mirror enabled | yes/no |
| Reconcile runs | N |
| OK / WARNING / ERROR counts | / / |
| Mirror write failures (`failure_count` peak) | |
| Drift events recorded (`we_registry_reconcile`) | |
| Root causes | |

**Decision:** GO / NO-GO for Phase 2 (PostgreSQL source of truth)

## Success criteria checklist

- [x] Reconcile utility реализована
- [x] Health visibility расширена
- [x] Drift detection реализован
- [ ] Несколько дней production observation завершены
- [ ] Нет критических расхождений
- [ ] Подготовлен итоговый observation report
- [ ] GO / NO-GO для Phase 2

## Tests

`tests/unit/test_wallet_editor_registry_db_reconcile.py` — 13 tests  
Full registry DB suite: **127 passed**
