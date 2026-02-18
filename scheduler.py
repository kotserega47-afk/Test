# scheduler.py

import os
import time
import threading
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

# === Импорты задач ===
from integrations.downloader import run_download                 # ~40 минут
from integrations.hourly_downloader import run_hourly_cycle      # каждый час
from analyzers.hourly_report import run_hourly_report            # отчёт после hourly
from integrations.downloader_wallets import run_wallet_cycle     # каждые N минут
from integrations.bakai_monitor_playwright import run_rate_monitor_safe


# ================= LOGS =================

def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log_main = _mk("MAIN")
log_wallet = _mk("WALLET")
log_hourly = _mk("HOURLY")
log_rate = _mk("RATE")

MSK = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    return datetime.now(MSK)


# ================= ENV UTILS =================

def env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None else str(v)


def parse_csv_dirs(value: str) -> list[str]:
    parts = [p.strip() for p in (value or "").split(",")]
    return [p for p in parts if p]


# ================= TIME WINDOWS =================

def in_hour_window(hour: int, start_hour: int, end_hour: int) -> bool:
    """
    Окно по часам (MSK), включая переход через полночь.
    Правило:
      - start_hour inclusive
      - end_hour inclusive (как в твоём старом коде)
    Поддержка:
      - end_hour == 24 трактуем как 23
      - end_hour == 0 может означать "до полуночи", т.е. 00:00 включительно (час 0)
    Примеры:
      start=8, end=24 -> 8..23
      start=9, end=0  -> 9..23 и 0
    """
    if end_hour == 24:
        end_hour = 23

    # обычный случай
    if start_hour <= end_hour:
        return start_hour <= hour <= end_hour

    # переход через полночь (например 9..0)
    return (hour >= start_hour) or (hour <= end_hour)


def sleep_smart(seconds: int, step: int = 2):
    """Спим небольшими шагами, чтобы поток был отзывчив (и логика рестарта не запаздывала)."""
    remaining = max(0, int(seconds))
    while remaining > 0:
        s = min(step, remaining)
        time.sleep(s)
        remaining -= s


# ================= HOUSEKEEPING (TMP CLEANUP) =================

DEFAULT_TMP_DIRS = [
    "/tmp/downloads",
    "/tmp/wallet_handler",
    "/tmp/hourly",
    "/tmp/rules",
    "/tmp",
]


def _should_keep_auth(path: Path, keep_auth_state: bool) -> bool:
    if not keep_auth_state:
        return False
    name = path.name
    # бережём Playwright storage state / auth state (чтобы не ломать логин)
    return name.startswith("auth_state") or name.endswith("_auth.json")


def cleanup_tmp_ttl(dirs: list[str], ttl_minutes: int, keep_auth_state: bool, log):
    """
    TTL-clean: удаляет всё старше TTL. Корневые dirs не трогаем, чистим содержимое.
    """
    now_ts = time.time()
    ttl_sec = max(1, ttl_minutes) * 60

    removed_files = 0
    removed_dirs = 0
    skipped = 0

    for d in dirs:
        base = Path(d)
        try:
            if not base.exists() or not base.is_dir():
                continue

            # сначала файлы, потом директории (чтобы rmtree не дёргать лишний раз)
            for p in sorted(base.rglob("*"), key=lambda x: (x.is_dir(), str(x))):
                try:
                    if _should_keep_auth(p, keep_auth_state):
                        skipped += 1
                        continue

                    try:
                        st = p.stat()
                    except FileNotFoundError:
                        continue

                    age = now_ts - st.st_mtime
                    if age < ttl_sec:
                        continue

                    if p.is_file() or p.is_symlink():
                        p.unlink(missing_ok=True)
                        removed_files += 1
                    elif p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                        removed_dirs += 1
                except Exception:
                    skipped += 1
                    continue

        except Exception:
            log.exception(f"❌ Housekeeping(TTL): ошибка при обработке {d}")

    log.info(
        f"🧹 TTL-clean: удалено файлов={removed_files}, папок={removed_dirs}, пропущено={skipped}, ttl={ttl_minutes} мин"
    )


def cleanup_tmp_full(dirs: list[str], keep_auth_state: bool, log):
    """
    Полная чистка перед рестартом: удаляет ВСЁ содержимое dirs (кроме auth_state*, если keep_auth_state=True).
    """
    removed_files = 0
    removed_dirs = 0
    skipped = 0

    for d in dirs:
        base = Path(d)
        try:
            if not base.exists() or not base.is_dir():
                continue

            for p in sorted(base.iterdir(), key=lambda x: (x.is_dir(), str(x))):
                try:
                    if _should_keep_auth(p, keep_auth_state):
                        skipped += 1
                        continue

                    if p.is_file() or p.is_symlink():
                        p.unlink(missing_ok=True)
                        removed_files += 1
                    else:
                        shutil.rmtree(p, ignore_errors=True)
                        removed_dirs += 1
                except Exception:
                    skipped += 1
                    continue

        except Exception:
            log.exception(f"❌ Housekeeping(FULL): ошибка при обработке {d}")

    log.info(
        f"🧽 FULL-clean: удалено файлов={removed_files}, папок={removed_dirs}, пропущено={skipped}"
    )


def housekeeping_loop():
    """
    TTL-clean каждые TMP_CLEAN_INTERVAL_MIN минут.
    По задаче: каждые 120 минут.
    """
    interval_min = env_int("TMP_CLEAN_INTERVAL_MIN", 120)
    ttl_min = env_int("TMP_FILE_TTL_MIN", 360)  # дефолт 6 часов (можешь менять)
    keep_auth_state = env_bool("KEEP_AUTH_STATE", True)

    dirs_override = parse_csv_dirs(env_str("TMP_CLEAN_DIRS", ""))
    dirs = dirs_override or DEFAULT_TMP_DIRS

    log_main.info(
        f"🧹 Housekeeping: interval={interval_min} мин, ttl={ttl_min} мин, keep_auth_state={keep_auth_state}, dirs={dirs}"
    )

    while True:
        try:
            cleanup_tmp_ttl(dirs=dirs, ttl_minutes=ttl_min, keep_auth_state=keep_auth_state, log=log_main)
        except Exception as e:
            log_main.exception(f"❌ Housekeeping(TTL): {e}")
        sleep_smart(interval_min * 60, step=5)


# ================= AUTO RESTART (BY TIMES) =================

def parse_restart_times(value: str) -> list[tuple[int, int]]:
    """
    "12:00,01:00" -> [(12,0), (1,0)]
    """
    out: list[tuple[int, int]] = []
    for part in (value or "").split(","):
        t = part.strip()
        if not t:
            continue
        try:
            h_s, m_s = t.split(":")
            h, m = int(h_s), int(m_s)
            if 0 <= h <= 23 and 0 <= m <= 59:
                out.append((h, m))
        except Exception:
            continue
    return out


def auto_restart_loop():
    enabled = env_bool("AUTO_RESTART_ENABLED", True)
    if not enabled:
        log_main.info("🔕 AutoRestart выключен")
        return

    times = parse_restart_times(env_str("AUTO_RESTART_TIMES", "12:00,01:00"))
    if not times:
        log_main.info("🔕 AutoRestart выключен (нет времён)")
        return

    keep_auth_state = env_bool("KEEP_AUTH_STATE", True)
    dirs_override = parse_csv_dirs(env_str("TMP_CLEAN_DIRS", ""))
    dirs = dirs_override or DEFAULT_TMP_DIRS

    marker_file = Path("/tmp/last_restart_marker.txt")

    log_main.info(f"♻️ AutoRestart: times(MSK)={times}")

    while True:
        now = now_msk()
        today_key = now.strftime("%Y-%m-%d")

        for h, m in times:
            if now.hour == h and now.minute == m:
                last_marker = None
                if marker_file.exists():
                    last_marker = marker_file.read_text().strip()

                # если уже рестартились сегодня в этот слот — пропускаем
                marker_value = f"{today_key}-{h:02d}:{m:02d}"
                if last_marker == marker_value:
                    break

                try:
                    log_main.info("♻️ AutoRestart: FULL-clean перед рестартом…")
                    cleanup_tmp_full(dirs=dirs, keep_auth_state=keep_auth_state, log=log_main)

                    marker_file.write_text(marker_value)

                except Exception as e:
                    log_main.exception(f"❌ AutoRestart cleanup: {e}")

                log_main.info("♻️ AutoRestart: выходим из процесса…")
                time.sleep(2)
                os._exit(99)

        time.sleep(10)



# ================= GENERIC JOB RUNNER =================

@dataclass(frozen=True)
class JobConfig:
    name: str
    interval_min: int
    start_hour: int
    end_hour: int


def run_periodic_job(cfg: JobConfig, func, log):
    log.info(
        f"🟢 {cfg.name}: interval={cfg.interval_min} мин, window={cfg.start_hour}:00–{cfg.end_hour}:00 (MSK)"
    )

    while True:
        now = now_msk()
        if not in_hour_window(now.hour, cfg.start_hour, cfg.end_hour):
            # не шумим каждую секунду
            if now.minute % 10 == 0:
                log.info(f"⏸ {cfg.name}: вне окна, текущее время {now.strftime('%H:%M')} (MSK)")
            sleep_smart(60, step=5)
            continue

        try:
            log.info(f"🚀 {cfg.name} старт (MSK {now.strftime('%H:%M:%S')})")
            func()
            log.info(f"✅ {cfg.name} завершён")
        except Exception as e:
            log.exception(f"❌ {cfg.name}: {e}")

        sleep_smart(cfg.interval_min * 60, step=5)


# ================= Rate Monitor =================

def run_rate_monitor():
    # каждые 10 минут, окно 06:00–23:55 (MSK)
    log_rate.info("🟢 RateMonitor: interval=10 мин, window=06:00–23:55 (MSK)")

    while True:
        now = now_msk()
        h, m = now.hour, now.minute

        in_window = (h > 6 or (h == 6 and m >= 0)) and (h < 23 or (h == 23 and m <= 55))
        if not in_window:
            if now.minute % 10 == 0:
                log_rate.info("⏸ RateMonitor: вне окна 06:00–23:55 (MSK)")
            sleep_smart(60, step=5)
            continue

        try:
            log_rate.info(f"🚀 RateMonitor (MSK {now.strftime('%H:%M:%S')})")
            run_rate_monitor_safe()
            log_rate.info("✅ RateMonitor завершён")
        except Exception as e:
            log_rate.exception(f"❌ RateMonitor: {e}")

        sleep_smart(10 * 60, step=5)


# ================= HOURLY (HourlyDownloader → HourlyReport) =================

def run_hourly_loop():
    """
    HourlyReporter: каждый час. Окно 09:00–00:00 (MSK).
    Триггер: смена часа (чтобы не выстрелить сразу при старте посреди часа).
    """
    log_hourly.info("🟢 HourlyReporter: каждый час, window=09:00–00:00 (MSK)")

    last_run_hour = now_msk().hour

    while True:
        now = now_msk()
        hour = now.hour

        # окно 09..23 и 00
        if not in_hour_window(hour, 9, 0):
            if now.minute % 10 == 0:
                log_hourly.info("⏸ HourlyReporter: вне окна, ждём 09:00 (MSK)")
            sleep_smart(60, step=5)
            continue

        # запуск строго при смене часа
        if hour != last_run_hour:
            last_run_hour = hour
            try:
                log_hourly.info(f"🚀 HourlyDownloader (MSK {now.strftime('%H:%M:%S')})")
                run_hourly_cycle()

                log_hourly.info(f"🚀 HourlyReport (MSK {now.strftime('%H:%M:%S')})")
                run_hourly_report()

                log_hourly.info("✅ HourlyReporter завершён")
            except Exception as e:
                log_hourly.exception(f"❌ HourlyReporter: {e}")

            # защита от повторного срабатывания в пограничный момент
            sleep_smart(60, step=5)

        sleep_smart(5, step=1)


# ================= START THREADS =================

def main():
    log_main.info(f">>> SYSTEM LOCAL: {datetime.now()}")
    log_main.info(f">>> NOW_MSK: {now_msk()}")

    # 1) Housekeeping TTL-clean (каждые 120 минут по задаче)
    threading.Thread(target=housekeeping_loop, daemon=True).start()

    # 2) AutoRestart в 12:00 и 01:00 (MSK)
    threading.Thread(target=auto_restart_loop, daemon=True).start()

    # 3) MainDownloader — каждые 40 минут, окно 08:00–23:00 (по-умолчанию)
    main_cfg = JobConfig(
        name="MainDownloader",
        interval_min=env_int("MAIN_INTERVAL_MIN", 40),
        start_hour=env_int("MAIN_START_HOUR", 8),
        end_hour=env_int("MAIN_END_HOUR", 24),  # 24 -> 23
    )
    threading.Thread(
        target=run_periodic_job,
        args=(main_cfg, run_download, log_main),
        daemon=True
    ).start()

    # 4) WalletDownloader — каждые 3 минуты, окно 08:00–23:00 (по-умолчанию)
    wallet_cfg = JobConfig(
        name="WalletDownloader",
        interval_min=env_int("WALLET_INTERVAL_MIN", 3),
        start_hour=env_int("WALLET_START_HOUR", 8),
        end_hour=env_int("WALLET_END_HOUR", 24),  # 24 -> 23
    )
    threading.Thread(
        target=run_periodic_job,
        args=(wallet_cfg, run_wallet_cycle, log_wallet),
        daemon=True
    ).start()

    # 5) RateMonitor
    threading.Thread(target=run_rate_monitor, daemon=True).start()

    # 6) HourlyDownloader + HourlyReport
    threading.Thread(target=run_hourly_loop, daemon=True).start()

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
