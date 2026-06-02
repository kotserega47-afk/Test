# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-YYYY-MM-DD-NN |
| **Связанная задача** | TASK-… |
| **KB версия** | v1.1 |
| **Триггер** | Задача `ready` с затронутыми пайплайнами/контрактами или риском ≠ low |

> Для каждого поля: «Поле» + «Справка» (назначение / когда / кем).

---

## Current Runtime Behavior

### Поле

<!-- Сводка prod-поведения по KB до изменения: entry points, активные R#, релевантные failure paths -->

Entry: `<DEPLOY_CONFIG_FILE>` → `<PRIMARY_ENTRYPOINT>` (см. `decisions.md` E#).  
Активные capabilities: `current_state.md` R#.

### Справка

| | |
|---|---|
| **Назначение** | Baseline runtime для сравнения «до/после»; только **CONFIRMED** из KB, не предположения. |
| **Когда заполняется** | Первым шагом impact; до оценки Pipeline/Contracts Impact. |
| **Кем заполняется** | **Архитектор или исполнитель** по `current_state.md` + `architecture_map.md`. |

---

## Runtime Paths

### Поле

| Путь | Entry | Модули (KB) | Изменяется? |
|------|-------|-------------|-------------|
| | `<PRIMARY_ENTRYPOINT>` / CLI / worker / … | | да / нет |

Пайплайны: `<PX>` — happy + failure (таблицы в `architecture_map.md`).

### Справка

| | |
|---|---|
| **Назначение** | Перечисляет *какие цепочки вызовов* затронуты, а не только файлы. |
| **Когда заполняется** | Сразу после Current Runtime Behavior. |
| **Кем заполняется** | **Исполнитель/архитектор** по `architecture_map.md`. |

---

## Contracts Impact

### Поле

| Контракт | Тип изменения | Breaking | Потребители (KB) |
|----------|---------------|----------|------------------|
| `<API_CONTRACT_1>` | | | |
| `<DATA_FORMAT_1>` | | | |
| `<NOTIFICATION_CHANNEL>` | | | |
| file in/out | | | |
| env | | | |
| `<PROJECT_CONFIG_OR_RULES_FILE>` | | | |

STALE_RISK: перечислить из `tasks.md` § S#, если затронуто.

### Справка

| | |
|---|---|
| **Назначение** | Оценивает совместимость данных и API. |
| **Когда заполняется** | После Runtime Paths; до Pipeline Impact (если контракт диктует поведение пайплайна). |
| **Кем заполняется** | **Архитектор/исполнитель** по `contracts.md`. |

---

## Pipeline Impact

### Поле

| P# | Happy path | Failure path | Побочные эффекты (integrations, persistence) |
|----|------------|--------------|---------------------------------------------|
| `<P1>` | | | |
| `<P2>` | | | |
| … | | | |

### Справка

| | |
|---|---|
| **Назначение** | Показывает, изменится ли наблюдаемое поведение каждого пайплайна (включая деградацию и lock-skip). |
| **Когда заполняется** | После Contracts Impact; для каждого pipeline с «да» в задаче. |
| **Кем заполняется** | **Архитектор/исполнитель** по `architecture_map.md`. |

---

## Integration Impact

### Поле

| Интеграция | Поведение сейчас (KB) | Влияние изменения |
|------------|----------------------|-------------------|
| `<INTEGRATION_1>` | | |
| `<INTEGRATION_2>` | | |
| `<SUBSYSTEM_X>` | | |

### Справка

| | |
|---|---|
| **Назначение** | Внешние зависимости и точки отказа вне одного модуля. |
| **Когда заполняется** | Если затронуты integrations / external subsystems из KB. |
| **Кем заполняется** | **Исполнитель/архитектор** по `architecture_map.md` + `contracts.md`. |

---

## Cache Impact

### Поле

| Артефакт | Путь | Влияние |
|----------|------|---------|
| config cache | `<CACHE_PATH>` | |
| temp files | `<TEMP_PATH>` | |
| auth / session state | `<AUTH_STATE_PATH>` | |
| in-memory state | `<MODULE>.<attr>` | |

Fail-safe config (если есть): см. `decisions.md` **E#**.

### Справка

| | |
|---|---|
| **Назначение** | Риск устаревших данных, гонок, потери сессии после cleanup/restart. |
| **Когда заполняется** | При изменении config provider, cache, background cleanup, auth. |
| **Кем заполняется** | **Архитектор/исполнитель** по `contracts.md` + `architecture_map.md`. |

---

## Background / Scheduler Impact

### Поле

| Элемент | Default (KB) | Влияние |
|---------|--------------|---------|
| Job intervals / windows | `<SCHEDULE>` | |
| Error in periodic job | log + continue (E#) | |
| Locks | `<PROJECT_LOCK_PATH>` | |
| Process restart policy | E# | |
| Worker threads / queues | | |

DOCS_ONLY features — не оценивать как реализованное.

### Справка

| | |
|---|---|
| **Назначение** | Влияние на фоновые циклы, рестарт процесса, конкуренцию за lock. |
| **Когда заполняется** | При изменении `<PRIMARY_ENTRYPOINT>` или модулей, вызываемых только из background jobs. |
| **Кем заполняется** | **Архитектор** по `architecture_map.md` + `contracts.md`. |

---

## Data Impact

### Поле

| Данные | Источник истины (KB) | Изменение схемы/семантики |
|--------|----------------------|---------------------------|
| YAML / JSON configs | `<CONFIG_PATH>` | |
| `<PROJECT_CONFIG_OR_RULES_FILE>` | sections / sheets | |
| external storage paths | `<STORAGE_PATH>` | |
| export / report formats | | |

DORMANT consumers — отметить, если риск активации.

### Справка

| | |
|---|---|
| **Назначение** | Влияние на файлы и конфиги, которые читают/пишут компоненты и ops. |
| **Когда заполняется** | При изменении data layer, config, external storage layout. |
| **Кем заполняется** | **Исполнитель/архитектор** по `contracts.md` + `architecture_map.md`. |

---

## Regression Risks

### Поле

| # | Риск регрессии | Область | Вероятность | Обнаружение |
|---|----------------|---------|-------------|-------------|
| 1 | | `<PX>` / contract / integration | | |

Связь с `tasks.md`: gaps G#, STALE_RISK S#, UNKNOWN.

### Справка

| | |
|---|---|
| **Назначение** | Что может сломаться в *других* пайплайнах при целевом изменении. |
| **Когда заполняется** | После всех Impact-секций; перед Rollback Strategy. |
| **Кем заполняется** | **Архитектор** (чеклист по KB) + **исполнитель** (конкретные сценарии). |

---

## Rollback Strategy

### Поле

| Уровень | Действие | Условие |
|---------|----------|---------|
| Конфиг | revert YAML / env на `<DEPLOYMENT_TARGET>` | |
| Код | revert commit / redeploy | |
| Данные | revert `<PROJECT_CONFIG_OR_RULES_FILE>` / storage / cache | |
| Runtime | restart `<PRIMARY_ENTRYPOINT>`; снятие lock (policy — KB) | |

Операционные one-shot: `tasks.md` § «Операционные точки».

### Справка

| | |
|---|---|
| **Назначение** | План отката без импровизации; особенно для prod (UNKNOWN gaps — зафиксировать). |
| **Когда заполняется** | Завершающий шаг impact; обязателен при risk medium/high. |
| **Кем заполняется** | **Архитектор** + **инициатор/ops** для platform-шагов. |

---

## Вердикт impact

| Поле | Значение |
|------|----------|
| **Уровень риска** | low \| medium \| high |
| **Рекомендация** | proceed \| proceed with caution \| defer \| split task |

### Справка (вердикт)

| | |
|---|---|
| **Назначение** | Итог для перехода задачи в `in_progress` или возврата в `draft`. |
| **Когда заполняется** | После заполнения всех секций выше. |
| **Кем заполняется** | **Архитектор** (при споре — согласование с инициатором). |

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| | | создано |
