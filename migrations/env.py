# migrations/env.py (фрагмент)
from logging.config import fileConfig
import os
from db.database import Base
from sqlalchemy import engine_from_config, pool
from alembic import context

# Этот объект содержит настройки Alembic (читаются из alembic.ini)
config = context.config

# Настраиваем URL подключения из переменной окружения
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Если у тебя есть Base = declarative_base(), сюда нужно подтянуть metadata
# from myapp.models import Base
target_metadata = Base.metadata

# Конфиг логов
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline():
    """Запуск миграций в offline-режиме (генерация SQL без подключения)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Запуск миграций в online-режиме (с подключением к базе)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
