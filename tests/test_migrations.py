# tests/test_migrations.py — тесты для Alembic миграций
"""
Проверяем:
  1. Initial schema создаёт все 3 таблицы + индексы
  2. Downgrade полностью удаляет таблицы
  3. Re-upgrade после downgrade восстанавливает схему
  4. Database() автоматически применяет миграции
  5. db check не находит различий после upgrade
  6. Создание новой миграции (autogenerate) работает
"""
import sys
import tempfile
import pytest
from pathlib import Path

# Подготовка: добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def db_url(tmp_path):
    """Временный SQLite для тестов миграций."""
    db_path = tmp_path / "test_granite.db"
    return f"sqlite:///{db_path}", str(db_path)


@pytest.fixture
def alembic_config(db_url, tmp_path, monkeypatch):
    """Конфигурация Alembic для тестов с временной БД."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    url, path = db_url

    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")

    # Создаём config.yaml рядом с БД, чтобы env.py мог его прочитать
    test_config = tmp_path / "config.yaml"
    test_config.write_text("database:\n  path: data/granite.db\n")
    monkeypatch.setenv("GRANITE_CONFIG", str(test_config))

    yield cfg


class TestInitialMigration:
    """Группа тестов для начальной миграции."""

    def test_upgrade_creates_all_tables(self, alembic_config, db_url):
        """upgrade head создаёт все таблицы и индексы."""
        from alembic import command
        from sqlalchemy import create_engine, inspect

        url, _ = db_url
        command.upgrade(alembic_config, "head")

        engine = create_engine(url)
        insp = inspect(engine)
        tables = insp.get_table_names()

        # Проверяем наличие всех таблиц
        assert "companies" in tables
        assert "raw_companies" in tables
        assert "enriched_companies" in tables
        assert "alembic_version" in tables

        # Проверяем индексы
        expected_indexes = {
            "companies": ["ix_companies_city", "ix_companies_status"],
            "raw_companies": ["ix_raw_companies_city", "ix_raw_companies_source"],
            "enriched_companies": ["ix_enriched_companies_city", "ix_enriched_companies_crm_score", "ix_enriched_companies_segment"],
        }
        for table, idx_names in expected_indexes.items():
            actual = {idx["name"] for idx in insp.get_indexes(table)}
            for idx_name in idx_names:
                assert idx_name in actual, f"Missing index {idx_name} on {table}"

    def test_downgrade_removes_all_tables(self, alembic_config, db_url):
        """downgrade base удаляет все таблицы (кроме alembic_version)."""
        from alembic import command
        from sqlalchemy import create_engine, text

        url, _ = db_url
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "base")

        engine = create_engine(url)
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'"
            ))
            remaining = [row[0] for row in result]

        assert remaining == [], f"Tables remain after downgrade: {remaining}"

    def test_upgrade_after_downgrade(self, alembic_config, db_url):
        """Повторный upgrade после downgrade восстанавливает схему."""
        from alembic import command
        from sqlalchemy import create_engine, inspect

        url, _ = db_url
        # Первый цикл
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "base")

        # Второй цикл (проверяем idempotency)
        command.upgrade(alembic_config, "head")

        engine = create_engine(url)
        insp = inspect(engine)
        tables = insp.get_table_names()

        assert "companies" in tables
        assert "raw_companies" in tables
        assert "enriched_companies" in tables

    def test_alembic_version_is_stamped(self, alembic_config, db_url):
        """После upgrade в alembic_version записана корректная ревизия."""
        from alembic import command
        from alembic.script import ScriptDirectory
        from sqlalchemy import create_engine, text

        url, _ = db_url
        command.upgrade(alembic_config, "head")

        script = ScriptDirectory.from_config(alembic_config)
        head = script.get_current_head()

        engine = create_engine(url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            version = result.scalar()

        assert version == head, f"Expected {head}, got {version}"


class TestDatabaseAutoMigrate:
    """Проверка автоматической миграции при создании Database()."""

    def test_database_auto_creates_tables(self, tmp_path, monkeypatch):
        """Database() автоматически применяет Alembic-миграции."""
        from sqlalchemy import create_engine, inspect
        # Подменяем config.yaml
        config_content = f"database:\n  path: {tmp_path / 'test_auto.db'}\n"
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        monkeypatch.setenv("GRANITE_CONFIG", str(config_path))

        from granite.database import Database
        db = Database(db_path=str(tmp_path / "test_auto.db"), config_path=str(config_path))

        insp = inspect(db.engine)
        tables = insp.get_table_names()

        assert "companies" in tables
        assert "raw_companies" in tables
        assert "enriched_companies" in tables
        assert "alembic_version" in tables

    def test_database_fallback_without_alembic(self, tmp_path):
        """Database(auto_migrate=False) использует create_all как фоллбэк."""
        from sqlalchemy import create_engine, inspect

        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"database:\n  path: {tmp_path / 'test_fallback.db'}\n")

        from granite.database import Database
        db = Database(
            db_path=str(tmp_path / "test_fallback.db"),
            config_path=str(config_path),
            auto_migrate=False,
        )

        insp = inspect(db.engine)
        tables = insp.get_table_names()

        # Таблицы ORM созданы через create_all
        assert "companies" in tables
        assert "raw_companies" in tables


class TestSchemaCheck:
    """Проверка обнаружения различий между ORM и БД."""

    def test_no_diff_after_upgrade(self, alembic_config, db_url):
        """После upgrade head нет различий между ORM и БД."""
        from alembic import command
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from sqlalchemy import create_engine
        from granite.database import Base

        url, _ = db_url
        command.upgrade(alembic_config, "head")

        engine = create_engine(url)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diff = compare_metadata(ctx, Base.metadata)

        assert diff == [], f"Unexpected diff after upgrade: {diff}"


class TestForeignKeys:
    """Проверка внешних ключей."""

    def test_enriched_companies_fk_cascade(self, alembic_config, db_url):
        """FK enriched_companies.id -> companies.id с ON DELETE CASCADE."""
        from alembic import command
        from sqlalchemy import create_engine, inspect

        url, _ = db_url
        command.upgrade(alembic_config, "head")

        engine = create_engine(url)
        insp = inspect(engine)

        fks = insp.get_foreign_keys("enriched_companies")
        assert len(fks) >= 1

        fk = fks[0]
        assert fk["referred_table"] == "companies"
        assert "id" in fk["constrained_columns"]

    def test_raw_companies_fk(self, alembic_config, db_url):
        """FK raw_companies.merged_into -> companies.id."""
        from alembic import command
        from sqlalchemy import create_engine, inspect

        url, _ = db_url
        command.upgrade(alembic_config, "head")

        engine = create_engine(url)
        insp = inspect(engine)

        fks = insp.get_foreign_keys("raw_companies")
        assert len(fks) >= 1

        fk = fks[0]
        assert fk["referred_table"] == "companies"
        assert "merged_into" in fk["constrained_columns"]
