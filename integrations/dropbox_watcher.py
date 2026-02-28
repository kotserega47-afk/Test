# integrations/dropbox_watcher.py
import os
import dropbox
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from dropbox.exceptions import ApiError

icon, name = LOG_PROFILES["DROPBOX"]
logger = get_logger(name, icon)

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
    """Список файлов в папке dropbox"""
    try:
        res = dbx.files_list_folder(path)
        return [entry.name for entry in res.entries]
    except Exception as e:
        logger.error(f"Ошибка list_files({path}): {e}")
        return []

def download_file(dropbox_path: str, local_path: str):
    """
    Возвращает:
      "ok"        — файл скачан
      "not_found" — файла нет
      "error"     — иная ошибка
    """
    try:
        metadata, res = dbx.files_download(dropbox_path)
        with open(local_path, "wb") as f:
            f.write(res.content)
        return "ok"

    except ApiError as e:
        # 404
        if isinstance(e.error, dropbox.files.DownloadError) and e.error.is_path():
            if e.error.get_path().is_not_found():
                logger.info(f"File not found in Dropbox: {dropbox_path}")
                return "not_found"

        logger.error(f"Dropbox API error: {e}")
        return "error"

    except Exception as e:
        logger.error(f"Ошибка download_file({dropbox_path}): {e}")
        return "error"

def upload_file(local_path: str, dropbox_path: str):
    """Загрузить локальный файл в dropbox"""
    try:
        with open(local_path, "rb") as f:
            dbx.files_upload(f.read(), dropbox_path, mode=dropbox.files.WriteMode("overwrite"))
        return True
    except Exception as e:
        logger.error(f"Ошибка upload_file({dropbox_path}): {e}")
        return False

def move_file(src_path: str, dest_path: str):
    """Переместить файл внутри dropbox"""
    try:
        dbx.files_move_v2(src_path, dest_path, allow_shared_folder=True, autorename=True)
        return True
    except Exception as e:
        logger.error(f"Ошибка move_file({src_path} → {dest_path}): {e}")
        return False
# --- Rules delivery contract (runtime) ---
RULES_DROPBOX_PATH = os.getenv("DROPBOX_RULES_PATH", "/rules.xlsx")
RULES_LOCAL_PATH = os.getenv("RULES_LOCAL_PATH", "/tmp/rules/rules.xlsx")


def download_rules_xlsx() -> str:
    """
    Скачивает rules.xlsx из Dropbox в канонический локальный путь.
    Возвращает локальный путь.
    Бросает исключение, если скачать не удалось.
    """
    os.makedirs(os.path.dirname(RULES_LOCAL_PATH), exist_ok=True)

    ok = download_file(RULES_DROPBOX_PATH, RULES_LOCAL_PATH)
    if not ok:
        raise RuntimeError(f"Failed to download rules.xlsx from Dropbox: {RULES_DROPBOX_PATH}")

    logger.info(f"✅ rules.xlsx downloaded: {RULES_DROPBOX_PATH} → {RULES_LOCAL_PATH}")
    return RULES_LOCAL_PATH