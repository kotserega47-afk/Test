# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-07-01-06 |
| **Статус** | ready |
| **Приоритет** | medium |
| **KB версия** | v1.9 |
| **Тип** | Documentation / KB / Ops only |
| **Depends on** | TASK-2026-07-01-01 … TASK-2026-07-01-05 (complete) |
| **Program** | WalletEditor Registry v2 — KB finalization + ops cutover |

---

## Goal

Привести Knowledge Base в состояние, полностью соответствующее финальной архитектуре WalletEditor Registry после миграции Phases 1–5.

**Без изменений runtime-кода, тестов и бизнес-логики.**

---

## Scope

| Артефакт | Действие |
|----------|----------|
| `current_state.md` | WalletEditor Registry — актуальная архитектура |
| `architecture_map.md` | Финальная схема P-WE |
| `contracts.md` | Manual workbook + export contracts |
| `decisions.md` | E-WE-23 … E-WE-27 |
| `tasks.md` | Закрыть TASK-01…05; активна только TASK-06 ops |
| `ops/WALLET_EDITOR_POSTGRES_CUTOVER.md` | Ops checklist (новый) |

---

## Success criteria

- [x] KB описывает только актуальную архитектуру (без migration narrative в hot paths)
- [x] ADR E-WE-23 … E-WE-27 добавлены
- [x] Ops checklist создан
- [x] Tasks 01–05 marked complete
- [x] Нет ссылок на excel projection / `registry_source=excel` rollback
- [ ] Ops cleanup в Dropbox выполнен оператором (`ops/WALLET_EDITOR_POSTGRES_CUTOVER.md`)

---

## Out of scope

- Выполнение ops cleanup в Dropbox (оператор вручную по checklist)
- Изменения кода / тестов

---

## История

| Дата | Событие |
|------|---------|
| 2026-07-01 | Task created + KB updated — final architecture documentation |
