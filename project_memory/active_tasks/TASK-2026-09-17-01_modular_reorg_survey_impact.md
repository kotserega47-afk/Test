# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-2026-09-17-01 |
| **Связанная задача** | TASK-2026-09-17-01 |
| **KB версия** | v1.10 |
| **Триггер** | I1/I2/I3 для **программы**; этот PR — документация |
| **Вердикт** | **proceed** для публикации docs; runtime-код **не** меняется; merge в `test_main` **может** затронуть сервис Test |

---

## Current Runtime Behavior

Entry: `railway.toml` → `scheduler.py`.  
Test service (F25): branch `test_main`, SHA `535994c…`, deploy SUCCESS — **не** job health. Код процесса **смешанный** (регистрация Antares + Raccoon jobs); исполнение raccoon jobs в этом контейнере — U12.  
Raccoon service (F26): Platform `develop` `ebbcd6c…`.

---

## Runtime Paths

| Путь | Entry | Diff этого PR | Эффект merge в connected branch |
|------|-------|---------------|----------------------------------|
| Test scheduler | `scheduler.py` | файлы `.py` **не** меняются | **возможен rebuild/restart** сервиса `542c84d6-…` |
| Raccoon scheduler | Platform `scheduler.py` | не в этом репо | merge Test **не** должен сдвигать SHA `ebbcd6c…` (проверить после выката) |
| WalletEditor workers | `automation/worker.py` | нет в diff | рестарт Test оборвёт in-memory очереди/cron cursors |

---

## Pipeline Impact

| Pipeline | Impact кода | Impact merge/deploy |
|----------|-------------|---------------------|
| P1–P4, P-WE | none в diff | restart Test = краткий разрыв polling и in-memory schedules |
| Будущие этапы | high | отдельный Impact |

**Не утверждается:** production blast radius = 0; docs-only merge гарантированно не трогает сервисы; совместимость 100%.

---

## Contracts Impact

Нет изменений runtime env/DB в diff. Предлагаемые будущие рычаги (`JOB_ACCEPT`, `EXTERNAL_SIDE_EFFECTS`, early profile gate) — **отсутствуют в коде**; см. migration.

Railway production-настройки **не** меняются этой задачей.

---

## Blast Radius

Читатели docs. Операционно: **если** PR влить в `test_main` при включённом auto-deploy — сервис Test может пересобраться и перезапуститься на том же runtime-коде. Raccoon при корректной независимости веток — нет. Независимый выпуск: `ops/MODULAR_REORG_MIGRATION.md` § Автодеплой.

---

## Risks

| ID | Риск | Mitigation |
|----|------|------------|
| R1 | Ops примет ADR как внедрённый | PROPOSED; mixed ≠ antares-only |
| R2 | Copy-paste Raccoon из Test | запрет в ADR |
| R3 | Merge docs рестартит Test | не merge без окна; либо принять restart |
| R4 | Dual Raccoon | U2/U10/U12; SUCCESS деплоя не закрывает |

---

## Verdict

**proceed** с docs. Не merge/deploy в рамках этой задачи без явного решения об автодеплое Test.
