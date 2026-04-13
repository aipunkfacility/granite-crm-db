# database.py
from contextlib import contextmanager
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
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
from loguru import logger

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
    scraped_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    city = Column(String, nullable=False, index=True)
    merged_into = Column(Integer, ForeignKey("companies.id"), nullable=True)

    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.id}, name={self.name!r})>"


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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.id}, name={self.name_best!r})>"



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
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
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
            "updated_at": self.updated_at,
        }

    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.id}, name={self.name!r})>"


# Допустимые стадии воронки — используется для валидации в API и future CHECK constraint
VALID_STAGES = {
    "new", "email_sent", "email_opened", "tg_sent", "wa_sent",
    "replied", "interested", "not_interested", "unreachable",
}


class CrmContactRow(Base):
    """Главная CRM-запись для компании. Создаётся SEED-скриптом для всех companies."""
    __tablename__ = "crm_contacts"

    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )

    funnel_stage = Column(String, default="new", server_default="new", index=True)

    # Email метрики
    email_sent_count = Column(Integer, default=0, server_default="0")
    email_opened_count = Column(Integer, default=0, server_default="0")
    email_replied_count = Column(Integer, default=0, server_default="0")
    last_email_sent_at = Column(DateTime, nullable=True)
    last_email_opened_at = Column(DateTime, nullable=True)

    # Мессенджер метрики
    tg_sent_count = Column(Integer, default=0, server_default="0")
    wa_sent_count = Column(Integer, default=0, server_default="0")
    last_tg_at = Column(DateTime, nullable=True)
    last_wa_at = Column(DateTime, nullable=True)

    # Общая статистика касаний
    contact_count = Column(Integer, default=0, server_default="0")
    last_contact_at = Column(DateTime, nullable=True, index=True)
    last_contact_channel = Column(String, default="", server_default="")
    first_contact_at = Column(DateTime, nullable=True)

    # Ручные поля
    notes = Column(Text, default="")
    stop_automation = Column(Integer, default=0, server_default="0", index=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        return f"<CrmContactRow(company_id={self.company_id}, stage={self.funnel_stage!r})>"


class CrmTouchRow(Base):
    """Лог касаний: каждое отправленное/полученное сообщение."""
    __tablename__ = "crm_touches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    channel = Column(String, nullable=False)    # email / tg / wa / manual
    direction = Column(String, nullable=False)  # outgoing / incoming

    subject = Column(String, default="")
    body = Column(Text, default="")
    note = Column(Text, default="")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<CrmTouchRow(id={self.id}, channel={self.channel!r}, dir={self.direction!r})>"


class CrmTemplateRow(Base):
    """Шаблоны сообщений с плейсхолдерами {from_name}, {city}, {company_name}."""
    __tablename__ = "crm_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    channel = Column(String, nullable=False)
    subject = Column(String, default="")
    body = Column(Text, nullable=False)
    description = Column(String, default="")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def render(self, **kwargs) -> str:
        """Подставить значения в плейсхолдеры шаблона.

        Безопасность: используется str.replace() (литеральная подстановка подстроки),
        НЕ str.format() или eval(). Инъекция невозможна.
        """
        result = self.body
        for key, value in kwargs.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result

    def render_subject(self, **kwargs) -> str:
        """Подставить значения в тему письма."""
        if not self.subject:
            return self.subject
        result = self.subject
        for key, value in kwargs.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result

    def __repr__(self):
        return f"<CrmTemplateRow(name={self.name!r}, channel={self.channel!r})>"


class CrmEmailLogRow(Base):
    """Запись об отправленном письме с UUID для tracking pixel."""
    __tablename__ = "crm_email_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    email_to = Column(String, nullable=False)
    email_subject = Column(String, default="")
    template_name = Column(String, default="")
    campaign_id = Column(Integer, nullable=True, index=True)

    status = Column(String, default="pending")

    sent_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    bounced_at = Column(DateTime, nullable=True)
    error_message = Column(Text, default="")

    tracking_id = Column(String, unique=True, nullable=True, index=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<CrmEmailLogRow(id={self.id}, to={self.email_to!r}, status={self.status!r})>"


class CrmTaskRow(Base):
    """Задачи: follow-up, отправка портфолио, звонок и т.д."""
    __tablename__ = "crm_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True, index=True
    )

    title = Column(String, nullable=False)
    description = Column(Text, default="")
    due_date = Column(DateTime, nullable=True)
    priority = Column(String, default="normal")
    status = Column(String, default="pending", index=True)
    task_type = Column(String, default="follow_up")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<CrmTaskRow(id={self.id}, title={self.title!r}, status={self.status!r})>"


class CrmEmailCampaignRow(Base):
    """Email-кампания: набор получателей + шаблон."""
    __tablename__ = "crm_email_campaigns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    template_name = Column(String, nullable=False)
    status = Column(String, default="draft", index=True)

    filters = Column(Text, default="{}")

    total_sent = Column(Integer, default=0)
    total_opened = Column(Integer, default=0)
    total_replied = Column(Integer, default=0)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<CrmEmailCampaignRow(id={self.id}, name={self.name!r}, status={self.status!r})>"


__all__ = [
    "Base", "Database",
    "RawCompanyRow", "CompanyRow", "EnrichedCompanyRow",
    "CrmContactRow", "CrmTouchRow", "CrmTemplateRow",
    "CrmEmailLogRow", "CrmTaskRow", "CrmEmailCampaignRow",
    "VALID_STAGES",
]


# ===== Синглтон для доступа к БД =====


def _make_alembic_config(db_path: str, config_path: str):
    """Создать Alembic Config с правильными путями."""
    from alembic.config import Config
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", "alembic")
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return alembic_cfg


def _tables_exist(engine) -> bool:
    """Проверить, существуют ли ORM-таблицы в БД."""
    from sqlalchemy import inspect
    inspector = inspect(engine)
    existing = inspector.get_table_names()
    return all(t in existing for t in ("companies", "raw_companies", "enriched_companies"))


def _alembic_needs_upgrade(engine) -> bool:
    """Проверить, нужно ли применять миграции (использует существующий engine)."""
    try:
        from alembic.runtime.migration import MigrationContext
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current = ctx.get_current_revision()
            return current is None
    except Exception:
        return True


def run_alembic_upgrade(engine, db_path: str, config_path: str = "config.yaml"):
    """
    Применить миграции / stamp через существующий engine (без создания
    отдельного подключения к SQLite — чтобы не было «database is locked»).

    Стратегия:
    - Таблицы существуют, alembic_version совпадает с head → ничего не делать.
    - Таблицы существуют, alembic_version пуст → stamp head (raw SQL).
    - Таблиц не существуют → create_all() + stamp head (raw SQL).
    """
    from sqlalchemy import text
    alembic_cfg = _make_alembic_config(db_path, config_path)
    from alembic.script import ScriptDirectory
    HEAD_REVISION = ScriptDirectory.from_config(alembic_cfg).get_current_head()

    try:
        # 1. Проверяем, нужно ли что-то делать
        if _tables_exist(engine) and not _alembic_needs_upgrade(engine):
            logger.debug("Alembic: schema up-to-date")
            return

        # 2. Создаём таблицы, если их нет
        if not _tables_exist(engine):
            Base.metadata.create_all(engine)
            logger.debug("Alembic: таблицы созданы через create_all")

        # 3. Stamp alembic_version напрямую через тот же engine
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                "  version_num VARCHAR(32) NOT NULL, "
                "  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                ")"
            ))
            conn.execute(text("DELETE FROM alembic_version"))
            conn.execute(text(
                "INSERT INTO alembic_version (version_num) VALUES (:rev)"
            ), {"rev": HEAD_REVISION})
            conn.commit()
        logger.debug("Alembic: stamped to head")

    except Exception as e:
        import warnings
        warnings.warn(
            f"Alembic setup не удалось ({e}), используется create_all().",
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
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
            except FileNotFoundError:
                raise FileNotFoundError(f"Config file not found: {config_path}")
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML in config file {config_path}: {e}")

            # Validate config (from separate module to avoid circular import)
            try:
                from granite.config_validator import validate_config
                if not validate_config(config):
                    logger.warning("Config validation failed, proceeding with defaults")
            except ImportError:
                logger.debug("config_validator not available, skipping config validation")

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
                run_alembic_upgrade(self.engine, db_path, config_path)
            except Exception as e:
                logger.warning(
                    f"Миграции не применились, используем fallback create_all: {e}"
                )
                # Фоллбэк: создать таблицы напрямую из ORM-моделей
                logger.warning(
                    "create_all() fallback: CRM tables created WITHOUT FK CASCADE. "
                    "Run 'python cli.py db upgrade head' to recreate with proper constraints."
                )
                Base.metadata.create_all(self.engine)
                # Stamp alembic_version to avoid "table exists" loop on next run
                self._stamp_alembic_head(db_path, config_path)
        else:
            # Без авто-миграций — просто создаём таблицы из ORM
            logger.warning(
                "create_all() fallback: CRM tables created WITHOUT FK CASCADE. "
                "Run 'python cli.py db upgrade head' to recreate with proper constraints."
            )
            Base.metadata.create_all(self.engine)

        self.SessionLocal = sessionmaker(bind=self.engine)

    def get_session(self) -> Session:
        return self.SessionLocal()

    def _stamp_alembic_head(self, db_path: str, config_path: str):
        """Stamp alembic_version через raw SQL (без отдельного Alembic-подключения).

        Called after create_all() fallback so Alembic doesn't try to re-create
        existing tables on the next run.
        """
        from sqlalchemy import text
        alembic_cfg = _make_alembic_config(db_path, config_path)
        from alembic.script import ScriptDirectory
        HEAD_REVISION = ScriptDirectory.from_config(alembic_cfg).get_current_head()
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS alembic_version ("
                    "  version_num VARCHAR(32) NOT NULL, "
                    "  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                    ")"
                ))
                conn.execute(text("DELETE FROM alembic_version"))
                conn.execute(text(
                    "INSERT INTO alembic_version (version_num) VALUES (:rev)"
                ), {"rev": HEAD_REVISION})
                conn.commit()
            logger.info("Alembic version stamped to 'head' (create_all fallback)")
        except Exception as e:
            logger.debug(f"Could not stamp alembic version: {e}")

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
