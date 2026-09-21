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

Зафиксировать поколение публикации, CAS после compute вне lock, диск/identity, lock без deadlock и матрицу проверок — без смены strict/shadow/legacy.

---

## Success Criteria

- [x] Единый PublishedState: snapshot, decision, workbook/stat как атрибут, indexes, монотонный `generation` ≠ file stat
- [x] AccessRules CAS к `provider_generation`; старый reader не откатывает опубликованное
- [x] Readers/writers/invalidate перечислены по исходникам
- [x] Успешный reload = commit нового поколения, затем clocks reset
- [x] Attempt/epoch: два force, invalidate во время compute, ошибка publish
- [x] Staging workbook + identity только у победителя commit
- [x] Admission lock не на I/O/compute; порядок lock без deadlock
- [x] Policy semantics не менять; влияние на mixed/callers честно
- [x] Матрица G1–G9; repro TASK-30 сохранить как дефект
- [x] Файлы будущего code и open decisions
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
