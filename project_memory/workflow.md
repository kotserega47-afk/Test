# Knowledge Workflow — Official Process

| Мета | Значение |
|------|----------|
| **Версия процесса** | 1.0 |
| **KB** | v1.1 |
| **Task Workflow** | v1 |
| **Context Pack** | v1.0 |
| **Область** | `project_memory/` — знания и процессы (не runtime) |

Этот документ — **единая точка входа** в цикл работы с задачей: от идеи до merge и обновления KB. Шаблоны полей — в `templates/`; детали pack — в `context_pack.md`.

---

## 1. Purpose

Зафиксировать воспроизводимый цикл, при котором:

1. Любая работа начинается с **понятной цели** и границ (Task).
2. Архитектурные и runtime-риски выявляются **до** merge (Impact Analysis, Review).
3. Контекст передаётся между чатами и инструментами без устной истории (Context Pack).
4. **KB v1.1** остаётся единственным нормативным описанием системы; артефакты workflow не подменяют KB.
5. После merge факты runtime обновляются в KB, а не остаются только в PR или чатах.

Процесс не описывает реализацию в коде и не меняет prod — только управление знаниями и задачами.

---

## 2. Sources of Truth

| Приоритет | Источник | Назначение |
|-----------|----------|------------|
| **1** | KB v1.1 (`architecture_map`, `contracts`, `current_state`, `decisions`, `tasks`) | Архитектура, контракты, состояние, решения, gaps |
| **2** | `active_tasks/TASK-*.md` (+ `_impact`, `_review`) | Конкретная задача и её артефакты |
| **3** | `context_pack.md` + заполненные `CP-*.md` | Сжатая передача контекста в новый чат |
| **4** | `workflow.md` (этот документ) | Порядок шагов и критерии merge |
| **—** | `templates/*` | Форма артефактов, не факты о системе |

**Не являются источником истины:**

- Legacy docs в корне репозитория (`<LEGACY_DOC>.md`) — только **DOCS_ONLY** / **STALE_RISK** (см. `tasks.md` S#).
- Устные договорённости, чаты без фиксации в Task/KB.
- Context Pack после истечения `Valid until` (см. `context_pack.md` § 3.4).

**Легенда статусов** (из KB): CONFIRMED, DORMANT, UNKNOWN, DOCS_ONLY, STALE_RISK — обязательна при любой формулировке «как работает система».

---

## 3. Standard Task Lifecycle

```
Идея → draft → ready → [impact?] → in_progress → review → merge → done → [KB update?]
```

| Статус | Смысл | Ключевые артефакты |
|--------|-------|-------------------|
| **draft** | Идея, Goal, Business Context | Task (частично) |
| **ready** | Scope согласован, можно начинать работу | Task (полный), Context Pack, Impact (если требуется) |
| **in_progress** | Реализация / исполнение | Task, pack `dev_task`, ссылка на PR (вне `project_memory`) |
| **review** | Проверка перед merge | Review, pack `qa_review` / `arch_review` |
| **merge** | Изменения в основной ветке | PR merged (вне repo process) |
| **done** | Success Criteria выполнены | Task, Review approve, KB при необходимости |
| **cancelled** | Работа прекращена | Task + причина в Истории |

**Правило:** переход `draft` → `ready` только когда заполнены обязательные поля Task (см. `templates/task_template.md`) и выполнены триггеры Impact / Context Pack (§ 6–7).

---

## 4. Roles

| Роль | Ответственность | Типичные артефакты |
|------|-----------------|-------------------|
| **Initiator** | Goal, Business Context, Success Criteria, Out Of Scope; приоритет | Task (`draft`→`ready`) |
| **GPT Architect** | Scope по KB, Impact, Context Pack, Constraints, обновление KB после merge | Task, Impact, CP `architect` / `dev_task`, `tasks.md` |
| **GPT Reviewer** | Архитектурный review: scope, contracts, DORMANT/DOCS_ONLY | Review, CP `arch_review` |
| **Cursor** | Реализация по Task `ready` + Reading Order; не меняет KB без поручения | Исполнение; комментарии в Review |
| **QA Reviewer** | Приёмка по Success Criteria, регрессия, Test Coverage | Review § Test Coverage, CP `qa_review` |
| **KB Owner** | Целостность KB v1.1, onboarding-pack, закрытие UNKNOWN gaps (G#) | `project_memory/*.md`, CP `onboarding` |

**Разделение:**

- **Initiator** принимает бизнес-результат (`done`), не архитектурные компромиссы без Review.
- **GPT Architect** — единственная роль, **обновляющая KB** по правилам § 9.
- **Cursor** не заполняет Impact и не approve Review; эскалация к Architect при расхождении с KB.

---

## 5. Required Artifacts

| Артефакт | Шаблон | Путь (рабочий) | Когда обязателен |
|----------|--------|----------------|------------------|
| **Task** | `templates/task_template.md` | `active_tasks/TASK-YYYY-MM-DD-NN_*.md` | Всегда |
| **Impact Analysis** | `templates/impact_analysis_template.md` | `active_tasks/TASK-…_impact.md` | По триггерам § 6 |
| **Context Pack** | `templates/context_pack_template.md` | `active_tasks/CP-*.md` | По триггерам § 7 |
| **Review** | `templates/review_template.md` | `active_tasks/TASK-…_review.md` | Перед merge (medium+); рекомендуется для low |

**Связи:**

- Task ссылается на `_impact.md` / `_review.md` в мета-блоке.
- Context Pack ссылается на `TASK-…` и сценарий (`context_pack.md` § 1.2).
- Review ссылается на Task + Impact (если был).

Дублировать текст KB или полные шаблоны в Task/Review **запрещено** — только выжимка и ссылки.

---

## 6. When Impact Analysis is required

Impact Analysis **обязателен** перед `in_progress`, если выполняется **хотя бы одно**:

| # | Условие |
|---|---------|
| I1 | Затронут любой **pipeline** из `architecture_map.md` (`Affected Pipelines` ≠ пусто) |
| I2 | Затронут **background scheduler**, locks, cache/auth paths, auto-restart |
| I3 | Изменение **contracts**: API, file in/out, env, `<PROJECT_CONFIG_OR_RULES_FILE>`, DTO |
| I4 | Риск активации **DORMANT** модулей |
| I5 | Заявленный риск **medium / high** в Task § Known Risks |
| I6 | Задача помечена Initiator/Architect как **medium/high-risk** |

**Не обязателен** (достаточно Task + pack `dev_task`):

- Только документация KB без смены CONFIRMED-фактов.
- Исправление опечаток в task/pack без изменения runtime-утверждений.
- **Low-risk** по согласованию Architect: один модуль, нет затронутых pipelines, нет contracts, Out Of Scope явно исключает интеграции.

**Вердикт Impact** (`proceed` / `defer` / `split`) — обязателен при обязательном Impact; `defer` блокирует `in_progress`.

Детали полей: `templates/impact_analysis_template.md`. Триггеры pack-сценария `architect`: `context_pack.md` § 1.5.C.

---

## 7. When Context Pack is required

Context Pack **обязателен** при:

| # | Ситуация | Сценарий pack |
|---|----------|----------------|
| C1 | Старт работы в **новом GPT-чате** или новой сессии Cursor | `dev_task`, `arch_review`, `qa_review`, `incident` |
| C2 | Передача Task от **Architect → Cursor** | `dev_task` |
| C3 | Передача на **GPT Reviewer** / QA | `arch_review` / `qa_review` |
| C4 | Task переходит в **`ready`** | `dev_task` (пересборка) |
| C5 | **Incident** расследование | `incident` |
| C6 | Онбординг участника | `onboarding` (KB Owner, ≤14 дней Valid until) |

**Рекомендуется**, но не обязателен:

- Продолжение в **той же** сессии Cursor, если чат уже содержит актуальный pack и Task `ready` без смены scope.

**Запрещено** использовать pack с истёкшим `Valid until` — только Reading Order + свежая KB (`context_pack.md` § 2.3).

Governance (размер, источники): `context_pack.md` § 3.

---

## 8. Merge Criteria

Merge **разрешён**, когда выполнены **все** применимые пункты:

| # | Критерий |
|---|----------|
| M1 | Task в статусе `in_progress` или `review`; Success Criteria отмечены или задокументированы исключения |
| M2 | **Review** завершён: `Approve` или `Approve with comments` (`templates/review_template.md` § Approve/Reject) |
| M3 | Нет открытых **Blockers** в Review |
| M4 | **Remaining Risks** в Review приняты Initiator или Architect (явная запись «кто принял») |
| M5 | Scope Review § Changed Modules совпадает с Task § Affected Modules (нет неучтённого creep) |
| M6 | **DORMANT** не активирован без записи в `decisions.md` |
| M7 | **DOCS_ONLY** не реализован как prod без отдельного решения |
| M8 | Для обязательного Impact (§ 6): вердикт `proceed` или `proceed with caution` с принятыми рисками |
| M9 | Флаг **обновления KB** в Review определён (да/нет); если да — задача KB Owner/Architect после merge (§ 9) |

**Request changes** → возврат в `in_progress`, без merge.

**Reject** → Task `cancelled` или новый Task с пересмотром Goal.

---

## 9. KB Update Rules

KB v1.1 обновляется **только** при смене нормативных фактов о системе (новый CONFIRMED path, новый контракт, закрытие gap, новый STALE_RISK).

| Событие | Действие | Файлы KB |
|---------|----------|----------|
| Merge меняет runtime-поведение | Обновить CONFIRMED-факты | `architecture_map`, `contracts`, `current_state` |
| Новое/arch решение | Добавить E_ / I_ | `decisions.md` |
| Новый или закрытый gap | G_ / снять из gaps | `tasks.md` |
| Расхождение docs vs факт | STALE_RISK или DOCS_ONLY | `tasks.md`, при необходимости `current_state` |
| Только task/pack, без смены runtime | KB **не** менять | — |

**Процесс обновления:**

1. Review § Approve → флаг «обновить KB».
2. **GPT Architect** или **KB Owner** вносит правки с меткой **CONFIRMED** и ссылкой на источник (как в существующих файлах KB).
3. Пересобрать Context Pack для активных задач (`context_pack.md` § 2.2).
4. В Task § История — запись «KB updated post-merge».

**Запрещено:** обновлять KB из предположений без merge или без пометки UNKNOWN→CONFIRMED с обоснованием.

---

## 10. Common Anti-patterns

| Anti-pattern | Почему вредно | Правильно |
|--------------|---------------|-----------|
| Начать код без Task `ready` | Нет scope и приёмки | Task → ready → pack → work |
| Копировать `architecture_map` в чат | Устаревание, лимиты | Context Pack + Reading Order |
| Считать legacy doc реализованным | DOCS_ONLY | `decisions` + `tasks` gaps |
| Активировать DORMANT «заодно» | скрытый scope | явное решение + Impact + KB |
| Пропустить Impact на pipeline / background jobs | скрытый blast radius | § 6 |
| Merge без Review на medium+ | Непринятые риски | § 8 |
| Не обновить KB после смены runtime | KB врёт | § 9 |
| Универсальный mega Context Pack | Governance § 3.1 | Один сценарий — один CP |
| Секреты в Task/Pack | Безопасность | Только имена env из `contracts` |
| `done` без Success Criteria | Нет приёмки | Initiator sign-off |
| Исправлять только STALE_RISK в коде без KB | Следующий чат повторит ошибку | KB + gap в `tasks.md` |

---

## 11. Minimal Flow for small tasks

**Условия low-risk:** один CONFIRMED-модуль; **нет** затронутых pipelines; **нет** изменений contracts/env/background jobs; Out Of Scope исключает интеграции; Initiator + Architect согласовали low.

```
┌─────────────┐
│ Initiator   │  Task: Goal, Business Context, Desired, Success Criteria
└──────┬──────┘
       ▼
┌─────────────┐
│ Architect   │  Task: Affected Modules, Constraints, status → ready
│             │  CP: dev_task (краткий)
└──────┬──────┘
       ▼
┌─────────────┐
│ Cursor      │  Reading Order → реализация
└──────┬──────┘
       ▼
┌─────────────┐
│ Reviewer    │  Review (краткий): Changed Modules, Approve
│ (optional   │  Impact — не требуется (§ 6)
│  peer)      │
└──────┬──────┘
       ▼
     merge → Task done → KB update только если Review указал
```

**Артефакты:** Task + CP `dev_task` + Review (рекомендуется). Impact — нет.

---

## 12. Full Flow for medium/high-risk tasks

**Условия:** любой триггер Impact (§ 6) или риск medium/high.

```
┌─────────────┐
│ Initiator   │  Task draft: Goal, Business Context
└──────┬──────┘
       ▼
┌─────────────┐
│ Architect   │  Task: Current/Desired, pipelines, Modules, Contracts,
│             │  Known Risks, Constraints → ready
│             │  Impact Analysis → вердикт proceed*
│             │  CP: architect + CP: dev_task
└──────┬──────┘
       ▼
┌─────────────┐
│ Cursor      │  CP dev_task + Task + Impact + Reading Order
│             │  status: in_progress
└──────┬──────┘
       ▼
┌─────────────┐
│ GPT         │  CP arch_review + Task + Impact
│ Reviewer    │  Review: Runtime, Pipeline, Contracts
└──────┬──────┘
       ▼
┌─────────────┐
│ Cursor      │  Устранение Blockers → повторный Review при необходимости
└──────┬──────┘
       ▼
┌─────────────┐
│ QA          │  CP qa_review + Review § Test Coverage
│ Reviewer    │  Remaining Risks → sign-off Initiator
└──────┬──────┘
       ▼
     merge (§ 8)
       ▼
┌─────────────┐
│ Architect / │  KB update (§ 9), Task done, CP archive / Valid until closed
│ KB Owner    │
└─────────────┘
```

**Артефакты (полный набор):** Task + Impact + CP (`architect`, `dev_task`, `arch_review`, `qa_review` по этапам) + Review.

**Блокировки:** Impact `defer`; Review `Reject`; непринятые Remaining Risks; обязательный KB update не выполнен — Task остаётся не `done` даже после merge кода (технический долг фиксируется в `tasks.md`).

---

## Quick Reference

| Вопрос | Документ |
|--------|----------|
| Как заполнить задачу? | `templates/task_template.md` |
| Нужен ли impact? | § 6 выше |
| Нужен ли pack? | § 7 выше, `context_pack.md` |
| Можно ли merge? | § 8 |
| Обновлять ли KB? | § 9 |
| С чего начать в KB? | `README.md` |
| Активные задачи где? | `active_tasks/README.md` |
| Gaps и риски? | `tasks.md` |

---

## Document Map

```
project_memory/
├── README.md                      ← главная точка входа в KB
├── workflow.md                    ← этот документ (процесс)
├── context_pack.md                ← Context Pack System
├── NEW_CHAT_BOOTSTRAP.md          ← старт GPT/Cursor чата
├── roles.md                       ← роли GPT / Cursor
├── architecture_map.md            ← KB (создать из template)
├── contracts.md                   ← KB (создать из template)
├── current_state.md               ← KB (создать из template)
├── decisions.md                   ← KB (создать из template)
├── tasks.md                       ← KB (создать из template)
├── templates/
│   ├── architecture_map_template.md
│   ├── contracts_template.md
│   ├── current_state_template.md
│   ├── decisions_template.md
│   ├── tasks_template.md
│   ├── gpt_bootstrap_template.md
│   ├── task_template.md
│   ├── impact_analysis_template.md
│   ├── review_template.md
│   └── context_pack_template.md
└── active_tasks/
    ├── README.md
    ├── TASK-*, CP-*, *_impact, *_review
    └── archive/
```

См. также root [README.md](../README.md) и [TEMPLATE_CHECKLIST.md](../TEMPLATE_CHECKLIST.md).

---

## История

| Дата | Версия | Событие |
|------|--------|---------|
| 2026-05-23 | 1.0 | Официальный Knowledge Workflow создан |
