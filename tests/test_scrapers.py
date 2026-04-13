# tests/test_scrapers.py — Тесты парсеров с моками HTTP-запросов
import pytest
from unittest.mock import patch, MagicMock
from granite.models import RawCompany, Source
from granite.scrapers.jsprav import JspravScraper


class TestBaseScraper:
    """Тесты базового класса скреперов."""

    def test_get_city_config_found(self):
        from granite.scrapers.base import BaseScraper

        class DummyScraper(BaseScraper):
            def scrape(self):
                return []

        config = {"cities": [{"name": "Астрахань", "population": 468000}]}
        scraper = DummyScraper(config, "Астрахань")
        assert scraper.city_config["population"] == 468000

    def test_get_city_config_not_found(self):
        from granite.scrapers.base import BaseScraper

        class DummyScraper(BaseScraper):
            def scrape(self):
                return []

        scraper = DummyScraper({}, "Неизвестный")
        assert scraper.city_config == {}

    def test_run_calls_scrape(self):
        from granite.scrapers.base import BaseScraper

        class DummyScraper(BaseScraper):
            def scrape(self):
                return [RawCompany(source=Source.WEB_SEARCH, name="Test")]

        scraper = DummyScraper({}, "Test")
        results = scraper.run()
        assert len(results) == 1
        assert results[0].name == "Test"

    def test_run_catches_exception(self):
        from granite.scrapers.base import BaseScraper

        class FailingScraper(BaseScraper):
            def scrape(self):
                raise RuntimeError("test error")

        scraper = FailingScraper({}, "Test")
        results = scraper.run()
        assert results == []


class TestJspravScraper:

    @pytest.fixture
    def config(self):
        return {
            "cities": [{"name": "Астрахань", "population": 468000}],
            "sources": {
                "jsprav": {
                    "enabled": True,
                    "subdomain_map": {"астрахань": "astrahan"}
                }
            }
        }

    @pytest.fixture
    def sample_jsonld(self):
        """JSON-LD с одной компанией."""
        return '''{
            "@context": "https://schema.org",
            "@type": "ItemList",
            "itemListElement": [{
                "@type": "ListItem",
                "item": {
                    "@type": "LocalBusiness",
                    "name": "Гранит-Мастер",
                    "telephone": ["+7 (903) 123-45-67"],
                    "address": {
                        "@type": "PostalAddress",
                        "streetAddress": "ул. Ленина, 10",
                        "addressLocality": "Астрахань"
                    },
                    "url": "https://granit-master.ru",
                    "sameAs": ["https://granit-master.ru"],
                    "geo": {"latitude": "46.35", "longitude": "48.03"}
                }
            }]
        }'''

    def test_subdomain_from_map(self, config):
        scraper = JspravScraper(config, "Астрахань")
        assert scraper._get_subdomain() == "astrahan"

    def test_subdomain_slugify_fallback(self, config):
        scraper = JspravScraper(config, "Новый Город")
        assert scraper._get_subdomain() == "novyy-gorod"

    def test_subdomain_cached(self, config):
        scraper = JspravScraper(config, "Астрахань", subdomain="custom")
        assert scraper._get_subdomain() == "custom"

    def test_is_local_exact_match(self, config):
        scraper = JspravScraper(config, "Астрахань")
        assert scraper._is_local({"addressLocality": "Астрахань"}) is True

    def test_is_local_different_city(self, config):
        scraper = JspravScraper(config, "Астрахань")
        assert scraper._is_local({"addressLocality": "Москва"}) is False

    def test_is_local_no_locality(self, config):
        """Если адрес не содержит город — считаем локальным."""
        scraper = JspravScraper(config, "Астрахань")
        assert scraper._is_local({}) is True

    def test_is_local_prefix_match(self, config):
        scraper = JspravScraper(config, "Астрахань")
        assert scraper._is_local({"addressLocality": "Астрахани"}) is True

    def test_parse_companies_from_soup(self, config, sample_jsonld):
        scraper = JspravScraper(config, "Астрахань")
        from bs4 import BeautifulSoup

        html = f'<script type="application/ld+json">{sample_jsonld}</script>'
        soup = BeautifulSoup(html, "html.parser")
        companies = scraper._parse_companies_from_soup(soup, seen_urls=set())

        assert len(companies) == 1
        c = companies[0]
        assert c.name == "Гранит-Мастер"
        assert c.source == Source.JSPRAV
        assert "79031234567" in c.phones
        assert c.website == "https://granit-master.ru"
        assert c.city == "Астрахань"
        assert c.geo is not None

    def test_parse_skips_duplicate_urls(self, config, sample_jsonld):
        """Дубли по URL не добавляются."""
        scraper = JspravScraper(config, "Астрахань")
        from bs4 import BeautifulSoup

        html = f'<script type="application/ld+json">{sample_jsonld}</script>'
        html += f'<script type="application/ld+json">{sample_jsonld}</script>'
        soup = BeautifulSoup(html, "html.parser")
        companies = scraper._parse_companies_from_soup(soup, seen_urls=set())

        assert len(companies) == 1

    def test_parse_skips_foreign_city(self, config):
        """Компании из другого города фильтруются."""
        foreign_jsonld = '''{
            "@type": "ItemList",
            "itemListElement": [{
                "@type": "ListItem",
                "item": {
                    "@type": "LocalBusiness",
                    "name": "Moscow Granite",
                    "telephone": [],
                    "address": {"@type": "PostalAddress", "addressLocality": "Москва"},
                    "url": "https://moscow-granit.ru"
                }
            }]
        }'''
        scraper = JspravScraper(config, "Астрахань")
        from bs4 import BeautifulSoup

        html = f'<script type="application/ld+json">{foreign_jsonld}</script>'
        soup = BeautifulSoup(html, "html.parser")
        companies = scraper._parse_companies_from_soup(soup, seen_urls=set())
        assert len(companies) == 0

    def test_parse_handles_malformed_json(self, config):
        """Битый JSON-LD не ломает парсер."""
        scraper = JspravScraper(config, "Астрахань")
        from bs4 import BeautifulSoup

        html = '<script type="application/ld+json">{bad json}</script>'
        soup = BeautifulSoup(html, "html.parser")
        companies = scraper._parse_companies_from_soup(soup, seen_urls=set())
        assert len(companies) == 0

    def test_extract_page_num(self):
        assert JspravScraper._extract_page_num("https://site.ru/category/page-3/") == 3
        assert JspravScraper._extract_page_num("https://site.ru/category/?page=5") == 5
        assert JspravScraper._extract_page_num("https://site.ru/category/") == 1


class TestJspravPlaywrightScraper:

    @pytest.fixture
    def pw_config(self):
        return {
            "cities": [{"name": "Астрахань", "population": 468000}],
            "sources": {
                "jsprav": {
                    "enabled": True,
                    "subdomain_map": {"астрахань": "astrahan"}
                }
            }
        }

    def test_returns_empty_without_page(self, pw_config):
        """No page provided → early return."""
        from granite.scrapers.jsprav_playwright import JspravPlaywrightScraper
        scraper = JspravPlaywrightScraper(pw_config, "Астрахань")
        result = scraper.scrape()
        assert result == []

    def test_rejects_invalid_subdomain_from_config(self, pw_config):
        """Subdomain_map with invalid value should be rejected."""
        from granite.scrapers.jsprav_playwright import JspravPlaywrightScraper
        pw_config["sources"]["jsprav"]["subdomain_map"] = {"астрахань": "attacker.evil.com"}
        mock_page = MagicMock()
        scraper = JspravPlaywrightScraper(pw_config, "Астрахань", playwright_page=mock_page)
        result = scraper.scrape()
        assert result == []
        mock_page.goto.assert_not_called()


class TestModels:
    """Тесты Pydantic-моделей данных."""

    def test_raw_company_defaults(self):
        rc = RawCompany(source=Source.WEB_SEARCH, name="Test")
        assert rc.phones == []
        assert rc.emails == []
        assert rc.messengers == {}
        assert rc.city == ""
        assert rc.geo is None

    def test_raw_company_with_geo(self):
        rc = RawCompany(source=Source.JSPRAV, name="Test", geo=[46.35, 48.03])
        assert rc.geo == [46.35, 48.03]

    def test_source_enum(self):
        assert Source.WEB_SEARCH == "web_search"
        assert Source.JSPRAV == "jsprav"
        assert Source.DGIS == "2gis"
        assert Source.YELL == "yell"
        assert Source.JSPRAV_PW == "jsprav_playwright"
        assert Source.GOOGLE_MAPS == "google_maps"
        assert Source.AVITO == "avito"
