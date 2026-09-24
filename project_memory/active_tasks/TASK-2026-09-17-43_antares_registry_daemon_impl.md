# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-43 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-42 close `041825f3d688847c917410e4d8f3cacb6c92073b` (review `d0e73d5ba4bc465e2245d18c206cf6ad99ebf9b1`, Draft PR #45); [MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md](../ops/MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md) |
| **PR** | Draft [#46](https://github.com/deniskotdavydov1991-wq/Test/pull/46) `feat/task-2026-09-17-43-antares-registry-daemon-join-impl`, base `feat/task-2026-09-17-42-antares-registry-daemon-join` |
| **Риск** | medium: process-local daemon ops; race start vs finally; не полный graceful |

Review **пройден**. GPT проверил PR #46 / code / diff / tests на полном SHA `01e0c55dc84b9e6be78ff20f6b1f5b58be017601`. Блокирующих нарушений контракта TASK-42 не обнаружено. GPT pytest **не** запускал. Cursor: **21** daemon join; **52** profile+executor+AE; **22** outbox+timeout (наборы не суммировать). Этот docs-коммит — закрытие TASK-43; runtime между accepted HEAD и close **не** менялся.

Реализованы process-local `RegistryDaemonLifecycle`, immediate identity-safe TERMINAL reap, `wait_isolated_registry_daemon_ops` (freeze + snapshot + cancellable join slices). Bounded TERMINAL history подтверждён. Helper **не** wired. Это **не** полный graceful. `run_ptb_lifecycle` **не** подключать. Sender / executor shutdown / serve — следующие срезы. TASK-39–42 повторно не закрывать. PR #46 остаётся Draft. Merge/deploy нет.

---

## Goal

Учесть каждую принятую `we-registry-*` операцию (REGISTERED→STARTED→TERMINAL), freeze после WE producers, join через `asyncio.to_thread` срезами; durable outbox не переписывать; `_daemon_ops` — active accounting, не история завершённых runs.

---

## API (фактические имена)

| Имя | Роль |
|-----|------|
| `RegistryDaemonLifecycle` | REGISTERED / STARTED / TERMINAL |
| `schedule_registry_append` | register→start→STARTED; start fail reap; freeze→`IsolatedRegistryDaemonCreateRejected` |
| `_mark_daemon_terminal(op)` | exact-op TERMINAL + identity pop (`current is op`) |
| `wait_isolated_registry_daemon_ops(admission, *, producers_complete, timeout=None)` | preconditions + freeze + join STARTED; joined = snapshot observed |
| `snapshot_isolated_registry_daemon_ops()` | наблюдение |
| `RegistryDaemonRemainder` / `IsolatedRegistryDaemonStopError` | failure без retry; normal TERMINAL excluded |
| `_reset_registry_daemon_ops_for_tests()` | test-only |

---

## Success Criteria

- [x] Accounting lock; freeze; lifecycle; remainder
- [x] Fast-finish race: TERMINAL не перетирается STARTED
- [x] Exact-op identity reap; bounded TERMINAL history
- [x] R1–R15 + inconsistent REGISTERED
- [x] Helper не wired
- [x] GPT review кода/diff/tests на `01e0c55…`; pytest GPT не запускал
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **21** `test_antares_registry_daemon_join.py`; **52** profile+executor+AE; **22** outbox+timeout на `01e0c55…`; наборы не суммировать |
| GPT | review PR #46 / code / diff / tests на `01e0c55…`; pytest **не** запускал |

---

## Out Of Scope

sender; executor shutdown; helper; serve; mirror; replay arch; delayed_cleanup join; merge/retarget/deploy; исходное Test; повторное закрытие TASK-39–42.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | code accounting/join; статус **review (подготовлено)** |
| 2026-09-24 | remainder excludes normal TERMINAL; immediate identity reap; GPT review `01e0c55…`; закрытие docs |
