# integrations/dropbox_watcher.py
import os
import dropbox
from utils.logger import logger
import shutil

# Получаем токены и ключи из env
ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")
REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")
APP_KEY = os.getenv("DROPBOX_APP_KEY")
APP_SECRET = os.getenv("DROPBOX_APP_SECRET")

# Создаём объект dropbox
if ACCESS_TOKEN:
    dbx = dropbox.Dropbox(ACCESS_TOKEN)
elif REFRESH_TOKEN and APP_KEY and APP_SECRET:
    dbx = dropbox.Dropbox(
        oauth2_refresh_token=REFRESH_TOKEN,
        app_key=APP_KEY,
        app_secret=APP_SECRET
    )
else:
    raise ValueError(
        "Нужно указать DROPBOX_ACCESS_TOKEN или комбинацию DROPBOX_REFRESH_TOKEN + APP_KEY + APP_SECRET"
    )

# -----------------------------
# Функции для работы с файлами
# -----------------------------
def list_files(path: str):
    return os.listdir(path)

def download_file(dropbox_path, local_path):
    shutil.copy(dropbox_path, local_path)
    return True

def upload_file(local_path, dropbox_path):
    shutil.copy(local_path, dropbox_path)
    return True

def move_file(src_path, dest_path):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.move(src_path, dest_path)
    return True
