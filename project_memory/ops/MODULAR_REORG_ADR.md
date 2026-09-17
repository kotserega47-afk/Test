# ADR — модульная сборка Antares / Raccoon / WR

| Мета | Значение |
|------|----------|
| **ID** | E-MOD-01 (предлагается; **не** CONFIRMED runtime до code merge) |
| **Дата** | 2026-09-17 |
| **Статус** | PROPOSED |
| **Survey** | [MODULAR_REORG_SURVEY.md](MODULAR_REORG_SURVEY.md) |
| **Миграция** | [MODULAR_REORG_MIGRATION.md](MODULAR_REORG_MIGRATION.md) |

Этот ADR **не** меняет production-настройки. Merge связанных PR в `test_main` **может** перезапустить сервис Test (автодеплой). Нормативные runtime-факты в `current_state.md` до code merge не считать «уже модульными».

---

## Контекст

Сейчас два git-репозитория и два Railway-сервиса (F25/F26):

- Test `test_main` @ `535994c…`: процесс из **смешанного** `scheduler.py` (Antares + регистрация Raccoon jobs + WE). Не называть это изолированным Antares. Факт исполнения raccoon **jobs** в этом контейнере — U12.
- Platform `develop` @ `ebbcd6c…`: Raccoon-only scheduler.

Одноимённые файлы **не** эквивалентны. WR в коде нет.

Нужно постепенно собрать **один репозиторий**, сохранив независимый запуск и изоляцию данных.

---

## Решение (целевая модель)

### Каталоги (имена после обследования)

Рекомендуемые пути в репозитории Test (канонический носитель Antares/WE):

```
core/                         # исполнение jobs, locks, logging, TG transport primitives, schedules loader
modules/antares/              # downloaders, analyzers, reporters, Wallet Editor site adapter + WE app
modules/raccoon/              # raccoon downloaders, analyzers, reports (поведение сверять с Platform develop)
modules/wr/                   # пусто до gate вопросов WR
apps/                         # тонкие entry: выбирают профиль, регистрируют ТОЛЬКО его jobs
```

До физического move допустим **логический** контур с теми же границами (`integrations/` остаётся, пока adapter не вынесен).

### Профили запуска

| Профиль | Env | Что регистрируется |
|---------|-----|-------------------|
| `antares` | `PROJECT_PROFILE=antares` | Antares jobs, Wallet Editor, Bakai, script_jobs Antares; **не** Raccoon jobs |
| `raccoon` | `PROJECT_PROFILE=raccoon` | только Raccoon jobs/commands |
| `wr` | `PROJECT_PROFILE=wr` | только WR, когда модуль появится |

Правила:

1. **Ядро не импортирует** `modules.antares` / `modules.raccoon` / `modules.wr`.
2. Точка входа (`apps/<profile>` или `scheduler.py` после флага) импортирует **один** модуль.
3. Парсер: неизвестное **значение** → ошибка (TASK-2026-09-17-02). Процесс: проверка профиля **до** побочных эффектов (TASK-2026-09-17-03) — иначе неизвестный/неподключённый профиль всё равно загрузит mixed jobs.
4. Неподдерживаемая операция → **явный отказ**, без silent fallback на другой проект.
5. Профили `raccoon` / `wr` **не** подключать к legacy `scheduler.py`, пока он регистрирует общий набор jobs. Известный, но не реализованный профиль → выход без чужих заданий (03).
6. Import модуля **без** side effects: нет browser, polling, schedule loop, `request_job`, `playwright install`, обязательных chat_id raise на import.
7. Объединять одноимённый код **только** после characterization одинакового поведения.

### Совместимость существующего Antares

| Режим | Поведение |
|-------|-----------|
| Исторический сервис Test, env **без** `PROJECT_PROFILE` | пока cutover: **mixed** scheduler (не ярлык `antares-only`); Raccoon jobs в коде не вырезать тем же PR, что парсер |
| `PROJECT_PROFILE=antares` на legacy entry до isolated register | **не** считать изоляцией; gate (03) должен отказать или явно выбрать documented mixed path |
| Новые сервисы | isolated entry + обязательный профиль; не legacy mixed |
| Целевое состояние | unset запрещён; Raccoon jobs сняты с процесса Test отдельным PR |

Смешанный процесс Test — **долг**. Риск двойных выгрузок Raccoon (U2/U10/U12) **не закрыт** SUCCESS-деплоем.

### Wallet Editor

Разделение:

| Слой | Ответственность | Где |
|------|-----------------|-----|
| Orchestration | очередь, профили операторов, registry, HOLD, Auto-Enable plan/run, запрет retry неизвестного Save | `modules/antares/wallet_editor/` (app) + куски `core` только для queue/lock primitives если реально общие |
| Site adapter | locators, чтение формы, терминал/chips, Save, verify reread, opening token | `modules/antares/site/` сейчас `automation/engine.py` + `wallet_terminal_field.py` |

WR **не** получает Antares adapter «по похожему UI».

Raccoon **не** получает WE, пока нет подтверждённого UI и ops-требования.

Сохранить инварианты survey § 4.3 (unread ≠ empty; add fail blocks set_status/Save; unsaved ≠ OK; Save verify; ready bound to card+opening).

### Изоляция хранилищ

**Выбор: отдельные runtime-контуры на процесс, не «один DB + project_id в логах».**

| Ресурс | Способ | Почему |
|--------|--------|--------|
| Процесс / Railway service | 1 сервис = 1 `PROJECT_PROFILE` | независимый выпуск: сегодня разные репо/ветки (F25/F26); pin SHA **не** считать уже включённым |
| Telegram token | отдельный бот на профиль | E-WE-01 уже: один polling на token |
| Browser `storage_state` | каталог `{STATE_DIR}/{profile}/auth/…` | сейчас `/tmp/auth_state_raccoon.json` совпадает в T и P |
| Job locks | `{STATE_DIR}/{profile}/locks/{job_type}.lock` | PID lock Test; при переносе Raccoon — тот же паттерн, **другие** пути |
| WE registry PG | **отдельная БД** (или отдельный schema + role, которому видны только свои таблицы) **плюс** запрет подключения Antares-DB из raccoon/wr process | `project_id` колонка **не** доказательство изоляции |
| Dropbox paths | отдельные `DROPBOX_*` / rules files на профиль | один `rules.xlsx` на смешанный процесс сегодня связывает проекты |
| Выгрузки | `{STATE_DIR}/{profile}/downloads` вместо голого `/tmp/hourly_raccoon` | коллизии payin.xlsx |
| Rules / schedules / TG routes | workbook и route keys **на профиль**; ядро читает snapshot, переданный профилем | нет общего « hop на чужой chat» |

`project_id` в строках логов/events — **телеметрия**, не граница безопасности.

Карты, partner id, имена операторов — **per project**. Join между проектами запрещён в коде ядра.

### Сообщения и jobs в ядре

Ядро может содержать:

- `request_job` / PID lock **с namespace профиля** в пути;
- schedule loop, который итерирует **уже отфильтрованный** список job_type;
- send_text(chat_id) без знания route catalog проекта.

Ядро **не** содержит списков `raccoon_wallet` vs `wallet` как бизнес-знаний — их регистрирует модуль.

---

## Последствия

- Три сервиса не обязаны обновляться одним merge: сегодня Test следит за `test_main`, Raccoon — за Platform `develop`. Способ после monorepo — `MIGRATION` § Автодеплой (проверяемые варианты, не предполагаемый pin SHA).
- PID locks не координируют контейнеры (`MIGRATION` § Блокировки).
- Перенос Raccoon — портирование Platform develop, не copy-paste Test `integrations/raccoon_*`.

---

## Отклонённые варианты

| Вариант | Почему нет |
|---------|------------|
| Один процесс, `project_id` в каждом job | dual feature bleed; один token; общий `/tmp` |
| Считать Test raccoon = prod Raccoon | blob DIFF; другой scheduler |
| Общий WE adapter для WR | UI не подтверждён |
| Слить Platform `main` как базу | main ≠ develop (сотни коммитов) |
