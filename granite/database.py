# database.py
from contextlib import contextmanager
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    Text,
    JSON,
    ForeignKey,
    event,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from datetime import datetime, timezone
import os
import yaml

Base = declarative_base()


class RawCompanyRow(Base):
    __tablename__ = "raw_companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False, index=True)
    source_url = Column(String, default="")
    name = Column(String, nullable=False)
    phones = Column(JSON, default=list)  # list[str]
    address_raw = Column(Text, default="")
    website = Column(String, nullable=True)
    emails = Column(JSON, default=list)  # list[str]
    geo = Column(String, nullable=True)  # "lat,lon"
    messengers = Column(JSON, default=dict)  # {"telegram": "...", "vk": "...", ...}
    scraped_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc))
    city = Column(String, nullable=False, index=True)
    merged_into = Column(Integer, ForeignKey("companies.id"), nullable=True)
    # DEPRECATED: merged_into — не используется в текущем пайплайне, оставлен для совместимости схемы


class CompanyRow(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    merged_from = Column(JSON, default=list)  # list[int]
    name_best = Column(String, nullable=False)
    phones = Column(JSON, default=list)
    address = Column(Text, default="")
    website = Column(String, nullable=True)
    emails = Column(JSON, default=list)
    city = Column(String, nullable=False, index=True)
    messengers = Column(JSON, default=dict)  # {"telegram": "...", "vk": "...", ...}
    status = Column(String, default="raw", index=True)
    segment = Column(String, default="Не определено")
    needs_review = Column(Boolean, default=False)
    review_reason = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc))


class EnrichedCompanyRow(Base):
    __tablename__ = "enriched_companies"

    id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    name = Column(String, nullable=False)
    phones = Column(JSON, default=list)
    address_raw = Column(Text, default="")
    website = Column(String, nullable=True)
    emails = Column(JSON, default=list)
    city = Column(String, nullable=False, index=True)

    # Обогащенные данные
    messengers = Column(JSON, default=dict)  # {"telegram": "...", "whatsapp": "..."}
    tg_trust = Column(JSON, default=dict)  # {"trust_score": 3, "has_avatar": True, ...}
    cms = Column(String, default="unknown")
    has_marquiz = Column(Boolean, default=False)
    is_network = Column(Boolean, default=False)

    # Результаты анализа
    crm_score = Column(Integer, default=0, index=True)
    segment = Column(String, default="D", index=True)

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(tz=timezone.utc),
        onupdate=lambda: datetime.now(tz=timezone.utc),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "phones": self.phones or [],
            "address_raw": self.address_raw,
            "website": self.website,
            "emails": self.emails or [],
            "city": self.city,
            "messengers": self.messengers or {},
            "tg_trust": self.tg_trust or {},
            "cms": self.cms,
            "has_marquiz": self.has_marquiz,
            "is_network": self.is_network,
            "crm_score": self.crm_score,
            "segment": self.segment,
        }


class PipelineRunRow(Base):
    # DEPRECATED: PipelineRunRow — не используется в текущем пайплайне, оставлен для совместимости схемы
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    city = Column(String, nullable=False, index=True)
    stage = Column(String, nullable=False)
    source = Column(String, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc))
    finished_at = Column(DateTime, nullable=True)
    records_found = Column(Integer, default=0)
    records_errors = Column(Integer, default=0)
    status = Column(String, default="running")
    error_message = Column(Text, default="")


# ===== Синглтон для доступа к БД =====


def run_alembic_upgrade(db_path: str, config_path: str = "config.yaml"):
    """
    Запустить Alembic upgrade head для применения миграций.
    Вызывается при инициализации Database, чтобы схема всегда была актуальной.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", "alembic")
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        # Устанавливаем GRANITE_CONFIG для env.py
        old_granite_config = os.environ.get("GRANITE_CONFIG")
        os.environ["GRANITE_CONFIG"] = config_path

        from alembic import command

        command.upgrade(alembic_cfg, "head")

        # Восстанавливаем старое значение
        if old_granite_config is None:
            os.environ.pop("GRANITE_CONFIG", None)
        else:
            os.environ["GRANITE_CONFIG"] = old_granite_config

    except Exception as e:
        # Если Alembic не настроен или миграций нет — фоллбэк на create_all
        import warnings

        warnings.warn(
            f"Alembic upgrade не удалось ({e}), используется create_all(). "
            "Для корректной эволюции схемы настройте Alembic: alembic init alembic",
            stacklevel=2,
        )
        raise


class Database:
    def __init__(
        self,
        db_path: str | None = None,
        config_path: str = "config.yaml",
        auto_migrate: bool = True,
    ):
        if not db_path:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            db_path = config.get("database", {}).get("path", "data/granite.db")

        self._db_path = db_path
        self._config_path = config_path
        os.makedirs(
            os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True
        )

        # WAL-режим: параллельные записи из ThreadPoolExecutor без "database is locked"
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )

        # Включаем WAL, foreign_keys и busy_timeout на уровне подключения
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")  # 5 сек ожидания блокировки
            cursor.close()

        # Применяем миграции через Alembic (если доступен)
        if auto_migrate:
            try:
                run_alembic_upgrade(db_path, config_path)
            except Exception as e:
                logger.warning(
                    f"Миграции не применились, используем fallback create_all: {e}"
                )
                # Фоллбэк: создать таблицы напрямую из ORM-моделей
                Base.metadata.create_all(self.engine)
        else:
            # Без авто-миграций — просто создаём таблицы из ORM
            Base.metadata.create_all(self.engine)

        self.SessionLocal = sessionmaker(bind=self.engine)

    def get_session(self) -> Session:
        return self.SessionLocal()

    @contextmanager
    def session_scope(self):
        """Контекстный менеджер для безопасной работы с сессией БД.

        Автоматически делает commit при успешном выходе,
        rollback при исключении и close в любом случае.

        Usage:
            with db.session_scope() as session:
                companies = session.query(CompanyRow).filter_by(city=city).all()
                ...
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
