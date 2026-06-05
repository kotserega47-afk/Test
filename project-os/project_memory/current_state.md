# Current State — analizis (project-os supplement)

| Мета | Значение |
|------|----------|
| **KB версия** | v1.5-supplement |
| **Снимок на дату** | 2026-06-05 |
| **Среда** | Production incident INC-2026-06-05 resolved in code + deploy |

Полный operational snapshot: `project_memory/current_state.md` в корне репозитория.

---

## Deploy configuration (post INC-2026-06-05)

| Поле | Значение | Статус |
|------|----------|--------|
| **Deploy target** | Railway service `file-analyzer` | CONFIRMED |
| **PID 1** | `/usr/bin/tini -s` | CONFIRMED (E-INFRA-02) |
| **App process** | `/opt/venv/bin/python scheduler.py` (child of tini) | CONFIRMED |
| **Start command (effective)** | `/usr/bin/tini -s -- /opt/venv/bin/python scheduler.py` | CONFIRMED — `railway.toml` `[deploy].startCommand` |
| **Deprecated / ignored** | `[services.main].start` in `railway.toml` | CONFIRMED ignored by Railway |

---

## Runtime hardening (2026-06-05)

| Component | State |
|-----------|-------|
| Playwright cleanup | `close_playwright_stack` in `finally` on all 7 Playwright callers |
| Process observability | `[ProcessResource/health]` every ~600s in `schedule_loop`; CRITICAL thresholds |
| Business logic | WalletEditor, Excel, Telegram routing, Antares actions, Conversion — **unchanged** |

---

## INC-2026-06-05 — resolved

| | Before | After |
|---|--------|-------|
| PID 1 | `python scheduler.py` | `tini` |
| `ps -eLf` | 959 (~56 min) | 61 (~2 h) |
| Zombie chrome-headless | 52 | 0 |

**Commits:** `affc689`, `5e31560`

---

## История изменений

| Дата | Событие |
|------|---------|
| 2026-06-05 | **INC-2026-06-05** — WalletEditor `RuntimeError: can't start new thread`; root cause: no init reaper + Chromium zombies. Fix: tini PID 1 (`5e31560`), Playwright `finally` cleanup + process resource health (`affc689`). Documented in PROJECT_BOOK, PROJECT_OWNER_BOOK, decisions E-INFRA-02. |
| 2026-06-04 | Wallet hang Patch A/B + Job Health Guard C1 (see `project_memory/current_state.md`) |
| 2026-06-02 | Conversion / Raccoon / Payout Rules V2 cutovers (see main current_state) |
