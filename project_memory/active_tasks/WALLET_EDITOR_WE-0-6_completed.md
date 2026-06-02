# WALLET_EDITOR WE-0 … WE-6 — completed

| Мета | Значение |
|------|----------|
| **Status** | complete |
| **Closed** | 2026-06-01 |
| **Scope** | Integrate WalletEditor from Platform_2.0 standalone into analizis main runtime |

---

## Summary

WalletEditor больше не standalone-компонент. Production path: `scheduler.py` → single PTB polling → `integrations/wallet_editor_tg.py` → per-operator workers → `automation/engine.py`.

Legacy `automation/main.py` и `automation/tg_receiver.py` не используются в production.

---

## Deliverables by phase

| ID | Deliverable | Status |
|----|-------------|--------|
| WE-0 | Import-safe migration; `automation/` in repo | complete |
| WE-1 | Single polling integration (`wallet_editor_tg` + `tg_commands`) | complete |
| WE-2 | Isolated auth-state (evolved to per-profile in WE-5/WE-6) | complete |
| WE-3 | Isolated Antares credentials; no shared fallback | complete |
| WE-4 | `WALLET_EDITOR_ALLOWED_CHAT_IDS` allowlist | complete |
| WE-5 | Operator routing: `telegram_user_id` → profile credentials → `WalletEditorTask` | complete |
| WE-6 | Per-profile queue + worker; parallel across operators | complete |

Post-WE-6: result file naming `wallet_editor_result_<INPUT>_<OPERATOR>.xlsx`.

---

## KB cross-references

- Pipeline: `architecture_map.md` § **P-WE**
- Env / files: `contracts.md` § WalletEditor
- Decisions: `decisions.md` § **E-WE-01 … E-WE-06**
- Risks: `tasks.md` § **R-WE-***
- Current state: `current_state.md` § **S8 WalletEditor**

---

## Tests

`tests/unit/test_wallet_editor_*.py`, `automation/tests/` — 108+ tests passing at close.
