# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-2026-09-17-01 |
| **Связанная задача** | TASK-2026-09-17-01 |
| **KB версия** | v1.10 |
| **Триггер** | I1/I2/I3 для **программы**; для **этого PR** — только docs |
| **Вердикт** | **proceed** для документации; runtime **defer** |

---

## Current Runtime Behavior

Entry: `railway.toml` → `scheduler.py` (E1, E2).  
Test capabilities R1–R6 + встроенный Raccoon (факт survey, шире чем KB S2).  
Platform develop: Raccoon-only threads.  
Этот PR **не** меняет runtime.

---

## Runtime Paths

| Путь | Entry | Изменяется этим PR? |
|------|-------|---------------------|
| Test scheduler + polling | `scheduler.py` | нет |
| Platform scheduler | `scheduler.py` | нет |
| WalletEditor workers | `automation/worker.py` | нет |

---

## Pipeline Impact

| Pipeline | Impact |
|----------|--------|
| P1–P4, P-WE | none (docs) |
| Будущие этапы 3–6 | high — отдельный Impact на каждый code PR |

---

## Contracts Impact

Нет изменений env/DB/files в этом PR. Предлагаемые будущие env: `PROJECT_PROFILE`, `JOB_ACCEPT`, profile-scoped `STATE_DIR` — только в ADR.

---

## DORMANT / DOCS_ONLY

Не активировать DORMANT Antares в Platform. Не реализовывать WR.

---

## Blast Radius

Docs readers / onboarding. Production blast radius = **0**.

---

## Risks

| ID | Риск | Mitigation |
|----|------|------------|
| R1 | Ops примет ADR как уже внедрённый | статус PROPOSED; current_state не переписан как migrated |
| R2 | Copy-paste Raccoon из Test | явно запрещено в ADR |

---

## Verdict

**proceed** — публикация обследования.  
Code/deploy: **не в этой задаче**.
