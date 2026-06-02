# Knowledge Base — `<PROJECT_NAME>`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.2 |
| **Workflow** | Knowledge Workflow v1 |
| **Context Pack** | v1.0 |
| **Область** | `project_memory/` — знания и процессы (не runtime) |

Главная точка входа в **Knowledge Base** проекта. Runtime-код живёт **вне** этой директории.

---

## Reading Order

Порядок чтения для нового участника, GPT или Cursor:

| Шаг | Документ | Когда |
|-----|----------|-------|
| 1 | [README.md](README.md) | всегда (этот файл) |
| 2 | [current_state.md](current_state.md) | что работает сейчас |
| 3 | [architecture_map.md](architecture_map.md) | задачи, impact, incident, onboarding |
| 4 | [contracts.md](contracts.md) | задачи с данными / API / env |
| 5 | [decisions.md](decisions.md) | review, impact, архитектурные изменения |
| 6 | [tasks.md](tasks.md) | риски, gaps, ops, **R-WE-*** |
| 7 | [workflow.md](workflow.md) | процесс Task → merge |
| 8 | [context_pack.md](context_pack.md) | правила Context Pack |
| 9 | [roles.md](roles.md) | роли GPT и Cursor |
| 10 | [GPT_CHAT_BOOTSTRAP.md](GPT_CHAT_BOOTSTRAP.md) | **старт GPT-чата** (рекомендуется) |
| 11 | [CURSOR_CHAT_BOOTSTRAP.md](CURSOR_CHAT_BOOTSTRAP.md) | **старт Cursor-чата** (рекомендуется) |
| 12 | [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) | Stage 0, общий lifecycle |
| 13 | `active_tasks/TASK-*.md` | если есть активная задача |
| 14 | `templates/*_template.md` | при создании артефакта |

**Легенда статусов** (обязательна в KB): `CONFIRMED` | `DORMANT` | `UNKNOWN` | `DOCS_ONLY` | `STALE_RISK`

---

## Document Map

```
project_memory/
├── README.md                      ← этот файл (вход в KB)
├── workflow.md                    ← Knowledge Workflow v1
├── context_pack.md                ← Context Pack System
├── GPT_CHAT_BOOTSTRAP.md          ← старт GPT-чата (рекомендуется)
├── CURSOR_CHAT_BOOTSTRAP.md       ← старт Cursor-чата (рекомендуется)
├── NEW_CHAT_BOOTSTRAP.md          ← Stage 0, общий lifecycle
├── roles.md                       ← роли GPT / Cursor
├── architecture_map.md            ← KB: архитектура, pipelines
├── contracts.md                   ← KB: контракты данных/API
├── current_state.md               ← KB: runtime-состояние
├── decisions.md                   ← KB: решения E_ / I_
├── tasks.md                       ← KB: gaps G_ / STALE_RISK S_
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
    ├── TASK-*.md, CP-*.md, *_impact.md, *_review.md
    └── archive/
```

Файлы KB (`architecture_map.md` и др.) **создаются** при инициализации проекта из `templates/*_template.md`. См. [TEMPLATE_CHECKLIST.md](../TEMPLATE_CHECKLIST.md).

### Active subsystems (runtime)

| Subsystem | Entry / modules | KB ref |
|-----------|-----------------|--------|
| Hourly / Wallet / Download / Rate jobs | `scheduler.py`, `JOB_REGISTRY` | P1–P4 |
| **WalletEditor** | Telegram Excel ingest → Antares card editing → per-operator execution | **P-WE** in `architecture_map.md` |

**WalletEditor** capabilities:

- Telegram `.xlsx` document ingest (allowed chats + operator map)
- Antares UI card editing via Playwright (`automation/engine.py`)
- Per-operator credentials, auth-state, queue, and worker thread

---

## Quick Start — новый чат

| Инструмент | Команда |
|------------|---------|
| **GPT** | `Прочитай project_memory/GPT_CHAT_BOOTSTRAP.md` |
| **Cursor** | `Прочитай project_memory/CURSOR_CHAT_BOOTSTRAP.md` |

Дополнительно: [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) для Stage 0 и Reading Order при активной Task.

---

## Workflow links

| Артефакт | Шаблон | Рабочий путь |
|----------|--------|--------------|
| **Task** | [task_template.md](templates/task_template.md) | `active_tasks/TASK-YYYY-MM-DD-NN_*.md` |
| **Impact** | [impact_analysis_template.md](templates/impact_analysis_template.md) | `active_tasks/TASK-*_impact.md` |
| **Review** | [review_template.md](templates/review_template.md) | `active_tasks/TASK-*_review.md` |
| **Context Pack** | [context_pack_template.md](templates/context_pack_template.md) | `active_tasks/CP-*.md` |

Процесс: [workflow.md](workflow.md) — lifecycle, merge criteria, KB update rules.

---

## Stage 0 — если Task ещё нет

Новая идея без `active_tasks/TASK-*.md`:

```
Business Request → KB Reading → Task Creation → Impact Decision → CP Decision → draft → ready
```

Подробно: [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) § Stage 0.

---

## Как начать новый GPT-чат

1. **Стартовая команда:** `Прочитай project_memory/GPT_CHAT_BOOTSTRAP.md` — см. [GPT_CHAT_BOOTSTRAP.md](GPT_CHAT_BOOTSTRAP.md).
2. Примените [gpt_bootstrap_template.md](templates/gpt_bootstrap_template.md) как system prompt (подставьте `<PROJECT_NAME>`).
3. При активной Task — [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) § Reading Order.
4. Роль: **GPT Architect / Reviewer** — [roles.md](roles.md).

---

## Как начать новый Cursor-чат

1. **Стартовая команда:** `Прочитай project_memory/CURSOR_CHAT_BOOTSTRAP.md` — см. [CURSOR_CHAT_BOOTSTRAP.md](CURSOR_CHAT_BOOTSTRAP.md).
2. [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) → [roles.md](roles.md) → [workflow.md](workflow.md).
3. **Не писать код** до Task `ready` + Context Pack (если требуется).
4. Рабочий набор: CP `dev_task` + Task + Impact (medium+) + Reading Order из Task/CP.

---

## Context Pack

Спецификация: [context_pack.md](context_pack.md).  
Сценарии: `onboarding` | `dev_task` | `arch_review` | `architect` | `qa_review` | `incident`.

---

## Инициализация KB (новый проект)

1. Скопировать каждый `templates/*_template.md` → соответствующий `*.md` в `project_memory/`.
2. Заменить все `<PLACEHOLDER>` на факты **вашего** проекта с меткой CONFIRMED.
3. Удалить секции «Как заполнить» из рабочих KB-файлов (опционально).
4. Пройти [TEMPLATE_CHECKLIST.md](../TEMPLATE_CHECKLIST.md).

---

## История

| Дата | Событие |
|------|---------|
| YYYY-MM-DD | KB v1.1 инициализирована для `<PROJECT_NAME>` |
| 2026-06-01 | KB v1.2 — WalletEditor P-WE integrated (WE-0…WE-6) |
