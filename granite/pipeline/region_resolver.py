# pipeline/region_resolver.py
"""Разрешение регионов и проверка конфигурации источников.

Вынесено из PipelineManager: чисто конфигурационная логика,
не зависящая от БД или пайплайна.
"""
from granite.regions import get_region_cities

__all__ = ["STANDARD_SOURCES", "RegionResolver"]

STANDARD_SOURCES = ["jsprav", "web_search", "dgis", "yell"]


class RegionResolver:
    """Работа с конфигурацией городов и областей."""

    def __init__(self, config: dict):
        self.config = config

    def get_region_cities(self, city: str) -> list[str]:
        """Найти все города для этой области.

        Берёт область из config.yaml по названию города,
        затем подтягивает полный список городов из data/regions.yaml.

        Пример: city="Ростов-на-Дону" → region="Ростовская область" →
        ["Азов", "Аксай", "Батайск", ..., "Ростов-на-Дону", ..., "Шахты"]
        """
        target_region = None
        for c in self.config.get("cities", []):
            if c.get("name") == city:
                target_region = c.get("region", "")
                break
        if not target_region:
            return [city]

        # Полный список городов из статичного файла
        region_file_cities = get_region_cities(target_region)
        if region_file_cities:
            return region_file_cities

        # Фоллбэк: города из config.yaml с той же областью
        siblings = []
        for c in self.config.get("cities", []):
            if c.get("region") == target_region:
                c_name = c.get("name")
                if c_name:
                    siblings.append(c_name)
        return siblings if siblings else [city]

    def is_source_enabled(self, source: str) -> bool:
        """Проверить включён ли источник в config.yaml."""
        return self.config.get("sources", {}).get(source, {}).get("enabled", True)

    def get_active_sources(self, sources: list[str] | None = None) -> list[str]:
        """Вернуть список включённых источников.

        Args:
            sources: список источников для проверки (по умолчанию все стандартные).
        """
        if sources is None:
            sources = STANDARD_SOURCES
        return [s for s in sources if self.is_source_enabled(s)]
