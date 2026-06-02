# Contracts — `<PROJECT_NAME>`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Последнее обновление** | YYYY-MM-DD |

> **Как создать:** скопируйте в `project_memory/contracts.md`, заполните `<PLACEHOLDER>`. **Не храните значения секретов.**

---

Каждый контракт имеет статус: **CONFIRMED** | **UNKNOWN** | **STALE_RISK**

**Breaking change** — изменение, ломающее потребителей без миграции.

---

## Environment variables

| Variable | Назначение | Обязательна | Статус |
|----------|------------|-------------|--------|
| `<ENV_VAR_1>` | | да / нет | CONFIRMED |
| `<ENV_VAR_2>` | | | |

Значения env **не** записывать в KB — только имена и семантика.

---

## API / service contracts

### `<API_CONTRACT_1>`

| Поле | Значение |
|------|----------|
| **Signature** | `<FUNCTION_OR_ENDPOINT>` |
| **Input** | |
| **Output** | |
| **Consumers** | `<MODULE_A>`, … |
| **Breaking if** | |
| **Статус** | CONFIRMED |

### `<API_CONTRACT_2>`

<!-- Повторить -->

---

## Data contracts

### `<DATA_FORMAT_1>` (file / message / DTO)

| Поле | Правило | Breaking if |
|------|---------|-------------|
| `<FIELD_A>` | required, type … | removed / type change |
| `<FIELD_B>` | optional | |

### `<PROJECT_CONFIG_OR_RULES_FILE>`

| | |
|---|---|
| **Path / source** | `<PATH_OR_URI>` |
| **Format** | xlsx / yaml / json / … |
| **Sheets / sections** | `<SECTION_1>`, `<SECTION_2>` |
| **Consumers** | |
| **Статус** | CONFIRMED |

---

## Integration contracts

### `<INTEGRATION_1>`

| | |
|---|---|
| **Protocol** | HTTP / queue / file sync / … |
| **Auth** | env `<ENV_VAR>`, не значение |
| **Failure behavior** | retry / fail-safe / alert |
| **Статус** | CONFIRMED \| UNKNOWN |

### `<NOTIFICATION_CHANNEL>`

| | |
|---|---|
| **Contract** | `<SEND_FN>(payload, target_id)` — `target_id` обязателен |
| **High risk if** | вызов без `target_id` / wrong channel |
| **Статус** | CONFIRMED |

---

## Concurrency & runtime

| Contract | Rule | Path / mechanism | Статус |
|----------|------|------------------|--------|
| Pipeline lock | один writer на ресурс | `<PROJECT_LOCK_PATH>` | CONFIRMED |
| Temp / cache | TTL, cleanup policy | `<CACHE_PATH>` | |
| Auth session | persist / refresh | `<AUTH_STATE_PATH>` | |

См. `decisions.md` **E_** / **I_** для связанных инвариантов.

---

## Scheduler / background (если применимо)

| Element | Default | Contract |
|---------|---------|----------|
| `<BACKGROUND_JOB>` | interval `<N>` | must not overlap without lock |
| Error in job | log + continue \| fail | см. decisions **E_** |

---

## STALE_RISK register (contracts)

| ID | Расхождение | Где проверить |
|----|-------------|---------------|
| S_ | `<DESCRIPTION>` | code vs KB |

Полный список STALE_RISK также в `tasks.md`.

---

## История

| Дата | Событие |
|------|---------|
| YYYY-MM-DD | Создан из `contracts_template.md` |
