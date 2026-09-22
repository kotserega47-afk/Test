# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-31 |
| **Статус** | review (подготовлено; не принято; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-30 Draft PR #33 HEAD `1eefc54720ccd036451f7c3c0e7dadaedf6efb98` (**не** закрыт); [MODULAR_REORG_RULES_PUBLISH_GENERATION.md](../ops/MODULAR_REORG_RULES_PUBLISH_GENERATION.md) |
| **PR** | Draft [#34](https://github.com/deniskotdavydov1991-wq/Test/pull/34) `feat/task-2026-09-17-31-rules-publish-generation`, base `feat/task-2026-09-17-30-antares-reload-rules-admission` |
| **Риск** | low в этом PR (только docs); будущий code — high: общий `rules_provider` + `AccessRules` для mixed/Antares/Raccoon/WR |

Контракт согласованной публикации snapshot/decision/stat/indexes и производного `AccessRules` снимка. Runtime **не** менялся. Pytest **не** требовался. Не «принято». TASK-30 остаётся blocked на review до code этого контракта.

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
- [x] Неизменяемый `capture_path`; канон отдельным replace; retain не удаляет capture при смене current
- [x] `get_rules_snapshot.local_path` = capture на hit; callers path перечислены
- [x] `observed_generation` только под lock вместе с attempt/epoch
- [x] `conflict_exhausted` → `RulesPublishConflictExhausted`, не stale-reuse и не успешный snapshot
- [x] Матрица G1–G23; historical repro ≠ safety
- [ ] GPT review; принятие контракта; code; merge/deploy

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документ по исходникам `1eefc54`; runtime не менялся; pytest не требовался |
| GPT | ещё не ревьюил |

---

## Out Of Scope

Runtime; закрытие TASK-30; смена PR #33; ingest; schedules; serve; mixed-stop; merge; retarget; deploy; исходное Test; live credentials; смена C4 policy.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | контракт публикации поколений; статус **review (не принято)** |
| 2026-09-21 | уточнение: publish_seq, accessor, CAS, диск, force/policy, закрытые решения |
| 2026-09-22 | replace в секции commit; capture vs canon; freshness до reuse; conflict_exhausted как исключение |
