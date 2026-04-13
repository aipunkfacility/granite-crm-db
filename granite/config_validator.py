# granite/config_validator.py
"""Валидация конфигурации config.yaml.

Вынесен из cli.py, чтобы избежать циклического импорта database.py ↔ cli.py.
Database.__init__() использует _validate_config(), но не должен зависеть от cli.py.
"""

from granite.pipeline.status import print_status


def validate_config(config: dict) -> bool:
    """Проверяет критические поля конфигурации при загрузке.

    Валидирует структуру и типы ключевых секций, чтобы ошибки проявились
    немедленно при запуске, а не через 30 минут работы пайплайна.
    """
    if not isinstance(config, dict):
        print_status("Конфиг должен быть словарём (mapping) на верхнем уровне", "error")
        return False

    errors = []

    # cities — обязательная секция
    cities = config.get("cities")
    if cities is None:
        errors.append("Отсутствует секция 'cities'")
    elif not isinstance(cities, list):
        errors.append("'cities' должен быть списком")
    elif len(cities) == 0:
        errors.append("'cities' пуст — нет городов для обработки")
    else:
        for i, c in enumerate(cities):
            if not isinstance(c, dict):
                errors.append(f"cities[{i}] = {c!r} — ожидается словарь с полями name, region")
                continue
            if "name" not in c or not c["name"]:
                errors.append(f"cities[{i}] — отсутствует или пустое поле 'name'")

    # scoring.weights — если есть, все значения должны быть числами
    weights = config.get("scoring", {}).get("weights", {})
    if isinstance(weights, dict):
        for key, val in weights.items():
            if not isinstance(val, (int, float)):
                errors.append(f"scoring.weights.{key} = {val!r} — ожидается число")

    # scoring.levels — если есть, пороги должны быть числами
    levels = config.get("scoring", {}).get("levels", {})
    if isinstance(levels, dict):
        for key, val in levels.items():
            if not isinstance(val, (int, float)):
                errors.append(f"scoring.levels.{key} = {val!r} — ожидается число")

    # database.path — если есть, должна быть строка
    db_cfg = config.get("database", {})
    if isinstance(db_cfg, dict):
        db_path = db_cfg.get("path")
        if db_path is not None and not isinstance(db_path, str):
            errors.append(f"database.path = {db_path!r} — ожидается строка")

    # scraping.max_threads — если есть, должно быть целым числом > 0
    scrape_cfg = config.get("scraping", {})
    if isinstance(scrape_cfg, dict):
        max_threads = scrape_cfg.get("max_threads")
        if max_threads is not None:
            if not isinstance(max_threads, int) or max_threads < 1:
                errors.append(f"scraping.max_threads = {max_threads!r} — ожидается целое число > 0")

    for err in errors:
        print_status(f"  Config validation: {err}", "error")

    if errors:
        print_status(f"Найдено {len(errors)} ошибок в конфигурации", "error")
        return False
    return True
