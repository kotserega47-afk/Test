import os
from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context
import psycopg2
from dotenv import load_dotenv, find_dotenv

# ===========================================
# 1. Загрузка .env по точному пути
# ===========================================
env_path = find_dotenv(usecwd=True)
load_dotenv(dotenv_path=env_path)
print(f"✅ Загружен .env: {env_path}")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не задана в .env!")

# Alembic иногда требует psycopg2-драйвер
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# ===========================================
# 2. Настройки Alembic
# ===========================================
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from db.database import Base  # noqa
from db import models  # noqa
target_metadata = Base.metadata

# ===========================================
# 3. Offline миграции
# ===========================================
def run_migrations_offline():
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

# ===========================================
# 4. Online миграции
# ===========================================
def run_migrations_online():
    """Чистое подключение к Railway без перекодировок"""
    dsn = DATABASE_URL  # ✅ используем уже исправленный URL

    # psycopg2 не понимает '+psycopg2'
    if dsn.startswith("postgresql+psycopg2://"):
        dsn = dsn.replace("postgresql+psycopg2://", "postgresql://", 1)

    def connect():
        return psycopg2.connect(dsn)

    connectable = create_engine(
        DATABASE_URL,  # ✅ теперь Alembic использует правильный URL
        creator=connect,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

# ===========================================
# 5. Точка входа
# ===========================================
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
