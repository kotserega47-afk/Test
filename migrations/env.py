import os
from dotenv import load_dotenv
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Загружаем переменные окружения
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

# Alembic config object
config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Логирование
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Подключаем metadata
from db.database import Base
from db import models   # чтобы Alembic видел таблицы
target_metadata = Base.metadata
