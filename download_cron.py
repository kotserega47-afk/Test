# download_cron.py
import time
from datetime import datetime, timedelta
from utils.logger import logger
from integrations.downloader import run_download

def _sleep_until_next_hour():
    now = datetime.now()
    nxt = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    delta = (nxt - now).total_seconds()
    logger.info(f"⏰ Сплю {int(delta)} сек до следующего часа ({nxt.strftime('%H:%M')})")
    time.sleep(max(1, int(delta)))

if __name__ == "__main__":
    while True:
        try:
            run_download()
        except Exception as e:
            logger.exception(f"Downloader упал: {e}")
        _sleep_until_next_hour()
