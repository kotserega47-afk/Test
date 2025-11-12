# Устанавливаем системные библиотеки для Chromium (Debian 12 / Railway)
postinstall: |
  apt-get update && apt-get install -y \
  libglib2.0-0 libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libexpat1 libxcb1 libxkbcommon0 libatspi2.0-0 libx11-6 libxcomposite1 \
  libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libcairo2 libpango-1.0-0 \
  libasound2 libxshmfence1 libxrender1 fonts-liberation libdrm2 libxrandr2 \
  libgtk-3-0 libxss1 libxtst6 lsb-release xdg-utils wget \
  && playwright install chromium

downloader: python -u download_cron.py
