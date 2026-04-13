# alembic/env.py — среда миграций
# Динамически читает URL БД из config.yaml или переменной DATABASE_URL
import os
import sys
from logging.config import fileConfig

from sqlalchemy import create_engine, event, pool
from alembic import context
from loguru import logger

# Добавляем корень проекта в sys.path, чтобы импортировать database.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from granite.database import Base  # noqa: E402
# Импортируем все модели, чтобы Alembic их увидел (autogenerate сканирует Base.registry)
import granite.database  # noqa: E402, F401

# Конфигурация Alembic из alembic.ini
config = context.config

# Настройка логирования из alembic.ini (если есть секция [loggers])
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Метаданные ORM — источник правды для autogenerate
target_metadata = Base.metadata

_VALID_SCHEMES = ("sqlite", "postgresql", "mysql", "oracle", "mssql")


def get_database_url() -> str:
    """
    Определить URL базы данных.
    Приоритет:
      1. sqlalchemy.url из alembic config (set_main_option / alembic.ini)
      2. Переменная окружения DATABASE_URL (должна содержать валидный SQLAlchemy URL)
      3. Путь из config.yaml (для локальной разработки)
      4. Фоллбэк: sqlite:///data/granite.db
    """
    # 1. sqlalchemy.url из Alembic config (если задан программно — e.g. в тестах / CLI)
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        if any(configured_url.startswith(s) for s in _VALID_SCHEMES):
            return configured_url
        logger.warning(f"Configured database URL has unrecognized scheme: {configured_url}")

    # 2. Переменная окружения (для CI/Docker)
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        if any(env_url.startswith(s) for s in _VALID_SCHEMES):
            return env_url
        logger.warning(f"Environment DATABASE_URL has unrecognized scheme: {env_url}")

    # 3. Из config.yaml
    config_path = os.environ.get("GRANITE_CONFIG", "config.yaml")
    if os.path.exists(config_path):
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        db_path = cfg.get("database", {}).get("path", "data/granite.db")
        return f"sqlite:///{db_path}"

    # 4. Фоллбэк
    return "sqlite:///data/granite.db"


def run_migrations_offline() -> None:
    """Запуск миграций в 'offline' режиме (генерация SQL без подключения к БД)."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Запуск миграций в 'online' режиме (с подключением к БД)."""
    db_url = get_database_url()

    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    engine = create_engine(
        db_url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    # Устанавливаем PRAGMA через event — не ломает транзакции Alembic
    # NOTE: This mirrors granite.database._set_sqlite_pragma. Keep in sync.
    if db_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
