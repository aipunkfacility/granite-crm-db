# tests/test_reverse_lookup.py — Тесты ReverseLookupEnricher с моками
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from granite.enrichers.reverse_lookup import (
    ReverseLookupEnricher,
)
from granite.scrapers.dgis_constants import get_dgis_region_id, DGIS_REGION_IDS


# ===== Fixtures =====

@pytest.fixture
def rl_config():
    return {
        "enrichment": {
            "reverse_lookup": {
                "enabled": True,
                "sources": {
                    "dgis": {
                        "enabled": True,
                        "api_key": "test_key",
                        "max_requests_per_day": 100,
                    },
                    "yell": {
                        "enabled": True,
                        "max_requests_per_day": 50,
                    },
                },
                "min_crm_score": 30,
                "delay_between_requests": 0.01,  # minimal for tests
            }
        }
    }


@pytest.fixture
def mock_db():
    """Мок Database с session_scope."""
    db = MagicMock()
    return db


@pytest.fixture
def enricher(rl_config, mock_db):
    return ReverseLookupEnricher(rl_config, mock_db)


def _make_enriched_row(**kwargs):
    """Создать мок EnrichedCompanyRow.

    Note: MagicMock(name=...) sets the mock's internal name, so we set 'name'
    via object.__setattr__ to avoid it being interpreted specially.
    """
    defaults = {
        "id": 1,
        "name": "Гранит Мастер",
        "phones": ["79031234567"],
        "address_raw": "",
        "website": None,
        "emails": [],
        "city": "Омск",
        "messengers": {},
        "crm_score": 10,
        "segment": "D",
    }
    defaults.update(kwargs)
    # Extract 'name' before creating MagicMock to avoid special handling
    mock_name = defaults.pop('name', 'Гранит Мастер')
    row = MagicMock(**defaults)
    object.__setattr__(row, 'name', mock_name)
    return row


# ===== 2GIS Region ID =====

class TestDgisRegionId:

    def test_moscow(self):
        assert get_dgis_region_id("Москва") == "32"

    def test_saint_petersburg(self):
        assert get_dgis_region_id("Санкт-Петербург") == "49"

    def test_novosibirsk(self):
        assert get_dgis_region_id("Новосибирск") == "131"

    def test_case_insensitive(self):
        assert get_dgis_region_id("москва") == "32"
        assert get_dgis_region_id("МОСКВА") == "32"

    def test_unknown_city(self):
        assert get_dgis_region_id("НеизвестныйГород") == ""

    def test_empty_city(self):
        assert get_dgis_region_id("") == ""

    def test_small_city(self):
        """Малые города Омской области fallback на region_id Омской области."""
        assert get_dgis_region_id("Тара") == "131"

    def test_kazan(self):
        assert get_dgis_region_id("Казань") == "72"


# ===== Candidate Selection =====

class TestCandidateSelection:

    def test_filters_no_messengers_no_emails_low_score(self, enricher, mock_db):
        """Кандидат: нет мессенджеров, нет email, низкий score."""
        row1 = _make_enriched_row(
            id=1, name="Гранит Мастер", messengers={}, emails=[], crm_score=10,
        )
        row2 = _make_enriched_row(
            id=2, name="Ритма", messengers={"telegram": "t.me/ritma"}, emails=[], crm_score=50,
        )
        row3 = _make_enriched_row(
            id=3, name="Памятник", messengers={}, emails=["info@pam.ru"], crm_score=5,
        )

        # session_scope context manager
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value = session
        session.query.return_value.filter_by.return_value.all.return_value = [row1, row2, row3]

        candidates = enricher._get_candidates("Омск")
        assert len(candidates) == 1
        assert candidates[0].id == 1

    def test_filters_high_crm_score(self, enricher, mock_db):
        """Компания с высоким crm_score не кандидат."""
        row = _make_enriched_row(messengers={}, emails=[], crm_score=50)

        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value = session
        session.query.return_value.filter_by.return_value.all.return_value = [row]

        candidates = enricher._get_candidates("Омск")
        assert len(candidates) == 0

    def test_filters_with_messengers(self, enricher, mock_db):
        """Компания с мессенджерами не кандидат."""
        row = _make_enriched_row(messengers={"telegram": "t.me/x"}, emails=[], crm_score=5)

        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value = session
        session.query.return_value.filter_by.return_value.all.return_value = [row]

        candidates = enricher._get_candidates("Омск")
        assert len(candidates) == 0

    def test_filters_with_emails(self, enricher, mock_db):
        """Компания с email не кандидат."""
        row = _make_enriched_row(messengers={}, emails=["a@b.ru"], crm_score=5)

        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value = session
        session.query.return_value.filter_by.return_value.all.return_value = [row]

        candidates = enricher._get_candidates("Омск")
        assert len(candidates) == 0

    def test_all_valid_candidates(self, enricher, mock_db):
        """Все компании — кандидаты."""
        rows = [
            _make_enriched_row(id=i, messengers={}, emails=[], crm_score=i)
            for i in range(1, 4)
        ]

        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value = session
        session.query.return_value.filter_by.return_value.all.return_value = rows

        candidates = enricher._get_candidates("Омск")
        assert len(candidates) == 3

    def test_empty_enriched_table(self, enricher, mock_db):
        """Пустая таблица — нет кандидатов."""
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value = session
        session.query.return_value.filter_by.return_value.all.return_value = []

        candidates = enricher._get_candidates("Омск")
        assert len(candidates) == 0

    def test_null_messengers_treated_as_empty(self, enricher, mock_db):
        """None messengers = пустые мессенджеры (кандидат)."""
        row = _make_enriched_row(messengers=None, emails=[], crm_score=5)

        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value = session
        session.query.return_value.filter_by.return_value.all.return_value = [row]

        candidates = enricher._get_candidates("Омск")
        assert len(candidates) == 1


# ===== 2GIS API Response Parsing =====

class TestDgisApiParsing:

    def test_parse_dgis_api_item_with_contacts(self):
        """Парсинг элемента 2GIS API с контактами."""
        item = {
            "address_name": "г. Омск, ул. Ленина, 10",
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
        result = enricher_instance = ReverseLookupEnricher.__new__(ReverseLookupEnricher)
        # Use method directly
        result = ReverseLookupEnricher._parse_dgis_api_item(None, item)
        assert result["phones"] == ["79031234567"]
        assert result["email"] == "info@granit.ru"
        assert result["website"] == "https://granit.ru"
        assert result["address"] == "г. Омск, ул. Ленина, 10"
        assert "telegram" in result["messengers"]

    def test_parse_dgis_api_item_minimal(self):
        """Парсинг элемента 2GIS без контактов."""
        item = {
            "address_name": "г. Москва, ул. Тверская, 5",
            "contact_groups": [],
        }
        result = ReverseLookupEnricher._parse_dgis_api_item(None, item)
        assert result["phones"] == []
        assert result["email"] is None
        assert result["website"] is None
        assert result["address"] == "г. Москва, ул. Тверская, 5"
        assert result["messengers"] == {}

    def test_parse_dgis_api_item_website_without_protocol(self):
        """Сайт без http:// — добавляется."""
        item = {
            "contact_groups": [
                {"contacts": [{"type": "website", "value": "mysite.ru"}]}
            ]
        }
        result = ReverseLookupEnricher._parse_dgis_api_item(None, item)
        assert result["website"] == "https://mysite.ru"

    def test_parse_dgis_api_item_whatsapp(self):
        """WhatsApp в контактах."""
        item = {
            "contact_groups": [
                {"contacts": [{"type": "whatsapp", "value": "https://wa.me/79031234567"}]}
            ]
        }
        result = ReverseLookupEnricher._parse_dgis_api_item(None, item)
        assert "whatsapp" in result["messengers"]
        assert result["messengers"]["whatsapp"] == "https://wa.me/79031234567"

    def test_parse_dgis_api_item_multiple_phones(self):
        """Несколько телефонов."""
        item = {
            "contact_groups": [
                {
                    "contacts": [
                        {"type": "phone", "value": "+7 (903) 123-45-67"},
                        {"type": "phone", "value": "8 903 222-33-44"},
                    ]
                }
            ]
        }
        result = ReverseLookupEnricher._parse_dgis_api_item(None, item)
        assert len(result["phones"]) == 2
        assert "79031234567" in result["phones"]
        assert "79032223344" in result["phones"]


# ===== Data Merging =====

class TestDataMerging:

    def test_merge_adds_phones(self, enricher):
        """Добавляет новые телефоны (union)."""
        company = _make_enriched_row(phones=["79031234567"])
        new_data = {"phones": ["79032223344"], "website": None, "email": None,
                     "address": "", "messengers": {}}
        updated = enricher._merge_data(company, new_data)
        assert "phones" in updated
        assert "79032223344" in company.phones

    def test_merge_does_not_overwrite_phones(self, enricher):
        """Не дублирует существующие телефоны."""
        company = _make_enriched_row(phones=["79031234567"])
        new_data = {"phones": ["79031234567", "79032223344"], "website": None,
                     "email": None, "address": "", "messengers": {}}
        updated = enricher._merge_data(company, new_data)
        assert len(company.phones) == 2

    def test_merge_adds_website(self, enricher):
        """Добавляет сайт если отсутствует."""
        company = _make_enriched_row(website=None)
        new_data = {"phones": [], "website": "https://granit.ru", "email": None,
                     "address": "", "messengers": {}}
        updated = enricher._merge_data(company, new_data)
        assert "website" in updated
        assert company.website == "https://granit.ru"

    def test_merge_does_not_overwrite_website(self, enricher):
        """Не перезаписывает существующий сайт."""
        company = _make_enriched_row(website="https://existing.ru")
        new_data = {"phones": [], "website": "https://new.ru", "email": None,
                     "address": "", "messengers": {}}
        updated = enricher._merge_data(company, new_data)
        assert "website" not in updated
        assert company.website == "https://existing.ru"

    def test_merge_adds_email(self, enricher):
        """Добавляет email."""
        company = _make_enriched_row(emails=[])
        new_data = {"phones": [], "website": None, "email": "info@granit.ru",
                     "address": "", "messengers": {}}
        updated = enricher._merge_data(company, new_data)
        assert "emails" in updated
        assert "info@granit.ru" in company.emails

    def test_merge_adds_messengers(self, enricher):
        """Добавляет мессенджеры (union, без перезаписи)."""
        company = _make_enriched_row(messengers={"vk": "https://vk.com/granit"})
        new_data = {"phones": [], "website": None, "email": None,
                     "address": "", "messengers": {"telegram": "https://t.me/granit"}}
        updated = enricher._merge_data(company, new_data)
        assert "telegram" in updated
        assert company.messengers["telegram"] == "https://t.me/granit"
        assert company.messengers["vk"] == "https://vk.com/granit"

    def test_merge_does_not_overwrite_messengers(self, enricher):
        """Не перезаписывает существующий мессенджер."""
        company = _make_enriched_row(messengers={"telegram": "https://t.me/old"})
        new_data = {"phones": [], "website": None, "email": None,
                     "address": "", "messengers": {"telegram": "https://t.me/new"}}
        updated = enricher._merge_data(company, new_data)
        assert "telegram" not in updated
        assert company.messengers["telegram"] == "https://t.me/old"

    def test_merge_adds_address(self, enricher):
        """Добавляет адрес если отсутствует."""
        company = _make_enriched_row(address_raw="")
        new_data = {"phones": [], "website": None, "email": None,
                     "address": "г. Омск, ул. Ленина, 10", "messengers": {}}
        updated = enricher._merge_data(company, new_data)
        assert "address" in updated
        assert company.address_raw == "г. Омск, ул. Ленина, 10"

    def test_merge_empty_data(self, enricher):
        """Пустые данные — ничего не обновляется."""
        company = _make_enriched_row()
        new_data = {"phones": [], "website": None, "email": None,
                     "address": "", "messengers": {}}
        updated = enricher._merge_data(company, new_data)
        assert updated == []

    def test_merge_all_fields(self, enricher):
        """Все новые поля — обновляются."""
        company = _make_enriched_row(phones=[], website=None, emails=[],
                                      messengers={}, address_raw="")
        new_data = {
            "phones": ["79031234567"],
            "website": "https://granit.ru",
            "email": "info@granit.ru",
            "address": "г. Омск, ул. Ленина, 10",
            "messengers": {"telegram": "https://t.me/granit", "vk": "https://vk.com/granit"},
        }
        updated = enricher._merge_data(company, new_data)
        assert "phones" in updated
        assert "website" in updated
        assert "emails" in updated
        assert "address" in updated
        assert "telegram" in updated
        assert "vk" in updated


# ===== 2GIS API Query =====

class TestDgisApiQuery:

    def test_query_dgis_api_success(self, enricher):
        """Успешный запрос к 2GIS API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "items": [
                    {
                        "address_name": "г. Москва, ул. Тверская, 5",
                        "contact_groups": [
                            {
                                "contacts": [
                                    {"type": "phone", "value": "+7 (903) 123-45-67"},
                                    {"type": "website", "value": "granit.ru"},
                                ]
                            }
                        ]
                    }
                ]
            }
        }

        with patch("granite.enrichers.reverse_lookup.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = enricher._query_dgis_api("Гранит Мастер Москва", "Москва")

        assert result is not None
        assert result["phones"] == ["79031234567"]
        assert result["website"] == "https://granit.ru"

    def test_query_dgis_api_empty_results(self, enricher):
        """2GIS API вернул пустые результаты."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"items": []}}

        with patch("granite.enrichers.reverse_lookup.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = enricher._query_dgis_api("Несуществующая", "Москва")

        assert result is None

    def test_query_dgis_api_error_status(self, enricher):
        """2GIS API вернул ошибку."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("granite.enrichers.reverse_lookup.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = enricher._query_dgis_api("Test", "Москва")

        assert result is None

    def test_query_dgis_api_exception(self, enricher):
        """2GIS API вызвал исключение."""
        with patch("granite.enrichers.reverse_lookup.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(side_effect=Exception("network error"))
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = enricher._query_dgis_api("Test", "Москва")

        assert result is None

    def test_query_dgis_api_with_region_id(self, enricher):
        """Проверка что region_id передаётся в запрос."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"items": []}}

        with patch("granite.enrichers.reverse_lookup.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            enricher._query_dgis_api("Test", "Москва")

            call_args = mock_client.get.call_args
            assert call_args[1]["params"]["region_id"] == "32"


# ===== 2GIS Crawlee Fallback =====

class TestDgisCrawlee:

    def test_query_dgis_crawlee_success(self, enricher):
        """Успешный Crawlee fallback."""
        async def mock_run(urls):
            pass

        mock_crawler = MagicMock()
        mock_crawler.run = mock_run

        # We mock the async function to return test data
        test_data = {
            "phones": ["79031234567"],
            "website": None,
            "email": "test@test.ru",
            "address": "г. Омск, ул. Ленина, 10",
            "messengers": {"telegram": "https://t.me/test"},
        }

        with patch("granite.enrichers.reverse_lookup._run_async", return_value=test_data):
            result = enricher._query_dgis_crawlee("Гранит Мастер", "Омск")

        assert result is not None
        assert result["phones"] == ["79031234567"]
        assert result["email"] == "test@test.ru"

    def test_query_dgis_crawlee_exception(self, enricher):
        """Crawlee вызвал исключение."""
        with patch("granite.enrichers.reverse_lookup.asyncio.run", side_effect=Exception("browser error")):
            result = enricher._query_dgis_crawlee("Test", "Омск")

        assert result is None


# ===== Yell Crawlee =====

class TestYellCrawlee:

    def test_query_yell_crawlee_success(self, enricher):
        """Успешный Yell Crawlee."""
        test_data = {
            "phones": ["79031234567"],
            "website": "https://granit.ru",
            "email": "info@granit.ru",
            "address": "",
            "messengers": {"vk": "https://vk.com/granit"},
        }

        with patch("granite.enrichers.reverse_lookup._run_async", return_value=test_data):
            result = enricher._query_yell_crawlee("Гранит Мастер Омск")

        assert result is not None
        assert result["website"] == "https://granit.ru"
        assert "vk" in result["messengers"]

    def test_query_yell_crawlee_exception(self, enricher):
        """Yell Crawlee вызвал исключение."""
        with patch("granite.enrichers.reverse_lookup.asyncio.run", side_effect=Exception("browser error")):
            result = enricher._query_yell_crawlee("Test")

        assert result is None


# ===== Rate Limiting =====

class TestRateLimiting:

    def test_delay_between_requests(self, enricher):
        """Проверка что задержка между запросами вызывается."""
        with patch("granite.enrichers.reverse_lookup.time.sleep") as mock_sleep:
            enricher._apply_delay()
            mock_sleep.assert_called_once()

    def test_delay_minimum(self, enricher):
        """Задержка не меньше 0.5 сек (jitter ≤30% of base delay)."""
        with patch("granite.enrichers.reverse_lookup.time.sleep") as mock_sleep:
            enricher._delay = 2.0
            enricher._apply_delay()
            call_args = mock_sleep.call_args[0][0]
            assert call_args >= 0.5  # floor

    def test_max_requests_dgis(self, enricher):
        """Не превышает max_requests_per_day для 2GIS."""
        enricher._dgis_max_per_day = 2

        with patch.object(enricher, "_query_dgis_api", return_value=None):
            with patch.object(enricher, "_query_dgis_crawlee", return_value=None):
                with patch.object(enricher, "_query_yell_crawlee", return_value=None):
                    with patch.object(enricher, "_save_updates"):
                        with patch.object(enricher, "_apply_delay"):
                            company = _make_enriched_row(id=1)
                            # 3-й запрос превысит лимит
                            for i in range(3):
                                enricher._enrich_one(company)

                            # Only first 2 calls should reach DGIS
                            # First: API call, Second: Crawlee fallback
                            # Third: skip DGIS (max_per_day=2)
                            assert enricher._dgis_requests_today == 2

    def test_max_requests_yell(self, enricher):
        """Не превышает max_requests_per_day для Yell."""
        enricher._yell_max_per_day = 1

        with patch.object(enricher, "_query_dgis_api", return_value=None):
            with patch.object(enricher, "_query_dgis_crawlee", return_value=None):
                with patch.object(enricher, "_query_yell_crawlee", return_value=None):
                    with patch.object(enricher, "_save_updates"):
                        with patch.object(enricher, "_apply_delay"):
                            company = _make_enriched_row(id=1)
                            # 2-й запрос превысит лимит Yell
                            for i in range(2):
                                enricher._enrich_one(company)

                            assert enricher._yell_requests_today == 1


# ===== Disabled Config =====

class TestDisabledConfig:

    def test_disabled_returns_zero(self, mock_db):
        """Выключенный reverse lookup возвращает 0."""
        config = {"enrichment": {"reverse_lookup": {"enabled": False}}}
        enricher = ReverseLookupEnricher(config, mock_db)
        result = enricher.run("Омск")
        assert result == 0

    def test_disabled_property(self, mock_db):
        """enabled property возвращает False."""
        config = {"enrichment": {"reverse_lookup": {"enabled": False}}}
        enricher = ReverseLookupEnricher(config, mock_db)
        assert enricher.enabled is False

    def test_enabled_property(self, rl_config, mock_db):
        """enabled property возвращает True."""
        enricher = ReverseLookupEnricher(rl_config, mock_db)
        assert enricher.enabled is True


# ===== API Key from Environment =====

class TestApiKeyFromEnv:

    def test_api_key_from_env(self, mock_db):
        """API ключ загружается из переменной окружения."""
        config = {
            "enrichment": {
                "reverse_lookup": {
                    "enabled": True,
                    "sources": {
                        "dgis": {"api_key": "", "enabled": True}
                    }
                }
            }
        }
        with patch.dict("os.environ", {"DGIS_API_KEY": "env_test_key"}):
            enricher = ReverseLookupEnricher(config, mock_db)
        assert enricher._dgis_api_key == "env_test_key"

    def test_api_key_from_config_takes_priority(self, mock_db):
        """API ключ из конфига приоритетнее чем из env."""
        config = {
            "enrichment": {
                "reverse_lookup": {
                    "enabled": True,
                    "sources": {
                        "dgis": {"api_key": "config_key", "enabled": True}
                    }
                }
            }
        }
        with patch.dict("os.environ", {"DGIS_API_KEY": "env_test_key"}):
            enricher = ReverseLookupEnricher(config, mock_db)
        assert enricher._dgis_api_key == "config_key"


# ===== Run Method =====

class TestRunMethod:

    def test_run_with_no_candidates(self, enricher, mock_db):
        """Нет кандидатов — возвращает 0."""
        with patch.object(enricher, "_get_candidates", return_value=[]):
            result = enricher.run("Омск")
        assert result == 0

    def test_run_with_candidates(self, enricher, mock_db):
        """Есть кандидаты — обогащает."""
        row = _make_enriched_row(id=1)
        dgis_data = {
            "phones": ["79032223344"],
            "website": "https://granit.ru",
            "email": "info@granit.ru",
            "address": "г. Омск, ул. Ленина, 10",
            "messengers": {"telegram": "https://t.me/granit"},
        }

        with patch.object(enricher, "_get_candidates", return_value=[row]):
            with patch.object(enricher, "_enrich_one", return_value=["phones", "website", "emails", "telegram"]):
                with patch.object(enricher, "_apply_delay"):
                    result = enricher.run("Омск")
        assert result == 1

    def test_run_handles_exception_gracefully(self, enricher, mock_db):
        """Исключение при обогащении не прерывает цикл."""
        row1 = _make_enriched_row(id=1)
        row2 = _make_enriched_row(id=2)

        with patch.object(enricher, "_get_candidates", return_value=[row1, row2]):
            with patch.object(enricher, "_enrich_one", side_effect=[Exception("boom"), ["phones"]]):
                with patch.object(enricher, "_apply_delay"):
                    result = enricher.run("Омск")
        assert result == 1

    def test_run_no_delay_after_last_candidate(self, enricher, mock_db):
        """Задержка не вызывается после последнего кандидата."""
        row = _make_enriched_row(id=1)

        with patch.object(enricher, "_get_candidates", return_value=[row]):
            with patch.object(enricher, "_enrich_one", return_value=[]):
                with patch.object(enricher, "_apply_delay") as mock_delay:
                    result = enricher.run("Омск")
        mock_delay.assert_not_called()


# ===== Full Enrichment =====

class TestEnrichOne:

    def test_enrich_one_dgis_api_only(self, enricher):
        """2GIS API находит данные, Yell не вызывается."""
        company = _make_enriched_row(id=1, phones=[], website=None, emails=[],
                                      messengers={}, crm_score=5)
        dgis_data = {
            "phones": ["79031234567"],
            "website": "https://granit.ru",
            "email": "info@granit.ru",
            "address": "г. Омск, ул. Ленина, 10",
            "messengers": {"telegram": "https://t.me/granit"},
        }

        with patch.object(enricher, "_query_dgis_api", return_value=dgis_data):
            with patch.object(enricher, "_query_dgis_crawlee") as mock_crawlee:
                with patch.object(enricher, "_query_yell_crawlee") as mock_yell:
                    with patch.object(enricher, "_save_updates"):
                        updated = enricher._enrich_one(company)

        # Yell still called (different counter)
        mock_yell.assert_called_once()
        assert "phones" in updated
        assert "website" in updated
        assert "emails" in updated
        assert "telegram" in updated

    def test_enrich_one_dgis_fallback_to_crawlee(self, enricher):
        """2GIS API не дал данных → fallback на Crawlee."""
        # Use empty phones so crawlee's phone is genuinely new
        company = _make_enriched_row(id=1, phones=[])
        crawlee_data = {
            "phones": ["79032223344"],
            "website": "https://crawlee-site.ru",
            "email": None,
            "address": "г. Омск, ул. Ленина",
            "messengers": {},
        }

        with patch.object(enricher, "_query_dgis_api", return_value=None):
            with patch.object(enricher, "_query_dgis_crawlee", return_value=crawlee_data):
                with patch.object(enricher, "_query_yell_crawlee", return_value=None):
                    with patch.object(enricher, "_save_updates"):
                        updated = enricher._enrich_one(company)

        assert "phones" in updated
        assert "address" in updated
        assert "website" in updated

    def test_enrich_one_yell_adds_data(self, enricher):
        """Yell находит данные когда 2GIS не дал."""
        company = _make_enriched_row(id=1)
        yell_data = {
            "phones": ["79031234567"],
            "website": "https://yell-site.ru",
            "email": "info@yell-site.ru",
            "address": "",
            "messengers": {"vk": "https://vk.com/granit"},
        }

        with patch.object(enricher, "_query_dgis_api", return_value=None):
            with patch.object(enricher, "_query_dgis_crawlee", return_value=None):
                with patch.object(enricher, "_query_yell_crawlee", return_value=yell_data):
                    with patch.object(enricher, "_save_updates"):
                        updated = enricher._enrich_one(company)

        assert "website" in updated
        assert "emails" in updated
        assert "vk" in updated

    def test_enrich_one_no_results(self, enricher):
        """Ни один источник не дал результатов."""
        company = _make_enriched_row(id=1)

        with patch.object(enricher, "_query_dgis_api", return_value=None):
            with patch.object(enricher, "_query_dgis_crawlee", return_value=None):
                with patch.object(enricher, "_query_yell_crawlee", return_value=None):
                    with patch.object(enricher, "_save_updates") as mock_save:
                        updated = enricher._enrich_one(company)

        assert updated == []
        mock_save.assert_not_called()

    def test_enrich_one_uses_phone_query_for_yell(self, enricher):
        """Yell использует номер телефона для поиска если доступен."""
        company = _make_enriched_row(id=1, phones=["79031234567"])

        with patch.object(enricher, "_query_dgis_api", return_value=None):
            with patch.object(enricher, "_query_dgis_crawlee", return_value=None):
                with patch.object(enricher, "_query_yell_crawlee") as mock_yell:
                    with patch.object(enricher, "_save_updates"):
                        enricher._enrich_one(company)

                # Yell called with phone number as query
                mock_yell.assert_called_once_with("79031234567")

    def test_enrich_one_uses_name_for_yell_no_phone(self, enricher):
        """Yell использует имя+город если нет телефона."""
        company = _make_enriched_row(id=1, phones=[])

        with patch.object(enricher, "_query_dgis_api", return_value=None):
            with patch.object(enricher, "_query_dgis_crawlee", return_value=None):
                with patch.object(enricher, "_query_yell_crawlee") as mock_yell:
                    with patch.object(enricher, "_save_updates"):
                        enricher._enrich_one(company)

                mock_yell.assert_called_once_with("Гранит Мастер Омск")


# ===== Save Updates =====

class TestSaveUpdates:

    def test_save_updates_writes_to_db(self, enricher, mock_db):
        """Обновления записываются в БД."""
        company = _make_enriched_row(
            id=1, phones=["79031234567"], website="https://granit.ru",
            emails=["info@granit.ru"], messengers={"telegram": "https://t.me/granit"},
        )
        updated_fields = ["phones", "website", "emails", "telegram"]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value = mock_session

        mock_erow = MagicMock()
        mock_session.get.return_value = mock_erow

        enricher._save_updates(company, updated_fields)

        assert mock_erow.phones == company.phones
        assert mock_erow.website == company.website
        assert mock_erow.emails == company.emails

    def test_save_updates_handles_db_error(self, enricher, mock_db):
        """Ошибка БД не выбрасывается наверх."""
        company = _make_enriched_row(id=1)
        mock_db.session_scope.side_effect = Exception("DB locked")

        # Should not raise
        enricher._save_updates(company, ["phones"])


# ===== DGIS Region IDs Completeness =====

class TestDgisRegionIds:

    def test_has_major_cities(self):
        """Словарь содержит основные города."""
        for city in ["москва", "санкт-петербург", "новосибирск", "екатеринбург",
                      "казань", "красноярск", "челябинск", "уфа"]:
            assert city in DGIS_REGION_IDS, f"Missing city: {city}"

    def test_values_are_integers(self):
        """Все region_id — целые числа."""
        for city, rid in DGIS_REGION_IDS.items():
            assert isinstance(rid, int), f"Invalid region_id for {city}: {rid}"
