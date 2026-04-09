# pipeline/scraping_phase.py
"""Фаза 0+1: поиск категорий и сбор данных из скраперов.

Вынесено из PipelineManager — скрапинг — полностью самостоятельная фаза
с собственной логикой параллелизации и сохранения.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from granite.database import Database, RawCompanyRow
from granite.pipeline.status import print_status
from granite.pipeline.region_resolver import STANDARD_SOURCES
from granite.category_finder import discover_categories, get_categories, get_subdomain

# Import Scrapers
from granite.scrapers._playwright import playwright_session
from granite.scrapers.jsprav import JspravScraper
from granite.scrapers.dgis import DgisScraper
from granite.scrapers.yell import YellScraper
from granite.scrapers.firmsru import FirmsruScraper
from granite.scrapers.web_search import WebSearchScraper

__all__ = ["ScrapingPhase"]


class ScrapingPhase:
    """Координация скраперов: категорийный поиск + сбор данных."""

    def __init__(self, config: dict, db: Database, region_resolver):
        """
        Args:
            config: словарь конфигурации (config.yaml).
            db: экземпляр Database.
            region_resolver: RegionResolver для проверки источников.
        """
        self.config = config
        self.db = db
        self.region_resolver = region_resolver

    def run(self, city: str, region_cities: list[str] | None = None) -> int:
        """Запустить фазу 0+1 для города.

        Args:
            city: название города (под него сохраняются все данные области).
            region_cities: список городов области (если None — только city).

        Returns:
            Количество собранных записей.
        """
        if not region_cities:
            region_cities = [city]

        # Показываем какие источники включены
        active = self.region_resolver.get_active_sources(STANDARD_SOURCES)
        print_status(f"Источники: {', '.join(active)}", "info")

        # ФАЗА 0: Поиск рабочих категорий в справочниках
        if self.region_resolver.is_source_enabled("jsprav"):
            print_status("Поиск категорий в справочниках...", "info")
            cat_cache = discover_categories(region_cities, self.config)
        else:
            cat_cache = {}

        # ФАЗА 1: Сбор данных
        max_threads = self.config.get("scraping", {}).get("max_threads", 1)
        print_status(f"ФАЗА 1: Сбор данных (Scraping, threads={max_threads})", "info")

        raw_results = self._collect_results(city, region_cities, cat_cache, max_threads)

        # Сохранение сырых данных в БД
        self._save_raw(raw_results)
        return len(raw_results)

    def _collect_results(
        self, city: str, region_cities: list[str], cat_cache: dict, max_threads: int
    ) -> list:
        """Собрать результаты скрапинга (параллельно или последовательно)."""
        raw_results = []

        if max_threads > 1 and len(region_cities) > 1:
            # Параллельный парсинг городов (каждый поток создаёт свою сессию Playwright)
            print_status(
                f"Параллельный парсинг {len(region_cities)} городов на {max_threads} потоках",
                "info",
            )
            with ThreadPoolExecutor(max_workers=max_threads) as executor:
                future_to_city = {
                    executor.submit(self._scrape_single_city, rc, city, cat_cache): rc
                    for rc in region_cities
                }
                for future in as_completed(future_to_city):
                    rc = future_to_city[future]
                    try:
                        city_results = future.result()
                        raw_results.extend(city_results)
                        print_status(f"  {rc}: +{len(city_results)} записей", "success")
                    except Exception as e:
                        logger.error(f"  {rc}: ошибка парсинга — {e}")
                        print_status(f"  {rc}: ошибка — {e}", "warning")
        else:
            # Последовательный парсинг
            for rc in region_cities:
                try:
                    city_results = self._scrape_single_city(rc, city, cat_cache)
                    raw_results.extend(city_results)
                    print_status(f"  {rc}: +{len(city_results)} записей", "success")
                except Exception as e:
                    logger.error(f"  {rc}: ошибка парсинга — {e}")
                    print_status(f"  {rc}: ошибка — {e}", "warning")

        # Все результаты сохраняем под одним city — вся область вместе
        for r in raw_results:
            r.city = city

        return raw_results

    def _scrape_single_city(self, rc: str, city: str, cat_cache: dict) -> list:
        """Скрапинг одного города (для ThreadPoolExecutor)."""
        print_status(f"  Парсинг: {rc}", "info")
        city_results = []

        jsprav_cats = get_categories(cat_cache, "jsprav", rc)
        jsprav_sub = get_subdomain(cat_cache, "jsprav", rc, self.config)
        yell_cats = get_categories(cat_cache, "yell", rc)
        firmsru_cats = get_categories(cat_cache, "firmsru", rc)

        # 1. Быстрые скреперы (без Playwright)
        if self.region_resolver.is_source_enabled("jsprav"):
            jsprav = JspravScraper(
                self.config, rc, categories=jsprav_cats, subdomain=jsprav_sub
            )
            city_results.extend(jsprav.run())

        if self.region_resolver.is_source_enabled("web_search"):
            web_search = WebSearchScraper(self.config, rc)
            city_results.extend(web_search.run())

        # 2. Playwright скреперы (NOT parallelizable — shared browser session)
        pw_sources = ["dgis", "yell", "firmsru"]
        if any(self.region_resolver.is_source_enabled(s) for s in pw_sources):
            with playwright_session(headless=True) as (browser, page):
                if page:
                    if self.region_resolver.is_source_enabled("dgis"):
                        dgis = DgisScraper(self.config, rc, page)
                        city_results.extend(dgis.run())
                    if self.region_resolver.is_source_enabled("yell"):
                        yell = YellScraper(self.config, rc, page, categories=yell_cats)
                        city_results.extend(yell.run())
                    if self.region_resolver.is_source_enabled("firmsru"):
                        firmsru = FirmsruScraper(
                            self.config, rc, page, categories=firmsru_cats
                        )
                        city_results.extend(firmsru.run())

        return city_results

    def _save_raw(self, raw_results: list) -> None:
        """Сохранить сырые данные в БД."""
        with self.db.session_scope() as session:
            for r in raw_results:
                # Сериализация geo: list[float] → "lat,lon" (String в БД)
                geo_str = None
                if r.geo:
                    try:
                        geo_str = ",".join(str(v) for v in r.geo)
                    except (TypeError, ValueError):
                        pass

                row = RawCompanyRow(
                    source=r.source.value,
                    source_url=r.source_url,
                    name=r.name,
                    phones=r.phones,
                    address_raw=r.address_raw,
                    website=r.website,
                    emails=r.emails,
                    geo=geo_str,
                    scraped_at=r.scraped_at,
                    city=r.city,
                    messengers=r.messengers,
                )
                session.add(row)
            print_status(f"Собрано {len(raw_results)} записей", "success")
