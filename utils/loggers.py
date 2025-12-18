from utils.log_ctx import CtxLogger
from utils.logger import logger as base_logger

def get_logger(name: str, icon: str):
    # name/icon — строки, префикс единый для всех сообщений этого логгера
    return CtxLogger(base_logger, {"prefix": f"{icon} [{name}]"})
