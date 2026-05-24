# Current State — `<PROJECT_NAME>`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Снимок на дату** | YYYY-MM-DD |
| **Среда** | prod \| staging \| local |

> **Как создать:** скопируйте в `project_memory/current_state.md`. Обновляйте после merge, меняющего runtime.

---

## Конфигурация deploy

| Поле | Значение | Статус |
|------|----------|--------|
| **Deploy target** | `<DEPLOYMENT_TARGET>` | CONFIRMED \| UNKNOWN |
| **Entry command** | `<RUN_COMMAND>` → `<PRIMARY_ENTRYPOINT>` | CONFIRMED |
| **Config files** | `<DEPLOY_CONFIG_FILE>` | |

---

## Capabilities (R#)

Краткий снимок возможностей системы. ID **R1, R2, …** — локальные для проекта.

| ID | Capability | Active in prod? | Статус KB |
|----|------------|-----------------|-----------|
| R1 | `<CAPABILITY_1>` | да / нет | CONFIRMED |
| R2 | `<CAPABILITY_2>` | | |
| R3 | … | | |

Подробности пайплайнов: `architecture_map.md` § Pipelines.

---

## Subsystems / modules (S#)

| ID | Subsystem | Runtime status | Статус KB |
|----|-----------|----------------|-----------|
| S1 | `<SUBSYSTEM_1>` | active \| dormant | CONFIRMED |
| S2 | `<SUBSYSTEM_2>` | | DORMANT |

---

## Integrations (operational)

| Integration | Works in prod? | Notes |
|-------------|----------------|-------|
| `<INTEGRATION_1>` | да / partial / no | |
| `<INTEGRATION_2>` | | |

---

## Tests (что существует)

| Тип | Где | Покрывает |
|-----|-----|-----------|
| Unit | `<TEST_PATH>/` | |
| Integration | | |
| Manual scripts | `<DEV_SCRIPT_1>`, … | pipeline `<PX>` |

Review не требует новых тестов, если out of scope задачи.

---

## Known operational limits

| Limit | Value / policy | Источник |
|-------|----------------|----------|
| Lock stale timeout | `<N>` sec | `contracts.md` |
| Job error policy | log + continue | `decisions.md` **E_** |

---

## DOCS_ONLY in prod

| Item | Почему не в prod |
|------|------------------|
| `<PLANNED_FEATURE>` | DOCS_ONLY — см. `architecture_map.md` |

---

## История

| Дата | Событие |
|------|---------|
| YYYY-MM-DD | Initial snapshot |
