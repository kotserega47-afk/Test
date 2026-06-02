# Decisions — `<PROJECT_NAME>`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Последнее обновление** | YYYY-MM-DD |

> **Как создать:** скопируйте в `project_memory/decisions.md`.  
> **E_** — explicit decisions (архитектура, prod). **I_** — implicit invariants (из кода/ops, зафиксированные).

---

## Explicit decisions (E#)

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E1 | YYYY-MM-DD | Prod entry = `<PRIMARY_ENTRYPOINT>` | CONFIRMED | ops / code |
| E2 | | Deploy on `<DEPLOYMENT_TARGET>` | CONFIRMED \| UNKNOWN | |
| E3 | | `<DECISION_TEXT>` | CONFIRMED | |
| E4 | | Pipeline lock at `<PROJECT_LOCK_PATH>` | CONFIRMED | |
| E5 | | `<NOTIFICATION_CHANNEL>` requires explicit target id | CONFIRMED | |
| E6 | | Config fail-safe: last known good on sync error | CONFIRMED \| N/A | |
| E7 | | Background job errors: log + continue (not crash process) | CONFIRMED \| N/A | |
| E8 | | Process restart policy: `<RESTART_POLICY>` | CONFIRMED \| UNKNOWN | |

Добавляйте **E9, E10, …** по мере принятия решений. Не копируйте ID из других проектов без смысла.

---

## Implicit invariants (I#)

| ID | Инвариант | Нарушение = | Статус |
|----|-----------|-------------|--------|
| I1 | Один pipeline run не держит lock дольше `<N>`s | deadlock / skip | CONFIRMED |
| I2 | Side effects только через documented integrations | hidden coupling | CONFIRMED |
| I3 | DORMANT modules не активируются без E# record | scope creep | CONFIRMED |
| I4 | DOCS_ONLY не деплоится как prod | false expectations | CONFIRMED |

---

## DORMANT activation log

| Module | Decision | Date | Task |
|--------|----------|------|------|
| `<DORMANT_MODULE>` | pending \| activated **E#** | | TASK-… |

---

## Отменённые / superseded

| ID | Было | Заменено на | Дата |
|----|------|-------------|------|
| | | | |

---

## История

| Дата | Событие |
|------|---------|
| YYYY-MM-DD | Создан из `decisions_template.md` |
