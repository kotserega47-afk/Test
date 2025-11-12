

downloader: python -u download_cron.py

# Устанавливаем системные пакеты для Chromium
postinstall: |
  apt-get update && apt-get install -y \
  libglib2.0-0 libgobject-2.0-0 libnspr4 libnss3 libnssutil3 libsmime3 libgio-2.0-0 libdbus-1-3 \
  libatk1.0-0 libatk-bridge2.0-0 libcups2 libexpat1 libxcb1 libxkbcommon0 libatspi2.0-0 \
  libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libcairo2 \
  libpango-1.0-0 libasound2 && playwright install chromium
downloader: python -u download_cron.py