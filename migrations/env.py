import os
from dotenv import load_dotenv
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# ------------------------------
# Загружаем переменные окружения
# ------------------------------
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задана!")

# Alembic config
config = context.config

# Принудительно заменяем драйвер на psycopg2 (если вдруг указали без него)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

config.set_main_option("sqlalchemy.url", DATABASE_URL)

# ------------------------------
# Логирование
# ------------------------------
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ------------------------------
# Импорт моделей
# ------------------------------
from db.database import Base
from db import models  # noqa: F401 — нужно, чтобы Alembic увидел таблицы
target_metadata = Base.metadata


# ------------------------------
# Offline migrations
# ------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ------------------------------
# Online migrations
# ------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


# ------------------------------
# Точка входа
# ------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
