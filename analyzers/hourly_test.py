from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from analyzers.hourly_report import run_hourly_report_for_interval
import time
MSK = ZoneInfo("Europe/Moscow")


def test_day(date_str):
    """Тест отчёта за сутки с отправкой в Telegram."""
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MSK)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    end = d.replace(hour=23, minute=59, second=59, microsecond=0)

    run_hourly_report_for_interval(start, end, send=True)


def test_hour(date_str, hour):
    """Тест отчёта за один час."""
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MSK)
    start = d.replace(hour=hour, minute=0, second=0, microsecond=0)
    end = start.replace(minute=59, second=59)

    run_hourly_report_for_interval(start, end, send=True)


def test_last_hour():
    """Отчёт за последний час — как боевой, но запускается вручную."""
    now = datetime.now(MSK)
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    end = start.replace(minute=59, second=59)

    run_hourly_report_for_interval(start, end, send=True)
    time.sleep(1)

if __name__ == "__main__":
    import sys

    print("ARGS:", sys.argv)

    cmd = sys.argv[1]

    if cmd == "test_day":
        test_day(sys.argv[2])
    elif cmd == "test_hour":
        test_hour(sys.argv[2], int(sys.argv[3]))
    elif cmd == "test_last_hour":
        print("RUNNING test_last_hour")
        test_last_hour()
    else:
        print("Неизвестная команда:", cmd)