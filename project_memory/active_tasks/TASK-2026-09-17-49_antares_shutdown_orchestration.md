# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-49 |
| **Статус** | review (подготовлено; docs-only; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-48 close `90cda7c92e56df3657c293f2e6de6ee65d2426c0` (accepted runtime `7f6b5a8c211658fba92e2f6b98320b3443935cb6`, Draft PR #51); [SHUTDOWN_ORCHESTRATION.md](../ops/MODULAR_REORG_ANTARES_SHUTDOWN_ORCHESTRATION.md); [DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md); [REGISTRY_DAEMON.md](../ops/MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md); [SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [STARTSTOP.md](../ops/MODULAR_REORG_ANTARES_STARTSTOP.md) |
| **PR** | Draft [#52](https://github.com/deniskotdavydov1991-wq/Test/pull/52) `docs/task-2026-09-17-49-antares-shutdown-orchestration`, base `feat/task-2026-09-17-48-antares-sender-full-stop` @ `90cda7c…` |
| **Риск** | high: общий helper order; PTB producer wait; process-global executor; D30/registry vs WE; O10 открыт |

CODE: **нет**. Discovery + docs-only контракт соединения принятых stop-срезов с `run_ptb_lifecycle`. Runtime/`tests`/`requirements` **не** менять. Реализацию автоматически **не** начинать. TASK-39–48 повторно не закрывать.

Обследованный SHA: `90cda7c92e56df3657c293f2e6de6ee65d2426c0` (TASK-48 docs-close; runtime stop APIs на `7f6b5a8…`).

---

## Goal

Зафиксировать проверяемый порядок общей остановки isolated Antares:

`request_antares_stop` → wait PTB producers → Accepted/continuation/WE items → WE stop → registry join → sender full stop → executor shutdown → PTB cleanup,

с одним абсолютным deadline, разделением outcomes и явными противоречиями TASK-39 §5 vs TASK-42/48.

---

## Success Criteria

- [x] Обследованы `application_lifecycle`, WorkAdmission, Accepted Futures, WE stop, registry daemon, sender stop, job executor на `90cda7c…`
- [x] Таблица фаз: предусловия → действие → доказательство → deadline/cancel/error → следующая фаза
- [x] Сохранён `seal()` → `stop.set()`; Accepted work не cancel
- [x] PTB producers: `stop.set()` / empty queue / no polling ≠ proof
- [x] Порядок согласован с TASK-41/43 (WE stop → registry → sender); freeze/registry/sender предусловия явны
- [x] Executor ownership + production shutdown constraints (не test reset)
- [x] Один overall deadline; remaining budget; согласование с owner HTTP session TASK-48
- [x] Cancel / repeat / partial failure; разделение business / work / resources / PTB / overall
- [x] Матрица будущих Event/barrier; code slices; указатели project_memory
- [ ] GPT review документа
- [ ] runtime/helper wiring (отдельные future срезы; не этот PR)
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

Runtime/tests/requirements; helper wiring в этом PR; serve/polling cutover; live Telegram; mixed-stop O10; merge/Ready/deploy/Railway; исходное дерево Test; повторное закрытие TASK-39–48; автостарт code.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-30 | docs-контракт shutdown orchestration; Draft PR; ожидание GPT review |
