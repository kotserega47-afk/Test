# Decisions — analizis (project-os supplement)

| Мета | Значение |
|------|----------|
| **Документ** | Supplement to `project_memory/decisions.md` |
| **Последнее обновление** | 2026-06-05 |
| **Назначение** | Решения, зафиксированные в project-os discovery / postmortem |

Полный реестр E#/I#: `project_memory/decisions.md` в корне репозитория.

---

## Infrastructure decisions (E-INFRA-*)

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E-INFRA-01 | 2026-06-02 | `STATE_DIR` default `/data/state` (Railway Volume); local persistent state (locks, events, observation JSONL) | CONFIRMED | `project_memory/decisions.md` |
| **E-INFRA-02** | **2026-06-05** | **Railway containers must run through tini reaper because Playwright/Chromium jobs create child processes that otherwise accumulate as zombies.** Start: `/usr/bin/tini -s -- /opt/venv/bin/python scheduler.py` via `railway.toml` `[deploy].startCommand` (not `[services.main].start`). Package: `tini` in `nixpacks.toml` `aptPkgs`. | **CONFIRMED** | INC-2026-06-05; commits `affc689`, `5e31560`; `railway.toml`, `nixpacks.toml` |

### E-INFRA-02 — контекст

- **Проблема:** PID 1 = `python scheduler.py` → no SIGCHLD reaping → `[chrome-headless] <defunct>` zombies → `RuntimeError: can't start new thread` in WalletEditor.
- **Дополнение (не замена tini):** `core/playwright_cleanup.py` + `observability/process_resource_health.py` (commit `affc689`).
- **Инвариант:** Production deploy MUST show `tini` as PID 1; `python scheduler.py` as child.

---

## Operations decisions (E-OPS-*) — incident-related

| ID | Дата | Решение | Статус | Источник |
|----|------|---------|--------|----------|
| E-OPS-04 | 2026-06-05 | Playwright callers close page/context/browser in `finally` via `close_playwright_stack` (never raises) | CONFIRMED | `core/playwright_cleanup.py`; INC-2026-06-05 |
| E-OPS-05 | 2026-06-05 | Process resource health: periodic log of `process_count`, `thread_count`, `zombie_count`, `zombie_chrome_count`; CRITICAL on thresholds; **no autokill/autorestart** | CONFIRMED | `observability/process_resource_health.py`; `scheduler.py` |

---

## История

| Дата | Событие |
|------|---------|
| 2026-06-05 | INC-2026-06-05 postmortem — E-INFRA-02, E-OPS-04, E-OPS-05 |
