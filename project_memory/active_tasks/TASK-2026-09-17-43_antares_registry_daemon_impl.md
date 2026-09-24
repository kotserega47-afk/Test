# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-43 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-42 close `041825f3d688847c917410e4d8f3cacb6c92073b` (review `d0e73d5ba4bc465e2245d18c206cf6ad99ebf9b1`, Draft PR #45); [MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md](../ops/MODULAR_REORG_ANTARES_REGISTRY_DAEMON.md) |
| **PR** | Draft [#46](https://github.com/deniskotdavydov1991-wq/Test/pull/46) `feat/task-2026-09-17-43-antares-registry-daemon-join-impl`, base `feat/task-2026-09-17-42-antares-registry-daemon-join` |
| **Риск** | medium: process-local daemon ops; race start vs finally; не полный graceful |

Code: process-local accounting + `wait_isolated_registry_daemon_ops`. Helper не подключён. Sender/executor/serve вне среза. TASK-42 повторно не закрывать. Merge/deploy нет.

---

## Goal

Учесть каждую принятую `we-registry-*` операцию (REGISTERED→STARTED→TERMINAL), freeze после WE producers, join через `asyncio.to_thread` срезами; durable outbox не переписывать.

---

## API (фактические имена)

| Имя | Роль |
|-----|------|
| `RegistryDaemonLifecycle` | REGISTERED / STARTED / TERMINAL |
| `schedule_registry_append` | register→start→STARTED; start fail reap; freeze→`IsolatedRegistryDaemonCreateRejected` |
| `wait_isolated_registry_daemon_ops(admission, *, producers_complete, timeout=None)` | preconditions + freeze + join STARTED |
| `snapshot_isolated_registry_daemon_ops()` | наблюдение |
| `RegistryDaemonRemainder` / `IsolatedRegistryDaemonStopError` | failure без retry |
| `_reset_registry_daemon_ops_for_tests()` | test-only |

---

## Success Criteria

- [x] Accounting lock; freeze; lifecycle; remainder
- [x] Fast-finish race: TERMINAL не перетирается STARTED
- [x] Reap после наблюдения TERMINAL
- [x] R1–R15 + inconsistent REGISTERED
- [x] Helper не wired
- [ ] GPT review
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

sender; executor shutdown; helper; serve; mirror; replay arch; delayed_cleanup join; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | code accounting/join; статус **review (подготовлено)** |
