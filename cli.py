# cli.py
import typer
import yaml
import sys
import os
from loguru import logger
from granite.database import Database
from granite.pipeline.manager import PipelineManager, PipelineCriticalError
from granite.exporters.csv import CsvExporter
from granite.exporters.markdown import MarkdownExporter
from granite.config_validator import validate_config as _validate_config
from granite.pipeline.status import print_status

app = typer.Typer(help="Granite Workshops DB - Сбор и обогащение базы ритуальных мастерских")

# Global state for --config
_config_path: str = "config.yaml"

def config_callback(value: str):
    global _config_path
    _config_path = value

@app.callback()
def main(config: str = typer.Option("config.yaml", "--config", "-c", help="Путь к config.yaml", callback=config_callback)):
    """Granite Workshops DB — pipeline для сбора и обогащения базы."""
    pass

def setup_logging(config: dict):
    """Настройка логирования из config.yaml."""
    logger.remove()  # убираем дефолтный handler
    log_cfg = config.get("logging", {})
    level = log_cfg.get("level", "INFO")
    fmt = log_cfg.get("format", "{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}")
    rotation = log_cfg.get("rotation", "10 MB")
    retention = log_cfg.get("retention", "30 days")

    # Консоль
    logger.add(sys.stderr, level=level, format=fmt, colorize=True)
    # Файл
    os.makedirs("data/logs", exist_ok=True)
    logger.add("data/logs/granite.log", level=level, format=fmt,
               rotation=rotation, retention=retention, encoding="utf-8")

def load_config(config_path: str | None = None):
    path = config_path or _config_path
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print_status(f"Ошибка: файл конфигурации не найден: {path}", "error")
        raise typer.Exit(1)
    except yaml.YAMLError as e:
        print_status(f"Ошибка: некорректный YAML в файле конфигурации: {e}", "error")
        raise typer.Exit(1)

    if not _validate_config(config):
        print_status("Ошибка: конфигурация не прошла валидацию (см. выше)", "error")
        raise typer.Exit(1)
    return config


@app.command()
def run(
    city: str = typer.Argument(..., help="Название города, например 'Астрахань' или 'all' для всех"),
    force: bool = typer.Option(False, "--force", "-f", help="Очистить старые данные и начать заново"),
    no_scrape: bool = typer.Option(False, "--no-scrape", help="Пропустить фазу парсинга (использовать кэш)"),
    re_enrich: bool = typer.Option(False, "--re-enrich", help="Перезапустить только обогащение (сохранить scrape+dedup)")
):
    """Запуск полного цикла сбора, дедупликации и обогащения для города."""
    config = load_config()
    setup_logging(config)
    db = Database(config_path=_config_path)
    
    manager = PipelineManager(config, db)
    
    target_cities = []
    if city.lower() == "all":
        target_cities = [c["name"] for c in config.get("cities", []) if "name" in c]
    else:
        target_cities = [city]
        
    for c in target_cities:
        try:
            manager.run_city(c, force=force, run_scrapers=not no_scrape, re_enrich=re_enrich)
        except PipelineCriticalError:
            print_status(f"Критическая ошибка для города {c}. Остановка.", "error")
            raise typer.Exit(1)
    db.engine.dispose()

@app.command()
def export(
    city: str = typer.Argument(..., help="Название города или 'all'"),
    fmt: str = typer.Option("csv", "--format", "-f", help="Формат экспорта: csv или md")
):
    """Экспорт готовых данных из БД."""
    config = load_config()
    setup_logging(config)
    db = Database(config_path=_config_path)
    
    target_cities = []
    if city.lower() == "all":
        target_cities = [c["name"] for c in config.get("cities", []) if "name" in c]
    else:
        target_cities = [city]

    for c in target_cities:
        if fmt == "csv":
            exporter = CsvExporter(db)
        else:
            exporter = MarkdownExporter(db)
        exporter.export_city(c)

    print_status("Экспорт завершен успешно!", "success")
    db.engine.dispose()

@app.command()
def export_preset(
    city: str = typer.Argument(..., help="Название города или 'all'"),
    preset: str = typer.Argument(..., help="Имя пресета из config.yaml (hot_leads, producers_only, ...)"),
):
    """Экспорт данных по пресету из config.yaml."""
    config = load_config()
    setup_logging(config)
    db = Database(config_path=_config_path)

    export_presets = config.get("export_presets", {})
    if not export_presets:
        print_status("В config.yaml нет секции export_presets", "warning")
        raise typer.Exit(1)

    if preset not in export_presets:
        available = ", ".join(export_presets.keys())
        print_status(f"Пресет '{preset}' не найден. Доступные: {available}", "warning")
        raise typer.Exit(1)

    preset_config = export_presets[preset]
    preset_format = preset_config.get("format", "csv")
    description = preset_config.get("description", "")

    print_status(f"Экспорт пресета '{preset}': {description}", "info")

    target_cities = []
    if city.lower() == "all":
        target_cities = [c["name"] for c in config.get("cities", []) if "name" in c]
    else:
        target_cities = [city]

    for c in target_cities:
        if preset_format in ("markdown", "md"):
            exporter = MarkdownExporter(db)
            exporter.export_city_with_preset(c, preset, preset_config)
        else:
            exporter = CsvExporter(db)
            exporter.export_city_with_preset(c, preset, preset_config)

    print_status("Экспорт пресета завершен!", "success")
    db.engine.dispose()

# ===== Команды управления миграциями =====

db_app = typer.Typer(help="Управление схемой базы данных (Alembic миграции)")
app.add_typer(db_app, name="db")


def _get_alembic_config():
    """Подготовить конфигурацию Alembic для CLI-команд."""
    from alembic.config import Config
    config = load_config()
    db_path = config.get("database", {}).get("path", "data/granite.db")

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    # Передаём путь к config.yaml для env.py
    os.environ["GRANITE_CONFIG"] = _config_path

    return alembic_cfg


@db_app.command("upgrade")
def db_upgrade(
    revision: str = typer.Argument("head", help="Целевая ревизия (head, base, или ID)")
):
    """Применить миграции до указанной ревизии."""
    try:
        alembic_cfg = _get_alembic_config()
        from alembic import command
        command.upgrade(alembic_cfg, revision)
        print_status(f"Миграция применена: {revision}", "success")
    except Exception as e:
        print_status(f"Ошибка миграции: {e}", "error")
        raise typer.Exit(1)


@db_app.command("downgrade")
def db_downgrade(
    revision: str = typer.Argument("-1", help="Целевая ревизия (-1 = на одну назад, base = удалить всё)")
):
    """Откатить миграции до указанной ревизии."""
    try:
        alembic_cfg = _get_alembic_config()
        from alembic import command

        # Подтверждение для отката более чем на одну версию или до base
        try:
            rev_num = int(revision)
        except ValueError:
            print_status(f"Ошибка: неверный формат revision: {revision}", "error")
            raise typer.Exit(1)

        if revision in ("base", "0") or (revision.startswith("-") and rev_num < -1):
            confirm = typer.confirm(f"Вы уверены, что хотите откатить до {revision}? Это может удалить данные.")
            if not confirm:
                raise typer.Exit(0)

        command.downgrade(alembic_cfg, revision)
        print_status(f"Откат выполнен: {revision}", "success")
    except typer.Exit:
        raise
    except Exception as e:
        print_status(f"Ошибка отката: {e}", "error")
        raise typer.Exit(1)


@db_app.command("history")
def db_history(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Показать детали каждой миграции"),
    rev_range: str = typer.Option(None, "--range", "-r", help="Диапазон (например: base..head, rev1..rev2)")
):
    """Показать историю миграций."""
    try:
        alembic_cfg = _get_alembic_config()
        from alembic import command
        command.history(alembic_cfg, verbose=verbose, rev_range=rev_range)
    except Exception as e:
        print_status(f"Ошибка: {e}", "error")
        raise typer.Exit(1)


@db_app.command("current")
def db_current():
    """Показать текущую версию схемы БД."""
    try:
        alembic_cfg = _get_alembic_config()
        from alembic import command
        command.current(alembic_cfg, verbose=True)
    except Exception as e:
        print_status(f"Ошибка: {e}", "error")
        raise typer.Exit(1)


@db_app.command("migrate")
def db_migrate(
    message: str = typer.Argument(..., help="Описание миграции (например: 'add phone column')")
):
    """Создать новую миграцию на основе изменений в ORM-моделях (autogenerate)."""
    try:
        alembic_cfg = _get_alembic_config()
        from alembic import command
        command.revision(alembic_cfg, message=message, autogenerate=True)
        print_status("Новая миграция создана в alembic/versions/", "success")
        print_status("Проверьте и при необходимости отредактируйте файл перед применением.", "info")
    except Exception as e:
        print_status(f"Ошибка создания миграции: {e}", "error")
        raise typer.Exit(1)


@db_app.command("stamp")
def db_stamp(
    revision: str = typer.Argument("head", help="Ревизия для маркировки (head, base, или ID)")
):
    """Пометить текущую версию БД без выполнения миграций (для существующих БД)."""
    try:
        alembic_cfg = _get_alembic_config()
        from alembic import command

        confirm = typer.confirm(f"Пометить БД как '{revision}' без выполнения миграций?")
        if not confirm:
            raise typer.Exit(0)

        command.stamp(alembic_cfg, revision)
        print_status(f"БД помечена как ревизия: {revision}", "success")
    except typer.Exit:
        raise
    except Exception as e:
        print_status(f"Ошибка: {e}", "error")
        raise typer.Exit(1)


@db_app.command("check")
def db_check():
    """Проверить, есть ли незаписанные изменения в ORM-моделях."""
    try:
        alembic_cfg = _get_alembic_config()
        from alembic import command
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(alembic_cfg)

        # Сравниваем текущую схему БД с ORM-моделями
        from granite.database import Base
        import granite.database  # noqa: F401

        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from sqlalchemy import create_engine

        config = load_config()
        db_path = config.get("database", {}).get("path", "data/granite.db")
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

        try:
            with engine.connect() as conn:
                migration_context = MigrationContext.configure(conn)
                diff = compare_metadata(migration_context, Base.metadata)

            if not diff:
                print_status("Схема БД совпадает с ORM-моделями — миграции не нужны.", "success")
            else:
                print_status(f"Обнаружено {len(diff)} различий между ORM и БД:", "warning")
                for item in diff:
                    print(f"  • {item}")
                print_status("Запустите 'python cli.py db migrate \"описание\"' для создания миграции.", "info")
        finally:
            engine.dispose()

    except Exception as e:
        print_status(f"Ошибка проверки: {e}", "error")
        raise typer.Exit(1)


@app.command()
def api(
    port: int = typer.Option(8000, "--port", "-p", help="Порт API сервера"),
    reload: bool = typer.Option(False, "--reload", help="Hot reload для разработки"),
):
    """Запустить CRM API сервер (FastAPI + uvicorn)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    import uvicorn
    uvicorn.run(
        "granite.api.app:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
