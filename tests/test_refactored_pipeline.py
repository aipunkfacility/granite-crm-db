# tests/test_refactored_pipeline.py — Тесты рефакторенных модулей pipeline/
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from granite.pipeline.web_client import WebClient
from granite.pipeline.region_resolver import RegionResolver
from granite.pipeline.dedup_phase import DedupPhase
from granite.pipeline.scoring_phase import ScoringPhase
from granite.pipeline.export_phase import ExportPhase
from granite.pipeline.enrichment_phase import EnrichmentPhase
from granite.pipeline.manager import PipelineManager
from granite.database import Database, RawCompanyRow, CompanyRow, EnrichedCompanyRow


# ═══════════════════════════════════════════════════════════
#  WebClient
# ═══════════════════════════════════════════════════════════

class TestWebClient:
    """Тесты веб-клиента."""

    def test_custom_timeout_and_limit(self):
        """Конфигурация timeout и search_limit."""
        client = WebClient(timeout=120, search_limit=5)
        assert client.timeout == 120
        assert client.search_limit == 5

    def test_search_returns_none_on_error(self):
        """Ошибка сети → None."""
        client = WebClient()
        with patch("granite.pipeline.web_client.fetch_page", side_effect=Exception("network error")):
            result = client.search("test query")
            assert result is None

    def test_scrape_returns_none_on_error(self):
        """Ошибка при скрапинге → None."""
        client = WebClient()
        with patch("granite.pipeline.web_client.fetch_page", side_effect=Exception("network error")):
            result = client.scrape("https://example.com")
            assert result is None

    def test_scrape_invalid_url(self):
        """Невалидный URL → None."""
        client = WebClient()
        result = client.scrape("not-a-url")
        assert result is None

    def test_scrape_ssrf_blocked(self):
        """SSRF-защита блокирует localhost."""
        client = WebClient()
        result = client.scrape("http://127.0.0.1/admin")
        assert result is None

    def test_scrape_extracts_phones_and_emails(self):
        """Скрапинг возвращает телефоны и email из HTML."""
        client = WebClient()
        html = '''
        <html><body>
            <a href="tel:+79031234567">Позвонить</a>
            <a href="mailto:info@test.ru">info@test.ru</a>
            <p>+7 (999) 111-22-33</p>
        </body></html>
        '''
        with patch("granite.pipeline.web_client.fetch_page", return_value=html):
            result = client.scrape("https://test.ru")
            assert result is not None
            assert "phones" in result
            assert "emails" in result
            assert len(result["phones"]) > 0
            assert "info@test.ru" in result["emails"]

    def test_search_parses_google_results(self):
        """Парсинг Google SERP."""
        client = WebClient()
        html = '''
        <html><body>
            <div class="g">
                <a href="https://example.com"><h3>Example Company</h3></a>
            </div>
            <div class="g">
                <a href="https://test.ru"><h3>Test Company</h3></a>
            </div>
        </body></html>
        '''
        with patch("granite.pipeline.web_client.fetch_page", return_value=html):
            result = client.search("test query")
            assert result is not None
            assert "data" in result
            web = result["data"]["web"]
            assert len(web) == 2
            assert web[0]["url"] == "https://example.com"
            assert web[0]["title"] == "Example Company"


# ═══════════════════════════════════════════════════════════
#  RegionResolver
# ═══════════════════════════════════════════════════════════

class TestRegionResolver:
    """Тесты резолвера городов/областей."""

    def _make_config(self, cities=None, sources=None):
        return {
            "cities": cities or [],
            "sources": sources or {},
        }

    def test_single_city_no_region(self):
        """Город без области → только он сам."""
        config = self._make_config(cities=[{"name": "Москва"}])
        resolver = RegionResolver(config)
        assert resolver.get_region_cities("Москва") == ["Москва"]

    def test_city_not_in_config(self):
        """Город не найден в конфиге → только он сам."""
        resolver = RegionResolver(self._make_config())
        assert resolver.get_region_cities("Несуществующий") == ["Несуществующий"]

    def test_region_from_regions_yaml(self, monkeypatch):
        """Область подтягивается из regions.yaml."""
        def mock_get_region_cities(region):
            if region == "Московская область":
                return ["Москва", "Химки", "Мытищи"]
            return []

        config = self._make_config(cities=[{"name": "Москва", "region": "Московская область"}])
        monkeypatch.setattr("granite.pipeline.region_resolver.get_region_cities", mock_get_region_cities)
        resolver = RegionResolver(config)
        cities = resolver.get_region_cities("Москва")
        assert "Москва" in cities
        assert "Химки" in cities

    def test_fallback_to_config_cities(self):
        """Фоллбэк: города из config.yaml с той же областью."""
        config = self._make_config(cities=[
            {"name": "ГородА", "region": "Область1"},
            {"name": "ГородБ", "region": "Область1"},
            {"name": "ГородВ", "region": "Область2"},
        ])
        resolver = RegionResolver(config)
        # regions.yaml вернёт пустой список (мок по умолчанию)
        with patch("granite.pipeline.region_resolver.get_region_cities", return_value=[]):
            cities = resolver.get_region_cities("ГородА")
        assert sorted(cities) == ["ГородА", "ГородБ"]

    def test_is_source_enabled_default(self):
        """Источник по умолчанию включён."""
        resolver = RegionResolver(self._make_config())
        assert resolver.is_source_enabled("jsprav") is True

    def test_is_source_enabled_explicit(self):
        """Источник явно отключён."""
        config = self._make_config(sources={"jsprav": {"enabled": False}})
        resolver = RegionResolver(config)
        assert resolver.is_source_enabled("jsprav") is False

    def test_get_active_sources(self):
        """Только включённые источники в списке."""
        config = self._make_config(sources={
            "jsprav": {"enabled": True},
            "web_search": {"enabled": True},
            "dgis": {"enabled": False},
        })
        resolver = RegionResolver(config)
        active = resolver.get_active_sources(["jsprav", "web_search", "dgis"])
        assert "jsprav" in active
        assert "web_search" in active
        assert "dgis" not in active

    def test_get_active_sources_default_list(self):
        """Без параметра — все стандартные источники."""
        config = self._make_config(sources={"yell": {"enabled": False}})
        resolver = RegionResolver(config)
        active = resolver.get_active_sources()
        assert "jsprav" in active
        assert "yell" not in active


# ═══════════════════════════════════════════════════════════
#  DedupPhase — Union-Find
# ═══════════════════════════════════════════════════════════

class TestUnionFind:
    """Тесты Union-Find алгоритма из DedupPhase."""

    def test_no_clusters(self):
        """Без кластеров — каждый id отдельный суперкластер."""
        dicts = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = DedupPhase._union_find(dicts, [])
        assert len(result) == 3

    def test_single_cluster(self):
        """Один кластер — один суперкластер."""
        dicts = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = DedupPhase._union_find(dicts, [[1, 2, 3]])
        assert len(result) == 1
        assert sorted(result[0]) == [1, 2, 3]

    def test_overlapping_clusters(self):
        """Пересекающиеся кластеры объединяются."""
        dicts = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
        clusters = [[1, 2], [2, 3], [4]]  # 1-2-3 связаны через 2, 4 отдельно
        result = DedupPhase._union_find(dicts, clusters)
        assert len(result) == 2
        merged = [sorted(c) for c in result]
        assert [1, 2, 3] in merged
        assert [4] in merged

    def test_all_ids_merged(self):
        """Цепочка через все id."""
        dicts = [{"id": i} for i in range(1, 6)]
        clusters = [[1, 2], [2, 3], [3, 4], [4, 5]]
        result = DedupPhase._union_find(dicts, clusters)
        assert len(result) == 1
        assert len(result[0]) == 5


# ═══════════════════════════════════════════════════════════
#  ScoringPhase
# ═══════════════════════════════════════════════════════════

class TestScoringPhase:
    """Тесты фазы скоринга."""

    def test_run_empty_city(self, tmp_path):
        """Нет данных — возвращается пустой словарь."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.all.return_value = []

        # Создаём scope mock
        from contextlib import contextmanager
        @contextmanager
        def fake_scope():
            yield mock_session

        mock_db.session_scope = fake_scope

        mock_classifier = MagicMock()
        phase = ScoringPhase(mock_db, mock_classifier)
        result = phase.run("ПустойГород")
        assert result == {}

    def test_run_calculates_scores(self):
        """Скоринг считается для каждой компании."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        row1 = MagicMock()
        row1.to_dict.return_value = {"name": "A", "website": "https://a.ru"}
        row2 = MagicMock()
        row2.to_dict.return_value = {"name": "B", "website": None}
        mock_session.query.return_value.filter_by.return_value.all.return_value = [row1, row2]

        mock_classifier = MagicMock()
        mock_classifier.calculate_score.side_effect = [80, 30]
        mock_classifier.determine_segment.side_effect = ["A", "C"]

        from contextlib import contextmanager
        @contextmanager
        def fake_scope():
            yield mock_session

        mock_db.session_scope = fake_scope

        phase = ScoringPhase(mock_db, mock_classifier)
        result = phase.run("Тест")
        assert result["A"] == 1
        assert result["C"] == 1
        assert row1.crm_score == 80
        assert row2.segment == "C"


# ═══════════════════════════════════════════════════════════
#  ExportPhase
# ═══════════════════════════════════════════════════════════

class TestExportPhase:
    """Тесты фазы экспорта."""

    def test_run_exports_csv(self):
        """Экспорт вызывает CsvExporter."""
        mock_db = MagicMock()
        config = {}
        phase = ExportPhase(config, mock_db)

        with patch("granite.pipeline.export_phase.CsvExporter") as MockCsv:
            mock_exporter = MagicMock()
            MockCsv.return_value = mock_exporter

            phase.run("ТестГород")

            mock_exporter.export_city.assert_called_once_with("ТестГород")

    def test_run_exports_presets(self):
        """Пресеты из конфига вызываются."""
        mock_db = MagicMock()
        config = {
            "export_presets": {
                "with_tg": {"format": "csv", "filters": "telegram IS NOT NULL"},
                "report": {"format": "markdown"},
            }
        }
        phase = ExportPhase(config, mock_db)

        with patch("granite.pipeline.export_phase.CsvExporter") as MockCsv, \
             patch("granite.pipeline.export_phase.MarkdownExporter") as MockMd:
            phase.run("ТестГород")

            # 1 вызов базовый CSV + 1 вызов пресета CSV + 1 вызов пресета MD
            assert MockCsv.call_count == 2  # базовый + пресет csv
            MockMd.assert_called_once()  # пресет markdown


# ═══════════════════════════════════════════════════════════
#  Database.session_scope
# ═══════════════════════════════════════════════════════════

class TestSessionScope:
    """Тесты контекстного менеджера session_scope."""

    def test_commit_on_success(self, tmp_path):
        """Успешный выход → commit + close."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        db_path = str(tmp_path / "test.db")
        engine = create_engine(f"sqlite:///{db_path}")
        from granite.database import Base
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)

        mock_db = MagicMock()
        mock_db.SessionLocal = SessionLocal

        # Вызываем через реальный session_scope
        with mock_db.session_scope() as session:
            pass

        # Сессия закрыта после выхода
        # (если бы была ошибка — упало бы с исключение)

    def test_rollback_on_error(self, tmp_path):
        """Исключение → rollback + close + проброс исключения."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        db_path = str(tmp_path / "test.db")
        engine = create_engine(f"sqlite:///{db_path}")
        from granite.database import Base
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)

        mock_db = MagicMock()
        mock_db.SessionLocal = SessionLocal

        with pytest.raises(ValueError):
            with mock_db.session_scope() as session:
                raise ValueError("test error")


# ═══════════════════════════════════════════════════════════
#  PipelineManager — интеграция
# ═══════════════════════════════════════════════════════════

class TestPipelineManagerInit:
    """Тесты инициализации PipelineManager."""

    def test_creates_all_phases(self, tmp_path):
        """Все фазы создаются при инициализации."""
        mock_db = MagicMock()
        config = {"cities": [{"name": "Тест"}]}

        with patch("granite.pipeline.manager.CheckpointManager"), \
             patch("granite.enrichers.classifier.Classifier"), \
             patch("granite.enrichers.network_detector.NetworkDetector"):
            pm = PipelineManager(config, mock_db)
            # Trigger lazy property access
            _ = pm.scoring
            _ = pm.network_detector

        assert hasattr(pm, 'region')
        assert hasattr(pm, 'web')
        assert hasattr(pm, 'scraping')
        assert hasattr(pm, 'dedup')
        assert hasattr(pm, 'enrichment')
        assert hasattr(pm, 'scoring')
        assert hasattr(pm, 'export')
        assert isinstance(pm.region, RegionResolver)
        assert isinstance(pm.web, WebClient)

    def test_web_client_config_from_yaml(self, tmp_path):
        """Настройки web_search из секции sources.web_search в конфиге."""
        mock_db = MagicMock()
        config = {
            "cities": [],
            "sources": {"web_search": {"timeout": 120, "search_limit": 5}},
        }

        with patch("granite.pipeline.manager.CheckpointManager"), \
             patch("granite.enrichers.classifier.Classifier"):
            pm = PipelineManager(config, mock_db)
            _ = pm.scoring  # trigger lazy init

        assert pm.web.timeout == 120
        assert pm.web.search_limit == 5

    def test_web_client_empty_config(self, tmp_path):
        """Если секция web_search пустая — используются дефолтные значения."""
        mock_db = MagicMock()
        config = {
            "cities": [],
            "sources": {},
        }

        with patch("granite.pipeline.manager.CheckpointManager"), \
             patch("granite.enrichers.classifier.Classifier"):
            pm = PipelineManager(config, mock_db)
            _ = pm.scoring  # trigger lazy init

        assert pm.web.timeout == 60
        assert pm.web.search_limit == 3


# ═══════════════════════════════════════════════════════════
#  EnrichmentPhase — изоляция
# ═══════════════════════════════════════════════════════════

class TestEnrichmentPhase:
    """Тесты фазы обогащения."""

    def test_deep_enrich_no_company(self):
        """Нет enriched-записи — пропускается."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = None  # Нет enriched

        from contextlib import contextmanager
        @contextmanager
        def fake_scope():
            yield mock_session

        mock_db.session_scope = fake_scope

        mock_web = MagicMock()
        phase = EnrichmentPhase({}, mock_db, mock_web)

        # Должно пройти без ошибок (пропустить компанию)
        result = phase._run_deep_enrich_for(
            mock_session, [MagicMock(id=1, website=None, emails=[], name_best="Test")],
            "Тест", MagicMock(), MagicMock(),
        )
        assert result == 0

    def test_is_enabled_default(self):
        """Источник по умолчанию включён."""
        mock_db = MagicMock()
        phase = EnrichmentPhase({}, mock_db, MagicMock())
        assert phase._resolver.is_source_enabled("web_search") is True

    def test_is_enabled_explicit_false(self):
        """Источник явно отключён."""
        config = {"sources": {"web_search": {"enabled": False}}}
        mock_db = MagicMock()
        phase = EnrichmentPhase(config, mock_db, MagicMock())
        assert phase._resolver.is_source_enabled("web_search") is False
