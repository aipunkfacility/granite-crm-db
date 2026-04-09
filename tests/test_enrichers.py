# tests/test_enrichers.py — Тесты обогатителей с моками HTTP-запросов
import pytest
from unittest.mock import patch, MagicMock
from granite.enrichers.classifier import Classifier
from granite.enrichers.tech_extractor import TechExtractor
from granite.enrichers.tg_finder import find_tg_by_phone, find_tg_by_name, generate_usernames
from granite.enrichers.tg_trust import check_tg_trust
from granite.enrichers.messenger_scanner import MessengerScanner


# ===== Classifier =====

class TestClassifierExtended:
    """Расширенные тесты скоринга и сегментации."""

    @pytest.fixture
    def classifier(self):
        config = {
            "scoring": {
                "weights": {
                    "has_website": 10,
                    "has_telegram": 15,
                    "has_whatsapp": 10,
                    "multiple_phones": 5,
                    "has_email": 5,
                    "cms_bitrix": 15,
                    "cms_modern": 10,
                    "has_marquiz": 5,
                    "tg_trust_multiplier": 5,
                    "is_network": 15
                },
                "levels": {
                    "segment_A": 60,
                    "segment_B": 40,
                    "segment_C": 20
                }
            }
        }
        return Classifier(config)

    def test_website_only_gives_website_score(self, classifier):
        company = {"website": "http://site.ru", "cms": "unknown"}
        assert classifier.calculate_score(company) == 10

    def test_wordpress_gets_modern_cms_bonus(self, classifier):
        company = {"website": "http://site.ru", "cms": "wordpress"}
        assert classifier.calculate_score(company) == 20  # 10 web + 10 modern

    def test_tilda_gets_modern_cms_bonus(self, classifier):
        company = {"website": "http://site.ru", "cms": "tilda"}
        assert classifier.calculate_score(company) == 20

    def test_bitrix_gets_bitrix_bonus(self, classifier):
        company = {"website": "http://site.ru", "cms": "bitrix"}
        assert classifier.calculate_score(company) == 25  # 10 web + 15 bitrix

    def test_tg_trust_multiplier(self, classifier):
        company = {
            "website": "http://site.ru",
            "messengers": {"telegram": "t.me/x"},
            "tg_trust": {"trust_score": 2}  # 2 * 5 = 10
        }
        assert classifier.calculate_score(company) == 35  # 10 web + 15 tg + 10 trust

    def test_negative_tg_trust(self, classifier):
        company = {
            "messengers": {"telegram": "t.me/x"},
            "tg_trust": {"trust_score": -2}  # -2 * 5 = -10
        }
        assert classifier.calculate_score(company) == 5  # 15 tg - 10 trust

    def test_single_phone_no_bonus(self, classifier):
        company = {"phones": ["79031234567"]}
        assert classifier.calculate_score(company) == 0

    def test_empty_messengers(self, classifier):
        company = {"messengers": {}}
        assert classifier.calculate_score(company) == 0

    def test_segment_boundaries(self, classifier):
        # Точная граница A
        assert classifier.determine_segment(60) == "A"
        assert classifier.determine_segment(59) == "B"
        # Точная граница B
        assert classifier.determine_segment(40) == "B"
        assert classifier.determine_segment(39) == "C"
        # Точная граница C
        assert classifier.determine_segment(20) == "C"
        assert classifier.determine_segment(19) == "D"

    def test_all_fields_maximal(self, classifier):
        """Максимальный скор: все поля заполнены."""
        company = {
            "website": "http://bitrix.ru",
            "cms": "bitrix",
            "has_marquiz": True,
            "messengers": {"telegram": "t.me/x", "whatsapp": "wa.me/7903"},
            "tg_trust": {"trust_score": 3},
            "phones": ["79031234567", "79032222222"],
            "emails": ["a@b.ru"],
            "is_network": True,
        }
        # 10+15+5+15+15+10+5+5+15 = 95
        assert classifier.calculate_score(company) == 95
        assert classifier.determine_segment(95) == "A"


# ===== TG Finder =====

class TestTgFinder:

    @pytest.fixture
    def tg_config(self):
        return {"enrichment": {"tg_finder": {"check_delay": 0.01}}}

    def test_find_tg_by_phone_with_contact(self, tg_config):
        """Находит TG, если страница содержит 'Telegram: Contact'."""
        mock_response = MagicMock()
        mock_response.text = '<html><title>Telegram: Contact</title></html>'
        mock_response.status_code = 200

        with patch("granite.enrichers.tg_finder.tg_request", return_value=mock_response):
            result = find_tg_by_phone("79031234567", tg_config)
        assert result == "https://t.me/+79031234567"

    def test_find_tg_by_phone_no_contact(self, tg_config):
        """Не находит TG, если нет кнопки Send Message."""
        mock_response = MagicMock()
        mock_response.text = "<html><title>Telegram</title></html>"
        mock_response.status_code = 200

        with patch("granite.enrichers.tg_finder.tg_request", return_value=mock_response):
            result = find_tg_by_phone("79031234567", tg_config)
        assert result is None

    def test_find_tg_by_phone_short_number(self, tg_config):
        """Слишком короткий номер — без запроса."""
        result = find_tg_by_phone("123", tg_config)
        assert result is None

    def test_find_tg_by_phone_empty(self, tg_config):
        result = find_tg_by_phone("", tg_config)
        assert result is None

    def test_find_tg_by_name_with_keywords(self, tg_config):
        """Находит юзернейм с ритуальными ключевыми словами."""
        mock_response = MagicMock()
        mock_response.text = (
            '<div class="tgme_page_title">Памятники Гранит</div>'
            '<div class="tgme_page_description">Ритуальные услуги и памятники</div>'
        )
        mock_response.status_code = 200

        with patch("granite.enrichers.tg_finder.tg_request", return_value=mock_response):
            result = find_tg_by_name("Памятники Гранит", "79031234567", tg_config)
        assert result is not None
        assert "t.me/" in result

    def test_find_tg_by_name_no_keywords(self, tg_config):
        """Не находит юзернейм без ритуальных ключевых слов."""
        mock_response = MagicMock()
        mock_response.text = (
            '<div class="tgme_page_title">Some User</div>'
            '<div class="tgme_page_description">Just a person</div>'
        )
        mock_response.status_code = 200

        with patch("granite.enrichers.tg_finder.tg_request", return_value=mock_response):
            result = find_tg_by_name("Random Name", None, tg_config)
        assert result is None

    def test_generate_usernames_basic(self):
        result = generate_usernames("Гранит Мастер")
        assert len(result) >= 1
        assert all(len(v) >= 5 for v in result)

    def test_generate_usernames_empty(self):
        result = generate_usernames("")
        assert result == []

    def test_generate_usernames_none(self):
        """None name returns empty list (no crash)."""
        result = generate_usernames(None)
        assert result == []

    def test_find_tg_by_name_none(self, tg_config):
        """None name returns None without crash."""
        with patch("granite.enrichers.tg_finder.tg_request") as mock_req:
            result = find_tg_by_name(None, "79031234567", tg_config)
        assert result is None
        mock_req.assert_not_called()

    def test_generate_usernames_with_phone(self):
        result = generate_usernames("Гранит Мастер", "79031234567")
        assert any("4567" in v for v in result)


# ===== TG Trust =====

class TestTgTrust:

    def test_check_tg_trust_full_profile(self):
        """Полный профиль: аватар, описание, не бот."""
        mock_response = MagicMock()
        mock_response.text = (
            '<img class="tgme_page_photo_image" src="avatar.jpg">'
            '<div class="tgme_page_description">Описание профиля</div>'
            '<div class="tgme_page_extra">Подписчики</div>'
        )
        mock_response.status_code = 200

        with patch("granite.enrichers.tg_trust.tg_request", return_value=mock_response):
            result = check_tg_trust("https://t.me/granit_master")
        assert result["has_avatar"] is True
        assert result["has_description"] is True
        # trust_score = 2 (1 avatar + 1 desc). No channel penalty because
        # the mock uses Russian "Подписчики" but the code checks for
        # English "subscribers"/"members".
        assert result["trust_score"] == 2

    def test_check_tg_trust_bot(self):
        """Профиль бота: штраф к скору."""
        mock_response = MagicMock()
        mock_response.text = '<div class="tgme_page_bot_button">Start</div>'
        mock_response.status_code = 200

        with patch("granite.enrichers.tg_trust.tg_request", return_value=mock_response):
            result = check_tg_trust("https://t.me/granit_bot")
        assert result["is_bot"] is True
        assert result["trust_score"] < 0

    def test_check_tg_trust_empty(self):
        """Пустой профиль без данных."""
        mock_response = MagicMock()
        mock_response.text = "<html><title>Telegram</title></html>"
        mock_response.status_code = 200

        with patch("granite.enrichers.tg_trust.tg_request", return_value=mock_response):
            result = check_tg_trust("https://t.me/empty")
        assert result["trust_score"] == 0
        assert result["has_avatar"] is False

    def test_check_tg_trust_none_url(self):
        result = check_tg_trust(None)
        assert result["trust_score"] == 0
        assert result["has_avatar"] is False
        assert result["has_description"] is False
        assert result["is_bot"] is False
        assert result["is_channel"] is False

    def test_check_tg_trust_request_failure(self):
        """HTTP-запрос не удался."""
        with patch("granite.enrichers.tg_trust.tg_request", return_value=None):
            result = check_tg_trust("https://t.me/notfound")
        assert result["trust_score"] == 0


# ===== Tech Extractor =====

class TestTechExtractor:

    @pytest.fixture
    def extractor(self):
        return TechExtractor({})

    def test_detect_wordpress(self, extractor):
        mock_html = '<html><body>wp-content/plugins/contact-form-7<style id="WordPress"></style></body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "wordpress"

    def test_detect_bitrix(self, extractor):
        mock_html = '<html><body><script src="/bitrix/js/main.js"></script></body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "bitrix"

    def test_detect_tilda(self, extractor):
        mock_html = '<html><body>created on Tilda</body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "tilda"

    def test_detect_flexbe(self, extractor):
        mock_html = '<html><body><script src="https://flexbe.com/widget.js"></script></body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "flexbe"

    def test_detect_lpmotor(self, extractor):
        mock_html = '<html><body><meta name="generator" content="lpmotor"></body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "lpmotor"

    def test_detect_joomla(self, extractor):
        mock_html = '<html><body><meta name="generator" content="Joomla!"></body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "joomla"

    def test_detect_opencart(self, extractor):
        mock_html = '<html><body><a href="index.php?route=common/home">Home</a></body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "opencart"

    def test_detect_marquiz(self, extractor):
        mock_html = '<html><body><script src="https://marquiz.ru/widget.js"></script></body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["has_marquiz"] is True

    def test_detect_unknown_cms(self, extractor):
        mock_html = '<html><body><h1>Simple page</h1></body></html>'
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=mock_html):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "unknown"
        assert result["has_marquiz"] is False

    def test_empty_url(self, extractor):
        result = extractor.extract("")
        assert result["cms"] == "unknown"

    def test_none_url(self, extractor):
        result = extractor.extract(None)
        assert result["cms"] == "unknown"

    def test_fetch_page_failure(self, extractor):
        with patch("granite.enrichers.tech_extractor.fetch_page", return_value=None):
            result = extractor.extract("http://site.ru")
        assert result["cms"] == "unknown"

    def test_unsafe_url_returns_default_dict(self, extractor):
        """BUG-001: is_safe_url(url)=False должен возвращать result, а не None."""
        with patch("granite.enrichers.tech_extractor.is_safe_url", return_value=False):
            result = extractor.extract("http://127.0.0.1")
        assert result is not None, "extract() must never return None"
        assert isinstance(result, dict)
        assert result["cms"] == "unknown"
        assert result["has_marquiz"] is False

    def test_unsafe_url_all_internal_ips(self, extractor):
        """BUG-001: все internal URL возвращают dict, не None."""
        unsafe_urls = [
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1",
            "http://192.168.1.1",
            "http://[::1]",
        ]
        for url in unsafe_urls:
            with patch("granite.enrichers.tech_extractor.is_safe_url", return_value=False):
                result = extractor.extract(url)
            assert result is not None, f"extract({url!r}) returned None"
            assert "cms" in result
            assert "has_marquiz" in result

    def test_extract_always_returns_dict_with_required_keys(self, extractor):
        """extract() всегда возвращает dict с 'cms' и 'has_marquiz'."""
        test_urls = [
            None,
            "",
            "http://safe-site.ru",
            "http://127.0.0.1",
        ]
        for url in test_urls:
            with patch("granite.enrichers.tech_extractor.is_safe_url", return_value=(url is not None and "127" not in (url or ""))):
                with patch("granite.enrichers.tech_extractor.fetch_page", return_value="<html></html>"):
                    result = extractor.extract(url)
            assert isinstance(result, dict), f"extract({url!r}) returned {type(result)}"
            assert "cms" in result
            assert "has_marquiz" in result


# ===== Messenger Scanner =====

class TestMessengerScanner:

    @pytest.fixture
    def scanner(self):
        return MessengerScanner({})

    def test_find_telegram_link(self, scanner):
        html = '<a href="https://t.me/granit_master">Telegram</a>'
        result = {}
        scanner._extract_social_links(html, result)
        assert "telegram" in result
        assert result["telegram"] == "https://t.me/granit_master"

    def test_skip_share_link(self, scanner):
        """Ссылки 'share' и 'joinchat' пропускаются."""
        html = '<a href="https://t.me/share/url">Share</a>'
        result = {}
        scanner._extract_social_links(html, result)
        assert "telegram" not in result

    def test_find_whatsapp_link(self, scanner):
        html = '<a href="https://api.whatsapp.com/send?phone=79031234567">WhatsApp</a>'
        result = {}
        scanner._extract_social_links(html, result)
        assert "whatsapp" in result

    def test_find_vk_link(self, scanner):
        html = '<a href="https://vk.com/granit_master">VK</a>'
        result = {}
        scanner._extract_social_links(html, result)
        assert "vk" in result

    def test_find_vk_www_link(self, scanner):
        """VK с www."""
        html = '<a href="https://www.vk.com/granit_master">VK</a>'
        result = {}
        scanner._extract_social_links(html, result)
        assert "vk" in result

    def test_no_duplicates(self, scanner):
        """Не перезаписывает первый найденный мессенджер."""
        html = (
            '<a href="https://t.me/first">TG1</a>'
            '<a href="https://t.me/second">TG2</a>'
        )
        result = {}
        scanner._extract_social_links(html, result)
        assert result["telegram"] == "https://t.me/first"

    def test_empty_html(self, scanner):
        result = {}
        scanner._extract_social_links("", result)
        assert len(result) == 0

    def test_none_html(self, scanner):
        result = {}
        scanner._extract_social_links(None, result)
        assert len(result) == 0

    def test_find_contacts_link_by_text(self, scanner):
        html = '<a href="/contacts">Контакты</a>'
        result = scanner._find_contacts_link("https://site.ru", html)
        assert result == "https://site.ru/contacts"

    def test_find_contacts_link_by_url(self, scanner):
        html = '<a href="/kontakty">Ссылка</a>'
        result = scanner._find_contacts_link("https://site.ru", html)
        assert result == "https://site.ru/kontakty"

    def test_no_contacts_link(self, scanner):
        html = '<a href="/about">О нас</a>'
        result = scanner._find_contacts_link("https://site.ru", html)
        assert result is None

    def test_scan_website_with_mock(self, scanner):
        """Полный цикл: главная → контакты → мессенджеры."""
        main_html = (
            '<a href="/kontakty">Контакты</a>'
            '<a href="https://vk.com/granit">VK</a>'
        )
        contacts_html = (
            '<a href="https://t.me/granit">TG</a>'
            '<a href="https://api.whatsapp.com/send?phone=79031234567">WhatsApp</a>'
        )
        with patch("granite.enrichers.messenger_scanner.fetch_page", side_effect=[main_html, contacts_html]):
            result = scanner.scan_website("https://granit.ru")
        assert "telegram" in result
        assert "whatsapp" in result
        assert "vk" in result

    def test_extract_emails_from_html(self, scanner):
        """Email извлекается из mailto: и текста."""
        html = '<a href="mailto:info@granit.ru">info@granit.ru</a><p>sales@test.ru</p>'
        result = {}
        scanner._extract_emails(html, result)
        assert "info@granit.ru" in result["_emails"]
        assert "sales@test.ru" in result["_emails"]

    def test_extract_phones_from_html(self, scanner):
        """Телефоны извлекаются из tel: и текста."""
        html = '<a href="tel:+79031234567">Позвонить</a><p>+7 (999) 111-22-33</p>'
        result = {}
        scanner._extract_phones(html, result)
        assert len(result["_phones"]) >= 2

    def test_scan_website_empty_url(self, scanner):
        result = scanner.scan_website("")
        assert result == {"_emails": [], "_phones": []}

    def test_scan_website_none_url(self, scanner):
        result = scanner.scan_website(None)
        assert result == {"_emails": [], "_phones": []}

    def test_find_relevant_links(self, scanner):
        html = (
            '<a href="/about">О нас</a>'
            '<a href="/proizvodstvo">Производство</a>'
            '<a href="https://other.com/page">External</a>'
            '<a href="#top">Наверх</a>'
        )
        links = scanner._find_relevant_links(html, "https://site.ru")
        assert len(links) >= 1
        assert all("site.ru" in link for link in links)
        assert len(links) <= 3  # не более 3 доп. страниц


# ===== TG Rate Limit Backoff =====

class TestTgRateLimit:

    def test_429_triggers_retry(self):
        """При HTTP 429 tg_request повторяет запрос."""
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.text = ""

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.text = "Telegram: Contact"

        with patch("granite.enrichers.tg_finder.requests.get", side_effect=[resp_429, resp_ok]):
            with patch("granite.enrichers.tg_finder.random.uniform", return_value=0):
                with patch("granite.enrichers.tg_finder.time.sleep") as mock_sleep:
                    from granite.enrichers.tg_finder import tg_request
                    result = tg_request("https://t.me/test", {})
        assert result is not None
        assert mock_sleep.call_count >= 1

    def test_429_exhausted_returns_none(self):
        """При исчерпании попыток возвращает None."""
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.text = ""

        with patch("granite.enrichers.tg_finder.requests.get", return_value=resp_429):
            with patch("granite.enrichers.tg_finder.random.uniform", return_value=0):
                with patch("granite.enrichers.tg_finder.time.sleep"):
                    from granite.enrichers.tg_finder import tg_request
                    result = tg_request("https://t.me/test", {})
        assert result is None

    def test_200_returns_immediately(self):
        """При 200 ответ возвращается сразу, без задержки."""
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.text = "OK"

        with patch("granite.enrichers.tg_finder.requests.get", return_value=resp_ok):
            with patch("granite.enrichers.tg_finder.time.sleep") as mock_sleep:
                from granite.enrichers.tg_finder import tg_request
                result = tg_request("https://t.me/test", {})
        assert result is not None
        assert mock_sleep.call_count == 0

    def test_connection_error_returns_none(self):
        """При ошибке соединения возвращает None."""
        import requests as req_mod
        with patch("granite.enrichers.tg_finder.requests.get", side_effect=req_mod.RequestException("connection refused")):
            from granite.enrichers.tg_finder import tg_request
            result = tg_request("https://t.me/test", {})
        assert result is None
