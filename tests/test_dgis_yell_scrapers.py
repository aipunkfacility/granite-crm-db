# tests/test_dgis_yell_scrapers.py — Тесты переписанных скраперов 2GIS и Yell (Phase 7)
import asyncio

import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from granite.models import RawCompany, Source
from granite.scrapers.dgis import DgisScraper, DGIS_REGION_IDS, _get_dgis_region_id
from granite.scrapers.yell import YellScraper


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def dgis_config():
    """Базовый конфиг для DgisScraper."""
    return {
        "cities": [{"name": "Омск", "population": 1100000}],
        "sources": {
            "dgis": {
                "enabled": True,
                "api_key": "test_dgis_key",
                "search_category": "изготовление памятников",
                "max_pages": 3,
                "delay": 0.01,
            }
        }
    }


@pytest.fixture
def dgis_no_api_config():
    """Конфиг без API ключа (Crawlee fallback mode)."""
    return {
        "cities": [{"name": "Омск", "population": 1100000}],
        "sources": {
            "dgis": {
                "enabled": True,
                "search_category": "изготовление памятников",
                "max_pages": 2,
            }
        }
    }


@pytest.fixture
def yell_config():
    """Базовый конфиг для YellScraper."""
    return {
        "cities": [{"name": "Омск", "population": 1100000}],
        "sources": {
            "yell": {
                "enabled": True,
                "base_path": "/catalog/izgotovlenie_pamyatnikov/",
                "max_pages": 3,
                "delay": 0.01,
            }
        }
    }


@pytest.fixture
def yell_with_categories_config():
    """Конфиг для YellScraper с категориями."""
    return {
        "cities": [{"name": "Омск", "population": 1100000}],
        "sources": {
            "yell": {
                "enabled": True,
                "max_pages": 2,
            }
        }
    }


# ============================================================
# DgisScraper: Region ID mapping
# ============================================================

class TestDgisRegionId:

    def test_moscow(self):
        assert _get_dgis_region_id("Москва") == "32"

    def test_saint_petersburg(self):
        assert _get_dgis_region_id("Санкт-Петербург") == "49"

    def test_novosibirsk(self):
        assert _get_dgis_region_id("Новосибирск") == "131"

    def test_case_insensitive(self):
        assert _get_dgis_region_id("москва") == "32"
        assert _get_dgis_region_id("МОСКВА") == "32"

    def test_unknown_city(self):
        assert _get_dgis_region_id("НеизвестныйГород") == ""

    def test_empty_city(self):
        assert _get_dgis_region_id("") == ""

    def test_small_city_fallback(self):
        """Малые города → region_id области."""
        assert _get_dgis_region_id("Тара") == "131"
        assert _get_dgis_region_id("Исилькуль") == "131"

    def test_all_region_ids_are_integers(self):
        for city, rid in DGIS_REGION_IDS.items():
            assert isinstance(rid, int), f"Invalid region_id for {city}: {rid}"


# ============================================================
# DgisScraper: Config & Init
# ============================================================

class TestDgisScraperInit:

    def test_init_defaults(self, dgis_config):
        scraper = DgisScraper(dgis_config, "Омск")
        assert scraper.city == "Омск"
        assert scraper.api_key == "test_dgis_key"
        assert scraper.search_category == "изготовление памятников"
        assert scraper.max_pages == 3

    def test_init_no_api_key_uses_env(self, dgis_no_api_config):
        with patch.dict("os.environ", {"DGIS_API_KEY": "env_key"}):
            scraper = DgisScraper(dgis_no_api_config, "Омск")
        assert scraper.api_key == "env_key"

    def test_init_config_key_priority_over_env(self, dgis_config):
        with patch.dict("os.environ", {"DGIS_API_KEY": "env_key"}):
            scraper = DgisScraper(dgis_config, "Омск")
        assert scraper.api_key == "test_dgis_key"

    def test_init_default_search_category(self):
        config = {"cities": [{"name": "Омск"}], "sources": {"dgis": {}}}
        scraper = DgisScraper(config, "Омск")
        assert scraper.search_category == "изготовление памятников"

    def test_init_default_max_pages(self):
        """max_pages = 5 когда не задан в конфиге."""
        config = {
            "cities": [{"name": "Омск"}],
            "sources": {"dgis": {}}
        }
        scraper = DgisScraper(config, "Омск")
        assert scraper.max_pages == 5

    def test_init_playwright_page_ignored(self, dgis_config):
        """Параметр playwright_page принимается для обратной совместимости, но игнорируется."""
        mock_page = MagicMock()
        scraper = DgisScraper(dgis_config, "Омск", playwright_page=mock_page)
        # page не сохраняется как атрибут
        assert not hasattr(scraper, "page")

    def test_inherits_base_scraper(self, dgis_config):
        scraper = DgisScraper(dgis_config, "Омск")
        assert scraper.city_config["population"] == 1100000


# ============================================================
# DgisScraper: API mode — _parse_api_item
# ============================================================

class TestDgisParseApiItem:

    def _make_scraper(self, config=None):
        if config is None:
            config = {
                "cities": [{"name": "Омск"}],
                "sources": {"dgis": {"api_key": "k"}}
            }
        return DgisScraper(config, "Омск")

    def test_parse_full_item(self):
        """Полный элемент 2GIS API — все поля."""
        item = {
            "id": "70000001037404892",
            "name": "Гранит Мастер",
            "address_name": "г. Омск, ул. Ленина, 10",
            "point": {"lat": 54.9885, "lon": 73.3242},
            "contact_groups": [
                {
                    "contacts": [
                        {"type": "phone", "value": "+7 (903) 123-45-67"},
                        {"type": "email", "value": "info@granit.ru"},
                        {"type": "website", "value": "granit.ru"},
                        {"type": "telegram", "value": "https://t.me/granit"},
                    ]
                }
            ],
        }
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)

        assert company is not None
        assert company.name == "Гранит Мастер"
        assert company.source == Source.DGIS
        assert company.phones == ["79031234567"]
        assert company.emails == ["info@granit.ru"]
        assert company.website == "https://granit.ru"
        assert company.address_raw == "г. Омск, ул. Ленина, 10"
        assert company.geo == [54.9885, 73.3242]
        assert company.messengers["telegram"] == "https://t.me/granit"

    def test_parse_minimal_item(self):
        """Минимальный элемент — только имя."""
        item = {"name": "Тест"}
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)
        assert company is not None
        assert company.name == "Тест"
        assert company.phones == []
        assert company.emails == []
        assert company.website is None
        assert company.geo is None

    def test_parse_item_short_name(self):
        """Слишком короткое имя — пропускается."""
        item = {"name": "АБ"}
        scraper = self._make_scraper()
        assert scraper._parse_api_item(item) is None

    def test_parse_item_empty_name(self):
        """Пустое имя — пропускается."""
        item = {"name": ""}
        scraper = self._make_scraper()
        assert scraper._parse_api_item(item) is None

    def test_parse_website_without_protocol(self):
        """Сайт без протокола — добавляется https://."""
        item = {
            "name": "Тест",
            "contact_groups": [
                {"contacts": [{"type": "website", "value": "mysite.ru"}]}
            ]
        }
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)
        assert company.website == "https://mysite.ru"

    def test_parse_multiple_phones(self):
        """Несколько телефонов."""
        item = {
            "name": "Тест",
            "contact_groups": [
                {
                    "contacts": [
                        {"type": "phone", "value": "+7 (903) 123-45-67"},
                        {"type": "phone", "value": "8 903 222-33-44"},
                    ]
                }
            ]
        }
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)
        assert len(company.phones) == 2
        assert "79031234567" in company.phones
        assert "79032223344" in company.phones

    def test_parse_messengers_vk_whatsapp(self):
        """Мессенджеры VK и WhatsApp."""
        item = {
            "name": "Тест",
            "contact_groups": [
                {
                    "contacts": [
                        {"type": "vk", "value": "https://vk.com/granit"},
                        {"type": "whatsapp", "value": "https://wa.me/79031234567"},
                    ]
                }
            ]
        }
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)
        assert company.messengers["vk"] == "https://vk.com/granit"
        assert company.messengers["whatsapp"] == "https://wa.me/79031234567"

    def test_parse_geo_coordinates(self):
        """Гео-координаты."""
        item = {
            "name": "Тест",
            "point": {"lat": 55.75, "lon": 37.62},
        }
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)
        assert company.geo == [55.75, 37.62]

    def test_parse_geo_zero_skipped(self):
        """Нулевые координаты — None."""
        item = {
            "name": "Тест",
            "point": {"lat": 0, "lon": 0},
        }
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)
        assert company.geo is None

    def test_parse_address_fallback(self):
        """Адрес из full_address_name если address_name пуст."""
        item = {
            "name": "Тест",
            "address_name": "",
            "full_address_name": "Омск, ул. Маркса, 5",
        }
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)
        assert company.address_raw == "Омск, ул. Маркса, 5"

    def test_parse_source_url(self):
        """source_url формируется из firm_id."""
        item = {
            "name": "Тест",
            "id": "70000001037404892",
        }
        scraper = self._make_scraper()
        company = scraper._parse_api_item(item)
        assert "70000001037404892" in company.source_url
        assert company.source_url.startswith("https://2gis.ru/")


# ============================================================
# DgisScraper: API mode — _scrape_api
# ============================================================

class TestDgisScrapeApi:

    def test_scrape_api_success_single_page(self, dgis_config):
        """Успешный запрос — одна страница с результатами."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "items": [
                    {
                        "id": "123",
                        "name": "Гранит Мастер",
                        "address_name": "г. Омск, ул. Ленина, 10",
                        "point": {"lat": 55.0, "lon": 73.0},
                        "contact_groups": [
                            {
                                "contacts": [
                                    {"type": "phone", "value": "+7 (903) 123-45-67"},
                                    {"type": "website", "value": "granit.ru"},
                                ]
                            }
                        ],
                    }
                ],
                "total": 1,
            }
        }

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            results = scraper._scrape_api()

        assert len(results) == 1
        assert results[0].name == "Гранит Мастер"
        assert results[0].source == Source.DGIS
        assert results[0].phones == ["79031234567"]
        assert results[0].website == "https://granit.ru"

    def test_scrape_api_pagination(self, dgis_config):
        """Пагинация: 2 страницы."""
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "result": {
                "items": [{"id": "1", "name": "Компания 1"}],
                "total": 2,
            }
        }
        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {
            "result": {
                "items": [{"id": "2", "name": "Компания 2"}],
                "total": 2,
            }
        }

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [page1, page2]
            mock_client_cls.return_value = mock_client

            with patch("granite.scrapers.dgis.adaptive_delay"):
                results = scraper._scrape_api()

        assert len(results) == 2

    def test_scrape_api_empty_results(self, dgis_config):
        """Пустой ответ — пустой список."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"items": [], "total": 0}}

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            results = scraper._scrape_api()

        assert results == []

    def test_scrape_api_403_retry(self, dgis_config):
        """При 403 — пауза и retry (пропуск текущей страницы)."""
        resp_403 = MagicMock()
        resp_403.status_code = 403

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "result": {
                "items": [{"id": "1", "name": "Компания"}],
                "total": 1,
            }
        }

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [resp_403, resp_ok]
            mock_client_cls.return_value = mock_client

            with patch("granite.scrapers.dgis.adaptive_delay"):
                with patch("granite.scrapers.dgis.time.sleep") as mock_sleep:
                    results = scraper._scrape_api()

            # 403 вызывает time.sleep с backoff
            mock_sleep.assert_called_once()

        assert len(results) == 1

    def test_scrape_api_429_retry(self, dgis_config):
        """При 429 — аналогично 403."""
        resp_429 = MagicMock()
        resp_429.status_code = 429

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "result": {
                "items": [{"id": "1", "name": "Компания"}],
                "total": 1,
            }
        }

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [resp_429, resp_ok]
            mock_client_cls.return_value = mock_client

            with patch("granite.scrapers.dgis.adaptive_delay"):
                with patch("granite.scrapers.dgis.time.sleep"):
                    results = scraper._scrape_api()

        assert len(results) == 1

    def test_scrape_api_non_200_stops(self, dgis_config):
        """Код 500 — остановка парсинга."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            results = scraper._scrape_api()

        assert results == []

    def test_scrape_api_timeout_continues(self, dgis_config):
        """Таймаут — переход к следующей странице."""
        import httpx

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "result": {
                "items": [{"id": "1", "name": "Компания"}],
                "total": 1,
            }
        }

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [httpx.TimeoutException("timeout"), resp_ok]
            mock_client_cls.return_value = mock_client

            with patch("granite.scrapers.dgis.adaptive_delay"):
                results = scraper._scrape_api()

        # Таймаут не прерывает — продолжает со следующей страницей
        assert len(results) == 1

    def test_scrape_api_respects_max_pages(self, dgis_config):
        """Остановка после max_pages."""
        def make_response(page):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {
                "result": {
                    "items": [{"id": str(page), "name": f"Компания {page}"}],
                    "total": 100,  # много результатов
                }
            }
            return r

        scraper = DgisScraper(dgis_config, "Омск")
        scraper.max_pages = 2

        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [make_response(1), make_response(2)]
            mock_client_cls.return_value = mock_client

            with patch("granite.scrapers.dgis.adaptive_delay"):
                results = scraper._scrape_api()

        assert len(results) == 2  # только 2 страницы

    def test_scrape_api_stops_when_total_reached(self, dgis_config):
        """Остановка когда total_fetched >= total."""
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {
            "result": {
                "items": [{"id": "1", "name": "Компания"}],
                "total": 1,
            }
        }

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = r
            mock_client_cls.return_value = mock_client

            with patch("granite.scrapers.dgis.adaptive_delay"):
                results = scraper._scrape_api()

        assert len(results) == 1
        # Один запрос — одна страница
        assert mock_client.get.call_count == 1

    def test_scrape_api_sends_region_id(self, dgis_config):
        """region_id передаётся в параметры запроса."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"items": [], "total": 0}}

        scraper = DgisScraper(dgis_config, "Омск")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            scraper._scrape_api()

            call_args = mock_client.get.call_args
            assert call_args[1]["params"]["region_id"] == "103"

    def test_scrape_api_no_region_for_unknown_city(self, dgis_config):
        """Неизвестный город — region_id не передаётся."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"items": [], "total": 0}}

        scraper = DgisScraper(dgis_config, "НеизвестныйГород")
        with patch("granite.scrapers.dgis.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            scraper._scrape_api()

            call_args = mock_client.get.call_args
            assert "region_id" not in call_args[1]["params"]


# ============================================================
# DgisScraper: Crawlee fallback mode
# ============================================================

class TestDgisScrapeCrawlee:

    def test_scrape_crawlee_called(self, dgis_no_api_config):
        """Без API ключа — вызывается Crawlee fallback."""
        test_data = [
            RawCompany(
                source=Source.DGIS, name="Гранит М", city="Омск",
                phones=["79031234567"],
            )
        ]

        scraper = DgisScraper(dgis_no_api_config, "Омск")
        with patch("granite.scrapers.dgis.asyncio.run", return_value=test_data):
            results = scraper._scrape_crawlee()

        assert len(results) == 1
        assert results[0].name == "Гранит М"

    def test_scrape_crawlee_exception(self, dgis_no_api_config):
        """Crawlee вызвал исключение — пустой список."""
        scraper = DgisScraper(dgis_no_api_config, "Омск")
        with patch("granite.scrapers.dgis.asyncio.run", side_effect=Exception("browser error")):
            results = scraper._scrape_crawlee()

        assert results == []

    def test_scrape_selects_crawlee_when_no_key(self, dgis_no_api_config):
        """scrape() вызывает Crawlee mode когда нет ключа."""
        scraper = DgisScraper(dgis_no_api_config, "Омск")
        with patch.object(scraper, "_scrape_crawlee", return_value=[]) as mock_crawlee:
            with patch.object(scraper, "_scrape_api", return_value=[]) as mock_api:
                scraper.scrape()

        mock_crawlee.assert_called_once()
        mock_api.assert_not_called()

    def test_scrape_selects_api_when_has_key(self, dgis_config):
        """scrape() вызывает API mode когда есть ключ."""
        scraper = DgisScraper(dgis_config, "Омск")
        with patch.object(scraper, "_scrape_api", return_value=[]) as mock_api:
            with patch.object(scraper, "_scrape_crawlee", return_value=[]) as mock_crawlee:
                scraper.scrape()

        mock_api.assert_called_once()
        mock_crawlee.assert_not_called()

    def test_run_wraps_scrape(self, dgis_config):
        """BaseScraper.run() оборачивает scrape() с логированием."""
        scraper = DgisScraper(dgis_config, "Омск")
        with patch.object(scraper, "scrape", return_value=[
            RawCompany(source=Source.DGIS, name="Тест", city="Омск")
        ]):
            results = scraper.run()
        assert len(results) == 1

    def test_run_catches_exception(self, dgis_config):
        """BaseScraper.run() ловит исключения."""
        scraper = DgisScraper(dgis_config, "Омск")
        with patch.object(scraper, "scrape", side_effect=RuntimeError("fail")):
            results = scraper.run()
        assert results == []
        assert scraper.last_error == "fail"


# ============================================================
# YellScraper: Config & Init
# ============================================================

class TestYellScraperInit:

    def test_init_defaults(self, yell_config):
        scraper = YellScraper(yell_config, "Омск")
        assert scraper.city == "Омск"
        assert scraper.max_pages == 3
        assert scraper.base_path == "/catalog/izgotovlenie_pamyatnikov/"

    def test_init_with_categories(self, yell_with_categories_config):
        scraper = YellScraper(
            yell_with_categories_config, "Омск",
            categories=["/catalog/izgotovlenie-pamyatnikov"]
        )
        assert scraper.categories == ["/catalog/izgotovlenie-pamyatnikov"]

    def test_init_default_max_pages(self, yell_config):
        config = {
            "cities": [{"name": "Омск"}],
            "sources": {"yell": {}}
        }
        scraper = YellScraper(config, "Омск")
        assert scraper.max_pages == 5

    def test_init_playwright_page_ignored(self, yell_config):
        """Параметр playwright_page игнорируется."""
        mock_page = MagicMock()
        scraper = YellScraper(yell_config, "Омск", playwright_page=mock_page)
        assert not hasattr(scraper, "page")


# ============================================================
# YellScraper: URL generation
# ============================================================

class TestYellUrls:

    def test_urls_from_categories(self, yell_with_categories_config):
        """URL генерируется из категорий."""
        cats = ["/catalog/izgotovlenie-pamyatnikov", "/catalog/pamyatniki-iz-granita"]
        scraper = YellScraper(yell_with_categories_config, "Омск", categories=cats)
        urls = scraper._get_urls()

        assert len(urls) == 2
        assert "yell.ru/catalog/izgotovlenie-pamyatnikov" in urls[0]
        assert "yell.ru/catalog/pamyatniki-iz-granita" in urls[1]

    def test_urls_from_base_path_fallback(self, yell_config):
        """URL генерируется из base_path когда нет категорий."""
        scraper = YellScraper(yell_config, "Омск")
        urls = scraper._get_urls()

        assert len(urls) == 1
        assert "yell.ru/catalog/izgotovlenie_pamyatnikov/" in urls[0]

    def test_urls_base_path_with_city_slug(self, yell_config):
        """base_path с {city_slug} заменяется."""
        config = {
            "cities": [{"name": "Омск"}],
            "sources": {"yell": {"base_path": "/catalog/{city_slug}/pamyatniki/"}}
        }
        scraper = YellScraper(config, "Омск")
        urls = scraper._get_urls()

        assert "omsk" in urls[0]

    def test_urls_skip_non_slash_categories(self, yell_with_categories_config):
        """Категории не начинающиеся с / пропускаются."""
        scraper = YellScraper(
            yell_with_categories_config, "Омск",
            categories=["invalid", "/catalog/ok"]
        )
        urls = scraper._get_urls()

        assert len(urls) == 1
        assert "catalog/ok" in urls[0]

    def test_urls_empty_when_no_categories_no_base(self):
        """Нет категорий и нет base_path — пустой список."""
        config = {
            "cities": [{"name": "Омск"}],
            "sources": {"yell": {}}
        }
        scraper = YellScraper(config, "Омск")
        assert scraper._get_urls() == []


# ============================================================
# YellScraper: Crawlee mode
# ============================================================

class TestYellCrawlee:

    def test_scrape_success(self, yell_config):
        """Успешный парсинг через Crawlee."""
        test_data = [
            RawCompany(
                source=Source.YELL, name="Гранит М", city="Омск",
                phones=["79031234567"], website="https://granit.ru",
            )
        ]

        scraper = YellScraper(yell_config, "Омск")
        with patch("granite.scrapers.yell.asyncio.run", return_value=test_data):
            results = scraper.scrape()

        assert len(results) == 1
        assert results[0].source == Source.YELL
        assert results[0].name == "Гранит М"

    def test_scrape_multiple_categories(self, yell_with_categories_config):
        """Парсинг нескольких категорий."""
        test_data = [RawCompany(source=Source.YELL, name="Тест", city="Омск")]

        scraper = YellScraper(
            yell_with_categories_config, "Омск",
            categories=["/catalog/a", "/catalog/b"]
        )
        with patch("granite.scrapers.yell.asyncio.run", return_value=test_data):
            results = scraper.scrape()

        assert len(results) == 2  # 2 категории

    def test_scrape_no_urls(self):
        """Нет URL — пустой список, Crawlee не вызывается."""
        config = {
            "cities": [{"name": "Омск"}],
            "sources": {"yell": {}}
        }
        scraper = YellScraper(config, "Омск")
        results = scraper.scrape()
        assert results == []

    def test_scrape_exception_handled(self, yell_config):
        """Исключение Crawlee — логируется, парсинг продолжается."""
        scraper = YellScraper(yell_config, "Омск")
        with patch("granite.scrapers.yell.asyncio.run", side_effect=Exception("browser fail")):
            results = scraper.scrape()

        assert results == []

    def test_run_wraps_scrape(self, yell_config):
        """BaseScraper.run() оборачивает scrape()."""
        scraper = YellScraper(yell_config, "Омск")
        with patch.object(scraper, "scrape", return_value=[
            RawCompany(source=Source.YELL, name="Тест", city="Омск")
        ]):
            results = scraper.run()
        assert len(results) == 1

    def test_run_catches_exception(self, yell_config):
        """BaseScraper.run() ловит исключения."""
        scraper = YellScraper(yell_config, "Омск")
        with patch.object(scraper, "scrape", side_effect=RuntimeError("fail")):
            results = scraper.run()
        assert results == []
        assert scraper.last_error == "fail"


# ============================================================
# YellScraper: _extract_companies (unit)
# ============================================================

class TestYellExtractCompanies:

    def _make_mock_card(self, name, html=""):
        """Создать мок карточки Yell с корректными async-методами."""
        mock_card = MagicMock()

        mock_name = MagicMock()
        mock_name.inner_text = AsyncMock(return_value=name)
        mock_name.get_attribute = AsyncMock(return_value=None)

        # query_selector: name → mock_name, address/site/category → None
        mock_card.query_selector = AsyncMock(side_effect=lambda sel: (
            mock_name if any(k in sel for k in ["name", "title", "company"])
            else None
        ))

        # links for query_selector_all — все async-методы должны быть AsyncMock
        mock_link = MagicMock()
        mock_link.inner_text = AsyncMock(return_value="")
        mock_link.get_attribute = AsyncMock(return_value="")
        mock_card.query_selector_all = AsyncMock(return_value=[mock_link])

        mock_card.inner_html = AsyncMock(return_value=html)

        return mock_card

    @pytest.mark.asyncio
    async def test_extract_from_page(self):
        """Извлечение карточек из страницы."""
        scraper = YellScraper(
            {"cities": [{"name": "Омск"}], "sources": {"yell": {}}},
            "Омск",
        )

        mock_page = MagicMock()
        mock_card1 = self._make_mock_card("Гранит Мастер", "info@granit.ru")
        mock_card2 = self._make_mock_card("Ритма", "")

        mock_page.query_selector_all = AsyncMock(return_value=[mock_card1, mock_card2])

        seen = set()
        companies = await scraper._extract_companies(mock_page, seen)

        assert len(companies) == 2
        assert companies[0].name == "Гранит Мастер"
        assert companies[1].name == "Ритма"
        assert companies[0].source == Source.YELL

    @pytest.mark.asyncio
    async def test_extract_deduplicates_by_name(self):
        """Дедупликация по имени."""
        scraper = YellScraper(
            {"cities": [{"name": "Омск"}], "sources": {"yell": {}}},
            "Омск",
        )
        mock_page = MagicMock()

        mock_card = self._make_mock_card("Дубликат", "")

        mock_page.query_selector_all = AsyncMock(return_value=[mock_card, mock_card])

        seen = set()
        companies = await scraper._extract_companies(mock_page, seen)

        assert len(companies) == 1

    @pytest.mark.asyncio
    async def test_extract_skips_short_name(self):
        """Слишком короткое имя — пропуск."""
        scraper = YellScraper(
            {"cities": [{"name": "Омск"}], "sources": {"yell": {}}},
            "Омск",
        )
        mock_page = MagicMock()

        mock_card = MagicMock()
        mock_name = MagicMock()
        mock_name.inner_text = AsyncMock(return_value="АБ")
        mock_card.query_selector = AsyncMock(return_value=mock_name)

        mock_page.query_selector_all = AsyncMock(return_value=[mock_card])

        seen = set()
        companies = await scraper._extract_companies(mock_page, seen)

        assert len(companies) == 0

    @pytest.mark.asyncio
    async def test_extract_skips_no_name(self):
        """Нет элемента имени — пропуск."""
        scraper = YellScraper(
            {"cities": [{"name": "Омск"}], "sources": {"yell": {}}},
            "Омск",
        )
        mock_page = MagicMock()

        mock_card = MagicMock()
        mock_card.query_selector = AsyncMock(return_value=None)

        mock_page.query_selector_all = AsyncMock(return_value=[mock_card])

        seen = set()
        companies = await scraper._extract_companies(mock_page, seen)

        assert len(companies) == 0
