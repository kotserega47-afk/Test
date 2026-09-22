# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-32 |
| **Статус** | review (подготовлено; не принято; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-31 close `3d567911e8fc69e4ca9fe54f3571e6657f6d31b2` (контракт `8ef2838…`); TASK-30 Draft PR #33 HEAD `1eefc54…` (**не** закрыт) |
| **PR** | Draft [#35](https://github.com/deniskotdavydov1991-wq/Test/pull/35) `feat/task-2026-09-17-32-rules-publish-generation`, base `feat/task-2026-09-17-31-rules-publish-generation` |
| **Риск** | high: общий `rules_provider` + `AccessRules` для mixed/Antares/Raccoon/WR |

Реализация принятого контракта [MODULAR_REORG_RULES_PUBLISH_GENERATION.md](../ops/MODULAR_REORG_RULES_PUBLISH_GENERATION.md). TASK-30 не закрывать автоматически. PR #33 не переназначать.

---

## Goal

Согласованно публиковать snapshot/decision/indexes/capture одним PublishedState с монотонным `publish_seq`.

---

## Success Criteria

- [x] PublishedState + `publish_seq`; invalidate не обнуляет
- [x] attempt/epoch, 3 повтора, `RulesPublishConflictExhausted`
- [x] `get_published_state`; eager indexes
- [x] freshness до AccessRules reuse
- [x] CAS: provider lock → instance lock
- [x] immutable capture в уникальном каталоге запуска; без sweep
- [x] проверка актуальности + canon replace в одной секции
- [x] isolated: fresh/existing vs stale_reuse vs reject
- [x] парные callers на accessor
- [ ] GPT review; merge/deploy

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | review-fix: provider/identity/C4/G **60 passed**, exit 0; admission/handlers/access_guard **123 passed**, exit 0; pair-caller settings/hourly **34 passed**, exit 0; clocks/inventory/registration/dispatch/export **30 passed**, exit 0; lifecycle+boot **52 passed**, exit 0; Python 3.12.10. Наборы не суммировать. |
| GPT | ещё не ревьюил |

---

## Out Of Scope

Закрытие TASK-30; retarget PR #33; ingest; schedules; serve; merge; deploy; исходное Test; live credentials; leases; очистка published capture.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | реализация контракта TASK-31; Draft PR; статус **review** |
| 2026-09-22 | review-fix: stale_reuse под lock, `_last_rules_wb` epoch, EXISTING+freshness, `parse_integral_id`, G3/G14/G9, удаление `_download_rules_workbook_atomic`; TASK-32 не закрыт |
