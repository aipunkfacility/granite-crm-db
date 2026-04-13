# scrapers/base.py
import traceback
from abc import ABC, abstractmethod
from granite.models import RawCompany
from loguru import logger


class BaseScraper(ABC):
    """Общий интерфейс для всех скреперов."""

    def __init__(self, config: dict, city: str):
        self.config = config
        self.city = city
        self.city_config = self._get_city_config()
        self.last_error: str | None = None

    def _get_city_config(self) -> dict:
        """Получить конфиг города из config.yaml."""
        for c in self.config.get("cities", []):
            if c.get("name") == self.city:
                return c
        logger.warning(f"City '{self.city}' not found in config, returning empty defaults")
        return {}

    @abstractmethod
    def scrape(self) -> list[RawCompany]:
        """Основной метод. Возвращает список сырых компаний."""
        ...

    def run(self) -> list[RawCompany]:
        """Запуск с логированием и обработкой ошибок.

        Returns:
            Список компаний.
        After call, check self.last_error for error details.
        """
        logger.info(f"[{self.__class__.__name__}] Запуск для города: {self.city}")
        self.last_error: str | None = None
        try:
            results = self.scrape()
            logger.info(f"[{self.__class__.__name__}] Найдено: {len(results)} компаний")
            return results
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Ошибка: {e}")
            logger.debug(traceback.format_exc())
            self.last_error = str(e)
            return []
