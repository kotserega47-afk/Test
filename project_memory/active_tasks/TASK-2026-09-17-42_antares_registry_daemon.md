# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-42 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-41 close `655e14aac8413fce44a9d3ece1be653441ac700a` (review `6ae8f88de2d146e9a550ae750745214c7c36c136`, runtime `c5ad702…`, Draft PR #44); [MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md](../ops/MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md); [DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) § 1.7 / O4b |
| **PR** | Draft (этот срез) `feat/task-2026-09-17-42-antares-registry-daemon-join`, base `feat/task-2026-09-17-41-antares-profile-worker-stop` |
| **Риск** | medium: fire-and-forget `we-registry-*`; sender handoff из append; не полный graceful |

Docs-контракт isolated drain/join daemon `schedule_registry_append`. Runtime **не** менялся. TASK-41 повторно не реализовывать. TASK-39/40/41 повторно не закрывать. Merge/deploy нет.

---

## Goal

Зафиксировать учёт каждой принятой `we-registry-*` операции, terminal = join потока, freeze после конца producers, remainder на deadline; durable pending ≠ конец I/O.

---

## Success Criteria

- [x] Обследованы prepare / schedule / append / outbox / locks / child sender / delayed_cleanup / replay / patch / mirror
- [x] Принятие = register + `Thread.start`; terminal = join; бизнес-outbox отдельно
- [x] Freeze новой операции после `unfinished_tasks==0`; финальный snapshot после freeze
- [x] Cancel wait не отменяет I/O; deadline = failure без retry
- [x] Порядок: оба WE join и daemon join до sender; helper не подключать
- [x] Матрица R1–R14
- [ ] GPT review
- [ ] runtime (следующий code-срез)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | контракт по production paths на `c5ad702…`; runtime нет; pytest не требовался |
| GPT | pytest **не** запускал (ожидается review документа) |

---

## Out Of Scope

runtime; sender; executor shutdown; helper; serve; mixed-stop; merge/retarget/deploy; исходное Test; повторное закрытие TASK-39/40/41.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | docs-контракт registry daemon drain/join; статус **review (подготовлено)** |
