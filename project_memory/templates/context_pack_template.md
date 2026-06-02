# Context Pack

| Мета | Значение |
|------|----------|
| **Pack ID** | CP-`<scenario>`-`<TASK-ID\|ONBOARD>`-`<YYYYMMDD>` |
| **Scenario** | onboarding \| dev_task \| arch_review \| architect \| qa_review \| incident |
| **Role** | GPT Architect \| GPT Reviewer \| Cursor \| QA Reviewer \| New Chat |
| **KB версия** | v1.1 |
| **Generated** | YYYY-MM-DD |
| **Valid until** | YYYY-MM-DD (см. `context_pack.md` § 3.4) |
| **Связанная задача** | TASK-… \| — |
| **Workflow** | Task Workflow v1 |

> **Правило:** pack — индекс и выжимка. Источник истины — `project_memory/*.md` KB v1.1.  
> Запрещено: секреты, полные копии KB, DOCS_ONLY без метки, исходный код.  
> Спецификация: `project_memory/context_pack.md`.

---

## Project Summary

### Поле

<!-- 5–15 строк: назначение системы, prod entry (<DEPLOYMENT_TARGET> → <PRIMARY_ENTRYPOINT>), основные контуры -->

**Prod entry (CONFIRMED):** `<DEPLOY_CONFIG_FILE>` → `<PRIMARY_ENTRYPOINT>` (см. `decisions.md` E#).

**Контуры (кратко):**

| ID | Контур |
|----|--------|
| R1 | `<CAPABILITY_1>` |
| R2 | `<CAPABILITY_2>` |
| … | |

### Справка

| | |
|---|---|
| **Назначение** | Даёт новому чату понимание «что это за система» без чтения всей `architecture_map.md`. |
| **Когда заполняется** | При создании pack; обновляется при смене KB v1.1 или prod entry. |
| **Кем заполняется** | **GPT Architect** или **владелец KB** (onboarding). |

---

## Current State

### Поле

| Категория | Содержание (кратко) |
|-----------|---------------------|
| **Runtime-active** | R#: перечислить только релевантные сценарию |
| **DORMANT** | что *не* трогать без решения |
| **DOCS_ONLY** | planned / legacy docs — не в prod |
| **Deploy** | `<DEPLOYMENT_TARGET>`, `<RUN_COMMAND>` |

### Справка

| | |
|---|---|
| **Назначение** | Снимок «что реально работает в prod» по `current_state.md`. |
| **Когда заполняется** | Каждая сборка pack; при task — только затронутые R#. |
| **Кем заполняется** | **GPT Architect** / исполнитель task по KB. |

---

## Active Tasks

### Поле

| Task ID | Статус | Goal (1 строка) | Файл |
|---------|--------|-----------------|------|
| | draft \| ready \| in_progress | | `active_tasks/…` |

**Только** не `done` / не `cancelled`. Максимум 10 строк.

### Справка

| | |
|---|---|
| **Назначение** | Координация параллельной работы; избегание конфликтующих изменений. |
| **Когда заполняется** | При pack для dev/review; для incident — только связанная задача. |
| **Кем заполняется** | **Инициатор задачи** или **GPT Architect**. |

---

## Critical Invariants

### Поле

<!-- ≤15 пунктов; только CONFIRMED из decisions.md E_/I_ -->

- **E#** Prod entry = `<PRIMARY_ENTRYPOINT>`
- **E#** Pipeline lock `<PROJECT_LOCK_PATH>`
- **E#** `<NOTIFICATION_CHANNEL>` requires explicit target id
- **E#** Periodic job: error → log, process continues
- …

### Справка

| | |
|---|---|
| **Назначение** | Жёсткие правила, нарушение которых = архитектурный дефект. |
| **Когда заполняется** | Все сценарии; для узкого task — подмножество по Affected Pipelines. |
| **Кем заполняется** | **GPT Architect** / **GPT Reviewer**. |

---

## Critical Contracts

### Поле

<!-- ≤12 пунктов: имя контракта + что считается high-risk нарушением -->

| Контракт | High risk если |
|----------|----------------|
| `<API_OR_MESSAGE_CONTRACT>` | required field omitted |
| Pipeline lock | parallel run without lock |
| `<DATA_DTO>` | breaking field removed |
| `<FILE_INPUT_CONTRACT>` | missing required columns |
| … | |

### Справка

| | |
|---|---|
| **Назначение** | Фокус review/реализации на соглашениях с максимальным blast radius. |
| **Когда заполняется** | dev_task, arch_review, qa_review, impact; incident — по домену симптома. |
| **Кем заполняется** | **GPT Architect** по `contracts.md` + task § Affected Contracts. |

---

## Recent Decisions

### Поле

| Дата | ID | Решение (1 строка) | Статус |
|------|-----|-------------------|--------|
| | E# / I# | | CONFIRMED |

Окно: **≤30 дней** или с даты создания связанной task.

### Справка

| | |
|---|---|
| **Назначение** | Не повторять уже принятые архитектурные выборы. |
| **Когда заполняется** | arch_review, architect, onboarding. |
| **Кем заполняется** | **GPT Architect** по `decisions.md` + task history. |

---

## Known Risks

### Поле

| Риск | Источник | Вероятность | Для сценария |
|------|----------|-------------|--------------|
| `<STALE_RISK_DESCRIPTION>` | STALE_RISK / G# / S# | med | dev, incident |
| Legacy doc vs KB | S# | med | review |
| … | | | |

### Справка

| | |
|---|---|
| **Назначение** | Активные риски, которые агент должен учитывать до действий. |
| **Когда заполняется** | Всегда; уточняется после impact. |
| **Кем заполняется** | **GPT Architect** / **GPT Reviewer** / **QA** (принятые риски). |

---

## Open Gaps

### Поле

| ID | Gap / UNKNOWN | Влияние на работу |
|----|---------------|-------------------|
| G# | `<PLATFORM_UNKNOWN>` | deploy / incident |
| G# | External caller of `<ENTRYPOINT>` | security |
| … | | |

### Справка

| | |
|---|---|
| **Назначение** | Явно помечает, где KB не даёт ответа — не выдумывать. |
| **Когда заполняется** | onboarding, incident, platform-задачи; при UNKNOWN в impact. |
| **Кем заполняется** | **GPT Architect** по `tasks.md`; ops заполняет после интервью → KB update. |

---

## Reading Order

### Поле

1. `project_memory/current_state.md` — …
2. `project_memory/architecture_map.md` — секции: …
3. `project_memory/contracts.md` — секции: …
4. `project_memory/decisions.md` — …
5. `project_memory/tasks.md` — …
6. `project_memory/active_tasks/TASK-….md` — если есть
7. `project_memory/active_tasks/TASK-…_impact.md` — если medium+
8. `project_memory/templates/<workflow>_template.md` — при создании артефакта

**Сценарий этого pack:** `<scenario>` — см. `context_pack.md` § 1.5.

### Справка

| | |
|---|---|
| **Назначение** | Детерминированный порядок чтения KB для роли. |
| **Когда заполняется** | Каждая сборка pack; подмножество секций по сценарию. |
| **Кем заполняется** | **GPT Architect** (норматив из `context_pack.md` § 1.8). |

---

## Scenario Extensions (заполнять по типу pack)

> Удалить ненужные подразделы перед отправкой.

### dev_task (Cursor)

| Поле | Значение |
|------|----------|
| Затронутые pipelines | `<P1>`, `<P2>`, … |
| Task file | `active_tasks/TASK-….md` |
| Impact required? | да / нет |
| Template | `task_template.md` |

### arch_review / qa_review

| Поле | Значение |
|------|----------|
| Review object | PR / branch / design |
| Impact file | `…_impact.md` |
| Template | `review_template.md` |

### incident

| Поле | Значение |
|------|----------|
| Симптом | |
| Гипотеза pipeline | `<PX>` |
| Failure path (1 строка из KB) | |
| Ops UNKNOWN | gaps G# из `tasks.md` |

---

## Pack Checklist (перед отправкой)

- [ ] Pack ID, Generated, Valid until
- [ ] ≤ 400 строк
- [ ] Нет секретов и копий полной KB
- [ ] DOCS_ONLY помечены
- [ ] Active Tasks без done
- [ ] Reading Order соответствует scenario

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| | | pack создан |
