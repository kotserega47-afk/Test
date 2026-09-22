# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-31 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-30 Draft PR #33 HEAD `1eefc54720ccd036451f7c3c0e7dadaedf6efb98` (**не** закрыт; blocked до review реализации и повторной проверки reload); [MODULAR_REORG_RULES_PUBLISH_GENERATION.md](../ops/MODULAR_REORG_RULES_PUBLISH_GENERATION.md) |
| **PR** | Draft [#34](https://github.com/deniskotdavydov1991-wq/Test/pull/34) `feat/task-2026-09-17-31-rules-publish-generation`, base `feat/task-2026-09-17-30-antares-reload-rules-admission` |
| **Риск** | low в этом PR (только docs); будущий code — high: общий `rules_provider` + `AccessRules` |

Review **пройден** на контракте HEAD `8ef2838b68219e37828303e4a08cabfa2cc5a0a3`. Этот docs-коммит — закрытие TASK-31. Runtime **не** менялся. Pytest **не** запускался. GPT проверил контракт. Code публикации — TASK-32. TASK-30 **не** закрыт.

---

## Goal

Зафиксировать `publish_seq`, accessor с freshness, CAS AccessRules, replace канона в одной секции с commit, неизменяемый capture и однозначный `RulesPublishConflictExhausted` — без runtime в этом PR.

---

## Success Criteria

- [x] `publish_seq` монотонен; invalidate не обнуляет (анти-ABA)
- [x] Единый accessor PublishedState; пара старых вызовов не обещана; callers пары в code scope
- [x] AccessRules CAS: provider lock → instance lock; следующий reader проверяет generation
- [x] AccessRules: freshness источника (TTL/stat/policy) до сравнения generation/epoch
- [x] Prepare вне lock; проверка attempt + `os.replace` в одной секции с commit; ошибки workbook vs identity
- [x] Неизменяемый `capture_path`; process-unique dir; опубликованные живут до конца процесса; без `G < publish_seq - 1` и без leases
- [x] `get_rules_snapshot.local_path` = capture на hit; callers path перечислены
- [x] `observed_generation` только под lock вместе с attempt/epoch
- [x] `conflict_exhausted` → `RulesPublishConflictExhausted`, не stale-reuse и не успешный snapshot
- [x] Identity temp только после evaluate и при разрешённом save
- [x] Матрица G1–G24; historical repro ≠ safety
- [x] GPT проверил контракт; runtime не менялся; pytest не запускался
- [ ] merge/deploy (намеренно открыто)
- [ ] code TASK-32; закрытие TASK-30

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документ по исходникам `1eefc54`; runtime не менялся; pytest **не** запускался |
| GPT | проверил контракт на `8ef2838b68219e37828303e4a08cabfa2cc5a0a3`; runtime не менялся; pytest **не** запускал |

---

## Out Of Scope

Runtime этого PR; закрытие TASK-30; смена PR #33; ingest; schedules; serve; mixed-stop; merge; retarget; deploy; исходное Test; live credentials; смена C4 policy; reader leases/refcount; очистка опубликованных capture (будущий scope).

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | контракт публикации поколений; статус **review (не принято)** |
| 2026-09-21 | уточнение: publish_seq, accessor, CAS, диск, force/policy, закрытые решения |
| 2026-09-22 | replace в секции commit; capture vs canon; freshness до reuse; conflict_exhausted как исключение |
| 2026-09-22 | lifetime capture: до конца процесса; без sweep `publish_seq - 1`; G24 |
| 2026-09-22 | review пройден на `8ef2838…`; закрытие docs; runtime нет; pytest не запускался; TASK-30 не закрыт |
