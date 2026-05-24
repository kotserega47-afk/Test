# Architecture Map — `<PROJECT_NAME>`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Статус документа** | draft \| active |
| **Последнее обновление** | YYYY-MM-DD |

> **Как создать из шаблона:** скопируйте этот файл в `project_memory/architecture_map.md`, замените `<PLACEHOLDER>`, удалите блок «Как создать» при необходимости.

---

## Назначение

<!-- 5–15 строк: что делает система, для кого, основная ценность -->

`<PROJECT_NAME>` — `<ONE_LINE_PURPOSE>`.

Основные контуры (см. также `current_state.md` § Capabilities):

| ID | Контур | Статус |
|----|--------|--------|
| R1 | `<CAPABILITY_1_NAME>` | CONFIRMED \| DORMANT \| UNKNOWN |
| R2 | `<CAPABILITY_2_NAME>` | |
| R3 | … | |

---

## Prod entry & deploy

| Поле | Значение | Статус |
|------|----------|--------|
| **Primary entrypoint** | `<PRIMARY_ENTRYPOINT>` (например `main.py`, `cmd/server`) | CONFIRMED |
| **Deploy target** | `<DEPLOYMENT_TARGET>` (например Docker, K8s, PaaS) | CONFIRMED \| UNKNOWN |
| **Deploy config** | `<DEPLOY_CONFIG_FILE>` (например `Dockerfile`, `compose.yaml`) | |
| **Run command** | `<RUN_COMMAND>` | |

Ссылка на решение: `decisions.md` **E1** (если заведено).

---

## Runtime inventory

### Active modules

| Модуль / компонент | Роль | Статус KB |
|--------------------|------|-----------|
| `<MODULE_A>` | `<DESCRIPTION>` | CONFIRMED active |
| `<MODULE_B>` | | |

### Dormant modules

| Модуль | Почему dormant | Активация |
|--------|----------------|-----------|
| `<DORMANT_MODULE_1>` | `<REASON>` | только через `decisions.md` + Impact |

### DOCS_ONLY

| Документ / концепт | Статус | Комментарий |
|--------------------|--------|-------------|
| `<LEGACY_DOC_OR_PLANNED_FEATURE>` | DOCS_ONLY | не реализовано в prod |

---

## Pipelines

Определите **собственную** нумерацию пайплайнов проекта. ID вида **P1, P2, …** — локальные для этого репозитория, не универсальны.

### P1 — `<PIPELINE_1_NAME>`

| | |
|---|---|
| **Entry** | `<ENTRYPOINT_OR_TRIGGER>` |
| **Happy path** | 1. … 2. … 3. … |
| **Failure path** | … |
| **Side effects** | `<INTEGRATION_X>`, notifications, persistence |
| **Статус** | CONFIRMED \| UNKNOWN |

### P2 — `<PIPELINE_2_NAME>`

<!-- Повторить структуру для каждого пайплайна -->

---

## Background / scheduled work

Если есть фоновые процессы:

| Job / loop | Interval / trigger | Модули | Статус |
|------------|-------------------|--------|--------|
| `<BACKGROUND_JOB_1>` | | | CONFIRMED |

См. `decisions.md` для инвариантов (locks, restart policy).

---

## Integrations & subsystems

| ID | Интеграция / подсистема | Назначение | Статус |
|----|-------------------------|------------|--------|
| I1 | `<INTEGRATION_1>` | | CONFIRMED |
| I2 | `<SUBSYSTEM_2>` | | |

Не использовать чужие имена интеграций из других проектов — только ваши.

---

Детали интеграций — секция Integrations в этом файле и § `<INTEGRATION_1>` в `contracts.md`.

---

## Источники истины (data)

| Данные | Источник | Потребители |
|--------|----------|-------------|
| `<PROJECT_CONFIG_OR_RULES_FILE>` | file / DB / remote | `<MODULE_A>`, … |
| `<CONFIG_YAML>` | repo path | |

---

## Reading hints

- Для Task / Impact: секции **Pipelines** + **Runtime inventory**
- Для incident: **Pipelines** § failure path + `contracts.md`
- Состояние prod: `current_state.md` (не дублировать R# здесь длинными таблицами)

---

## История

| Дата | Событие |
|------|---------|
| YYYY-MM-DD | Документ создан из `architecture_map_template.md` |
