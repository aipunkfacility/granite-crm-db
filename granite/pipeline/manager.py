# pipeline/manager.py
"""Лёгкий оркестратор пайплайна обогащения данных.

Рефакторинг: из 807 строк → ~60. Вся бизнес-логика вынесена в отдельные фазы:
  - pipeline/web_client.py — WebClient (requests + BeautifulSoup)
  - pipeline/region_resolver.py — RegionResolver (конфигурация городов)
  - pipeline/scraping_phase.py   — ScrapingPhase (скрапинг)
  - pipeline/dedup_phase.py      — DedupPhase (дедупликация)
  - pipeline/enrichment_phase.py — EnrichmentPhase (обогащение + веб-поиск)
  - pipeline/scoring_phase.py    — ScoringPhase (скоринг + сегментация)
  - pipeline/export_phase.py     — ExportPhase (CSV + пресеты)
"""
import sys
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

__all__ = ["PipelineManager"]


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
        )
        self.scraping = ScrapingPhase(config, db, self.region)
        self.dedup = DedupPhase(db)
        self.enrichment = EnrichmentPhase(config, db, self.web)
        self.export = ExportPhase(config, db)
        # Lazy-loaded: ScoringPhase, NetworkDetector (тяжёлые зависимости)
        self._scoring = None
        self._network_detector = None

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
                self._run_phase("обогащение", lambda: self.enrichment.run(city))

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
        """Обёртка для фазы с обработкой ошибок. Критические фазы прерывают pipeline."""
        try:
            fn()
        except Exception as e:
            logger.error(f"Ошибка фазы '{name}': {e}")
            print_status(f"[ОШИБКА] Фаза '{name}' завершена с ошибкой: {e}", "warning")
            if name in self._CRITICAL_PHASES:
                print_status(f"Критическая фаза '{name}' не удалась. Остановка.", "error")
                sys.exit(1)
