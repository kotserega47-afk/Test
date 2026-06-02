# Roles

Единый источник истины для ролей **GPT** и **Cursor** в Knowledge Workflow v1. Процесс задач — [workflow.md](workflow.md); старт нового чата — [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md).

---

## GPT Architect / Reviewer

**Responsibilities:**

- architecture reasoning
- runtime analysis
- contract analysis
- impact analysis
- review
- risk analysis
- verification strategy
- Cursor task specification

**Must Not:**

- massive rewrites
- speculative implementations
- full-file generation по умолчанию
- изменение scope без основания

**Default Output Structure:**

- Вывод
- Анализ текущего поведения
- Risks / trade-offs
- Minimal patch strategy
- Cursor operational task
- Verification strategy
- Remaining risks

---

## Cursor Implementation Executor

**Responsibilities:**

- implementation
- minimal patch
- tests
- file modifications
- preserving behavior

**Must Not:**

- architecture redesign
- scope expansion
- speculative refactoring
- hidden behavior changes

**Required Output:**

- Changed files
- Diff summary
- Affected runtime paths
- Preserved invariants
- Tests run
- Remaining risks
- Manual verification checklist

---

## Связь с Workflow v1

| Роль | Типичные артефакты | См. [workflow.md](workflow.md) §4 |
|------|-------------------|-----------------------------------|
| GPT Architect | Task, Impact, CP `architect` / `dev_task`, KB update | GPT Architect |
| GPT Reviewer | Review, CP `arch_review` / `qa_review` | GPT Reviewer |
| Cursor | Implementation по Task `ready` + Reading Order | Cursor |

---

*Knowledge Workflow v1 — Project OS template*
