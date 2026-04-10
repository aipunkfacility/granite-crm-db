# pipeline/manager.py
"""Лёгкий оркестратор пайплайна обогащения данных.

Рефакторинг: из 807 строк → ~60. Вся бизнес-логика вынесена в отдельные фазы:
  - pipeline/web_client.py — WebClient (requests + BeautifulSoup)
  - pipeline/region_resolver.py — RegionResolver (конфигурация городов)
  - pipeline/scraping_phase.py   — ScrapingPhase (скрапинг)
  - pipeline/dedup_phase.py      — DedupPhase (дедупликация)
  - pipeline/enrichment_phase.py — EnrichmentPhase (обогащение + веб-поиск, sync/async)
  - pipeline/scoring_phase.py    — ScoringPhase (скоринг + сегментация)
  - pipeline/export_phase.py     — ExportPhase (CSV + пресеты)
"""
import asyncio
from loguru import logger
from granite.database import Database
from granite.pipeline.checkpoint import CheckpointManager
from granite.pipeline.status import print_status

from granite.pipeline.web_client import WebClient
from granite.pipeline.region_resolver import RegionResolver
from granite.pipeline.scraping_phase import ScrapingPhase
from granite.pipeline.dedup_phase import DedupPhase
from granite.pipeline.enrichment_phase import EnrichmentPhase
from granite.pipeline.scoring_phase import ScoringPhase
from granite.pipeline.export_phase import ExportPhase

__all__ = ["PipelineManager", "PipelineCriticalError"]


class PipelineCriticalError(Exception):
    """Критическая ошибка пайплайна: фаза scraping или dedup не удалась.

    Выбрасывается из PipelineManager._run_phase() вместо sys.exit(1),
    чтобы вызывающий код (cli.py) мог решить, как обрабатывать ошибку.
    """
    pass


class PipelineManager:
    """Оркестрация фаз пайплайна обогащения компаний."""

    def __init__(self, config: dict, db: Database):
        self.config = config
        self.db = db
        self.checkpoints = CheckpointManager(db)

        self.region = RegionResolver(config)

        # WebClient config: enrichment.web_client (новая секция) с fallback на sources.web_search
        wc_config = config.get("enrichment", {}).get("web_client", {})
        if not wc_config:
            wc_config = config.get("sources", {}).get("web_search", {})
        self.web = WebClient(
            timeout=wc_config.get("timeout", 60),
            search_limit=wc_config.get("search_limit", 3),
            search_delay=wc_config.get("search_delay", 2.0),
        )
        self.scraping = ScrapingPhase(config, db, self.region)
        self.dedup = DedupPhase(db)
        self.enrichment = EnrichmentPhase(config, db, self.web)
        self.export = ExportPhase(config, db)
        # Lazy-loaded: ScoringPhase, NetworkDetector, ReverseLookup
        self._scoring = None
        self._network_detector = None
        self._reverse_lookup = None

    @property
    def scoring(self):
        if self._scoring is None:
            from granite.enrichers.classifier import Classifier
            from granite.pipeline.scoring_phase import ScoringPhase
            self._scoring = ScoringPhase(self.db, Classifier(self.config))
        return self._scoring

    @property
    def network_detector(self):
        if self._network_detector is None:
            from granite.enrichers.network_detector import NetworkDetector
            self._network_detector = NetworkDetector(self.db)
        return self._network_detector

    @property
    def reverse_lookup(self):
        if self._reverse_lookup is None:
            from granite.enrichers.reverse_lookup import ReverseLookupEnricher
            self._reverse_lookup = ReverseLookupEnricher(self.config, self.db)
        return self._reverse_lookup

    def run_city(self, city: str, force: bool = False,
                 run_scrapers: bool = True, re_enrich: bool = False):
        """Запуск полного цикла для города (и всех городов этой же области)."""
        print_status(f"Запуск конвейера для: {city}", "bold")

        region_cities = self.region.get_region_cities(city)
        if len(region_cities) > 1:
            print_status(f"Область включает города: {', '.join(region_cities)}", "info")

        if force:
            print_status("Флаг --force: очистка старых данных...", "warning")
            self.checkpoints.clear_city(city)

        # --re-enrich: перескакиваем на обогащение, не трогаем scrape/dedup/enriched
        stage = self.checkpoints.get_stage(city)
        print_status(f"Определен этап старта: {stage}")

        if re_enrich:
            # Пропускаем scrape+dedup, запускаем только точечный поиск (проход 2)
            self._run_phase("обогащение (re-enrich)", lambda: self.enrichment.run_deep_enrich_existing(city))
        else:
            if stage == "start" and run_scrapers:
                self._run_phase("скрапинг", lambda: self.scraping.run(city, region_cities))
                stage = "scraped"

            if stage == "scraped":
                self._run_phase("дедупликация", lambda: self.dedup.run(city))
                stage = "deduped"

            if stage == "deduped":
                if self.enrichment._is_async_enabled():
                    self._run_phase("обогащение (async)",
                                   lambda: self.enrichment.run_async(city))
                else:
                    self._run_phase("обогащение",
                                   lambda: self.enrichment.run(city))

        # Reverse lookup enrichment (между обогащением и детектором сетей)
        rl_config = self.config.get("enrichment", {}).get("reverse_lookup", {})
        if rl_config.get("enabled", False):
            self._run_phase("reverse lookup", lambda: self.reverse_lookup.run(city))

        # Пересчёт сетей только для текущего города/области
        print_status("Проверка филиальных сетей...", "info")
        self._run_phase("сетей", lambda: self.network_detector.scan_for_networks(threshold=2, city=city))

        # Пересчет скоринга (т.к. мы обновили is_network)
        self._run_phase("скоринг", lambda: self.scoring.run(city))

        # Автоэкспорт
        self._run_phase("экспорт", lambda: self.export.run(city))

        print_status(f"Город {city} завершен!", "success")

    _CRITICAL_PHASES = frozenset({"скрапинг", "дедупликация"})

    def _run_phase(self, name: str, fn) -> None:
        """Обёртка для фазы с обработкой ошибок. Критические фазы прерывают pipeline.

        Поддерживает как sync, так и async функции (detects coroutine functions
        и запускает через asyncio.run).
        """
        try:
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
        except Exception as e:
            logger.error(f"Ошибка фазы '{name}': {e}")
            print_status(f"[ОШИБКА] Фаза '{name}' завершена с ошибкой: {e}", "warning")
            if name in self._CRITICAL_PHASES:
                print_status(f"Критическая фаза '{name}' не удалась. Остановка.", "error")
                raise PipelineCriticalError(f"Критическая фаза '{name}' не удалась: {e}") from e
