# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-08 |
| **Статус** | in_progress (Draft PR на review; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-07 (PR #10), `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft, base `feat/task-2026-09-17-07-antares-handlers` |
| **Риск** | medium: владение двумя Antares dispatch-командами |

В `modules.antares.handlers` теперь **шесть** callbacks. Isolated entry и cutover **не** реализованы.

---

## Goal

`cmd_operator_wallets_ready` и `cmd_wallet_editor_refresh` живут в `modules.antares.handlers` через `_run_antares_command` и существующие `bind_rules` / `bind_logger`. Mixed re-export тех же function objects.

---

## Граница изменения

Перенесено: только два callback.

Не перенесено: script_jobs framework, registry refresh job, registry_replay, help/status, Raccoon, остальные WE, isolated entry.

Совместимые пути: `integrations.tg_commands.cmd_operator_wallets_ready` и `cmd_wallet_editor_refresh`.

---

## Success Criteria

- [x] ACL `operator_wallets_ready` → `script_job:operator_wallets_ready`
- [x] ACL `wallet_editor_refresh` → `wallet_editor_registry_refresh`
- [x] Identity re-export + шесть различимых callbacks
- [x] Mixed expected JSON без изменений
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

SCRIPT_REGISTRY / script runtime; registry_replay; isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#10; Railway; профили.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Два Antares dispatch callback перенесены в `modules.antares.handlers` |
