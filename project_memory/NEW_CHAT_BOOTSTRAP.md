# New Chat Bootstrap

Стандартный порядок старта **нового чата** (GPT или Cursor) в Knowledge Workflow v1. Роли — [roles.md](roles.md); навигация — [README.md](README.md). Lifecycle после появления Task — [workflow.md](workflow.md) §3.

## Рекомендуемые entrypoints (новый чат)

| Инструмент | Файл | Команда |
|------------|------|---------|
| **GPT** | [GPT_CHAT_BOOTSTRAP.md](GPT_CHAT_BOOTSTRAP.md) | `Прочитай project_memory/GPT_CHAT_BOOTSTRAP.md` |
| **Cursor** | [CURSOR_CHAT_BOOTSTRAP.md](CURSOR_CHAT_BOOTSTRAP.md) | `Прочитай project_memory/CURSOR_CHAT_BOOTSTRAP.md` |

Этот документ (`NEW_CHAT_BOOTSTRAP.md`) — общий Stage 0 и Reading Order; детальные сценарии A/B/C/D — в chat bootstrap файлах выше.

---

## If no Task exists

Когда пользователь пришёл с **новой идеей**, а файла `active_tasks/TASK-*.md` **ещё нет** — это **Stage 0 — Task Creation** (до `draft`/`ready` в [workflow.md](workflow.md) §3).

### Stage 0 — Task Creation (flow)

```
Business Request
      ↓
KB Reading
      ↓
Task Creation
      ↓
Impact Decision
      ↓
Context Pack Decision
      ↓
Workflow Start  (draft → ready → …)
```

| Шаг | Действие | Источник |
|-----|----------|----------|
| **1. Business Request** | Зафиксировать цель, контекст, ограничения «с первых слов» пользователя | запрос пользователя |
| **2. KB Reading** | Прочитать релевантные KB: `README` → `current_state` / `architecture_map` / `contracts` / `tasks` (gaps, STALE_RISK) — **не** весь repo | [README.md](README.md) Reading Order |
| **3. Task Creation** | Создать `active_tasks/TASK-YYYY-MM-DD-NN_*.md` по [task_template](templates/task_template.md); статус **`draft`**; Goal, Business Context, Out Of Scope, Success Criteria, Constraints | Initiator + **GPT Architect** |
| **4. Impact Decision** | Записать в Task § **Workflow decisions**: Impact нужен? (триггеры [workflow.md](workflow.md) §6). Если да — создать `TASK-*_impact.md` **до** `ready` | GPT Architect |
| **5. Context Pack Decision** | Записать: CP нужен? (триггеры [workflow.md](workflow.md) §7). Обычно **да** при `ready` → `CP-dev_task-*` или `CP-architect-*` | GPT Architect |
| **6. Workflow Start** | Перевод `draft` → **`ready`** только когда Task полон + Impact/CP по решению §4–5. Дальше — [workflow.md](workflow.md) §3 | см. § Reading Order ниже |

**Запрещено в Stage 0:**

- менять runtime-код, tests, prod config без Task `ready` и явного scope;
- считать устный чат источником истины вместо Task;
- пропускать Impact при срабатывании §6 workflow;
- объявлять Workflow v2 или менять семантику Workflow v1.

### GPT (Architect) — если Task ещё нет

1. [README.md](README.md) → [roles.md](roles.md) → [workflow.md](workflow.md) §3, §6, §7.
2. KB Reading по теме запроса (минимум `tasks.md` + затронутые разделы KB v1.1).
3. Уточнить scope; предложить **минимальную** формулировку Goal / Out Of Scope.
4. **Создать Task** (`draft`); зафиксировать Impact/CP decisions.
5. Выдать **Cursor operational task** только после `ready` (или явного поручения «только создать Task»).
6. Краткий bootstrap-блок (см. § После чтения) — с пометкой **Stage 0, Task отсутствует → создаём**.

### Cursor — если Task ещё нет

1. [README.md](README.md) → [roles.md](roles.md) — роль **Implementation Executor**.
2. **Не** начинать implementation / patch / tests до появления Task **`ready`** + CP (если требуется).
3. Допустимо: помочь **создать** markdown Task по шаблону, если пользователь явно просит оформить задачу.
4. Если пользователь просит «сразу код» — эскалация: нужен Task (`draft`→`ready`) и scope; см. [roles.md](roles.md) Must Not (scope expansion).
5. После появления Task — перейти к § Reading Order (Task существует).

---

## Reading Order (когда Task уже есть)

1. Read [README.md](README.md)
2. Read [roles.md](roles.md)
3. Read [workflow.md](workflow.md)
4. Read relevant **Context Pack** (`active_tasks/CP-*.md` для текущей задачи)
5. Read **active Task** (`active_tasks/TASK-*.md`)

Дополнительно по scope задачи: файлы KB v1.1 из Reading Order Task или CP (не копировать KB целиком в чат).

---

## После чтения

GPT **обязан** перед работой:

1. **Кратко пересказать проект** — назначение, prod entry (`<PRIMARY_ENTRYPOINT>` из KB), что в scope задачи.
2. **Перечислить ограничения** — из Task § Constraints, Out Of Scope, Allowed/Forbidden.
3. **Перечислить риски** — DORMANT/DOCS_ONLY/STALE_RISK, релевантные задаче; Remaining Risks из Review предшественника, если есть.
4. **Подтвердить понимание роли** — Architect/Reviewer **или** указать, что исполнение передаётся Cursor по operational task.

Формат — короткий блок (5–15 строк), без полного копирования KB.

---

## Роль по умолчанию

| Инструмент | Роль |
|------------|------|
| **GPT** | Architect / Reviewer — см. [roles.md](roles.md) § GPT |
| **Cursor** | Implementation Executor — см. [roles.md](roles.md) § Cursor |

Cursor-сессия: Task `ready` + CP `dev_task` + Reading Order; не менять KB без отдельного поручения.

---

## GPT Bootstrap

Для Custom GPT / system prompt используйте [templates/gpt_bootstrap_template.md](templates/gpt_bootstrap_template.md).
