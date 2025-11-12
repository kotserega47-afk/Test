# download_cron.py
import time
from datetime import datetime, timedelta
from utils.logger import logger
from integrations.downloader import run_download

def _sleep_until_next_hour():
    now = datetime.now()
    nxt = (now.replace(minute=5, second=0, microsecond=0) + timedelta(hours=0))
    delta = (nxt - now).total_seconds()
    logger.info(f"⏰ Сплю {int(delta)} сек до следующего часа ({nxt.strftime('%H:%M')})")
    time.sleep(max(1, int(delta)))
