# Project Memory Index — `analizis`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Последнее обновление** | 2026-05-24 |
| **Проект** | `analizis` — операционная аналитика платежей |

Точка входа в knowledge base. Новый чат: сначала [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md).

---

## Reading Order

Читать **в этом порядке** (не весь репозиторий):

| # | Документ | Путь |
|---|----------|------|
| 1 | **New chat bootstrap** | [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) |
| 2 | **Architecture map** | [architecture_map.md](architecture_map.md) |
| 3 | **Contracts** | [contracts.md](contracts.md) |
| 4 | **Current state** | [current_state.md](current_state.md) |
| 5 | **Decisions** | [decisions.md](decisions.md) |
| 6 | **Risks** | [risks.md](risks.md) |

**Дополнительно (по задаче):**

| Документ | Путь | Когда |
|----------|------|--------|
| Tasks index | [tasks.md](tasks.md) | Сводка задач (если ведётся) |
| Workflow | [workflow.md](workflow.md) | Процесс Task / Review |
| Roles | [roles.md](roles.md) | GPT vs Cursor |
| Context packs | [context_pack.md](context_pack.md) | Handoff в Cursor |
| Active task | [active_tasks/TASK-*.md](active_tasks/) | Конкретная работа |

**Шаблоны:** `templates/` — только для создания новых KB-файлов, не для runtime-чтения.

---

## Task Workflow

```
Risk
  → TASK
  → Investigation
  → Review
  → Decision
  → KB Update
```

| Этап | Артефакт | Кто | Примечание |
|------|----------|-----|------------|
| **Risk** | `risks.md` (R##) | Architect | Источник приоритета |
| **TASK** | `active_tasks/TASK-*.md` | Architect | Goal, Scope, Out Of Scope |
| **Investigation** | Findings в TASK; read-only | Architect / Cursor | Без кода/env, если TASK так задаёт |
| **Review** | `*_review.md` (optional) | Reviewer | Сверка с контрактами |
| **Decision** | `decisions.md` (E#) или отказ от изменения | Architect | Только подтверждённые факты |
| **KB Update** | `architecture_map`, `contracts`, `current_state`, `risks` | Architect | После merge-approved facts |

**Cursor:** implementation только с TASK; **GPT:** не пишет код по умолчанию — см. [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) §8–9.

---

## Status Legend

Использовать во всех KB-файлах и TASK:

| Status | Meaning |
|--------|---------|
| **CONFIRMED** | Подтверждено кодом или актуальным runtime |
| **DORMANT** | Код/модуль есть, не в prod entry или не вызывается |
| **UNKNOWN** | Не подтверждено — не выдумывать |
| **DOCS_ONLY** | Описано в документации, в runtime не найдено |
| **STALE_RISK** | Расхождение docs ↔ code или дублирующие пути |

---

## Open Tasks

| Task ID | File | Priority | Risk | Статус |
|---------|------|----------|------|--------|
| **TASK-A3-01** | [active_tasks/TASK-A3-01-telegram-env-verification.md](active_tasks/TASK-A3-01-telegram-env-verification.md) | P0 | **R01** — `TG_BOT_TOKEN` vs `TELEGRAM_BOT_TOKEN` | **open** |

**Следующие P0 из risk backlog** (ещё без TASK — создать по workflow):

| Risk | Тема |
|------|------|
| R02 | `integrations/downloader.py` production binding |
| R04 | `.env` / secrets in git history |
| R07 | Dropbox + `rules.xlsx` availability |

---

## KB Files (filled)

| File | Stage | Статус |
|------|-------|--------|
| [architecture_map.md](architecture_map.md) | A1 | draft |
| [contracts.md](contracts.md) | A2 | draft |
| [current_state.md](current_state.md) | A3 | draft |
| [decisions.md](decisions.md) | A4 | draft |
| [risks.md](risks.md) | A5 | draft |
| [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) | KB-BOOTSTRAP | active |

---

## Prod snapshot (one line)

**Railway** → `python scheduler.py` → Telegram + Raccoon scheduled jobs + `rules.xlsx` @ Dropbox.

Details: [current_state.md](current_state.md) §1, [architecture_map.md](architecture_map.md) §1.

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | Project Memory Index (KB v1.1) — Stages A1–A5, KB-BOOTSTRAP, T1 |
