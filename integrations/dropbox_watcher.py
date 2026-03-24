# integrations/dropbox_watcher.py
import os
import dropbox
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from dropbox.exceptions import ApiError

icon, name = LOG_PROFILES["DROPBOX"]
logger = get_logger(name, icon)

def _get_dbx():
    access_token = os.getenv("DROPBOX_ACCESS_TOKEN")
    refresh_token = os.getenv("DROPBOX_REFRESH_TOKEN")
    app_key = os.getenv("DROPBOX_APP_KEY")
    app_secret = os.getenv("DROPBOX_APP_SECRET")

    if access_token:
        return dropbox.Dropbox(access_token)

    if refresh_token and app_key and app_secret:
        return dropbox.Dropbox(
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
            app_secret=app_secret,
        )

    raise ValueError(
        "Нужно указать DROPBOX_ACCESS_TOKEN или комбинацию "
        "DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY + DROPBOX_APP_SECRET"
    )

# -----------------------------
# Функции для работы с файлами
# -----------------------------
def list_files(path: str):
    """Список файлов в папке dropbox"""
    try:
        dbx = _get_dbx()
        res = dbx.files_list_folder(path)
        return [entry.name for entry in res.entries]
    except Exception as e:
        logger.error(f"Ошибка list_files({path}): {e}")
        return []

def download_file_status(dropbox_path: str, local_path: str) -> str:
    """
    Возвращает статус скачивания:
      "ok"        — файл скачан
      "not_found" — файла нет
      "error"     — иная ошибка
    """
    try:
        dbx = _get_dbx()
        metadata, res = dbx.files_download(dropbox_path)
        with open(local_path, "wb") as f:
            f.write(res.content)
        return "ok"

    except ApiError as e:
        if isinstance(e.error, dropbox.files.DownloadError) and e.error.is_path():
            if e.error.get_path().is_not_found():
                logger.info(f"File not found in Dropbox: {dropbox_path}")
                return "not_found"

        logger.error(f"Dropbox API error: {e}")
        return "error"

    except Exception as e:
        logger.error(f"Ошибка download_file({dropbox_path}): {e}")
        return "error"


def download_file(dropbox_path: str, local_path: str) -> bool:
    """
    Булев контракт для runtime-кода.
    True — файл успешно скачан.
    False — файл не скачан по любой причине.

    Для различения причин используйте download_file_status().
    """
    return download_file_status(dropbox_path, local_path) == "ok"

def upload_file(local_path: str, dropbox_path: str):
    """Загрузить локальный файл в dropbox"""
    try:
        dbx = _get_dbx()
        with open(local_path, "rb") as f:
            dbx.files_upload(f.read(), dropbox_path, mode=dropbox.files.WriteMode("overwrite"))
        return True
    except Exception as e:
        logger.error(f"Ошибка upload_file({dropbox_path}): {e}")
        return False

def move_file(src_path: str, dest_path: str):
    """Переместить файл внутри dropbox"""
    try:
        dbx = _get_dbx()
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

    status = download_file_status(RULES_DROPBOX_PATH, RULES_LOCAL_PATH)
    if status != "ok":
        raise RuntimeError(f"Failed to download rules.xlsx from Dropbox: {RULES_DROPBOX_PATH}")

    logger.info(f"✅ rules.xlsx downloaded: {RULES_DROPBOX_PATH} → {RULES_LOCAL_PATH}")
    return RULES_LOCAL_PATH