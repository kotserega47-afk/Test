# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-08 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-07 (PR #10), `ops/MODULAR_REORG_HANDLER_SPLIT.md` |
| **PR** | Draft [#11](https://github.com/deniskotdavydov1991-wq/Test/pull/11) `feat/task-2026-09-17-08-antares-dispatch-cmds`, base `feat/task-2026-09-17-07-antares-handlers` |
| **HEAD (проверен GPT)** | `7169cd488a084f0b90f6635b6888b264a4748550` |
| **Риск** | medium: владение двумя Antares dispatch-командами |

`cmd_operator_wallets_ready` и `cmd_wallet_editor_refresh` **перенесены**. В `modules.antares.handlers` **шесть** callbacks. Прежние ACL command / job_type, совместимые экспорты и mixed-сборка сохранены. Review пройден. Isolated entry и cutover **не** реализованы. PR #11 остаётся Draft.

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
- [x] GPT review HEAD `7169cd48…`: блокирующих нет
- [ ] merge/deploy PR #11 (намеренно открыто)

---

## Ограничения покрытия

Isolated Antares **не** готов. В mixed `tg_commands` остаются: `start`/`help`/`status`, Raccoon cmds, `run_script_hello`, Auto-Enable, registry health/replay/export, document ingest. SCRIPT_REGISTRY и исполнители jobs не переносились. `registry_replay` — другой путь, не этот PR.

Покрытие TASK-04/05/07 без изменения: нет полного эталона wallet file→DTO, payout-report, conversion Excel, raccoon daily conversion, Platform wallet-analyzer; регистрация jobs не доказывает production schedules.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **99 passed** на HEAD `7169cd48…`, Python **3.12.10** (`test_antares_handlers`, inventory, registration, WE refresh, operator_wallets_ready, script hello, raccoon cmds, profile boot) |
| GPT | Проверил код и diff HEAD `7169cd48…`. Набор 99 **независимо не запускал.** |

---

## Out Of Scope

SCRIPT_REGISTRY / script runtime; registry_replay; isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4–#11; Railway; профили.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Два Antares dispatch callback перенесены в `modules.antares.handlers` |
| 2026-09-17 | GPT review HEAD `7169cd48…`: блокирующих нет; merge/deploy нет |
