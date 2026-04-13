# tests/test_async_enrichment.py — Тесты Фазы 8: async HTTP и async обогащение
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from granite.http_client import (
    async_fetch_page,
    async_head,
    async_get,
    async_adaptive_delay,
    run_async,
)
from granite.enrichers.messenger_scanner import MessengerScanner
from granite.enrichers.tech_extractor import TechExtractor
from granite.enrichers.tg_finder import (
    find_tg_by_phone_async,
    find_tg_by_name_async,
)
from granite.enrichers.tg_trust import check_tg_trust_async
from granite.pipeline.web_client import WebClient


# ===== http_client =====


def _make_mock_response(status_code=200, text="OK"):
    """Create a mock httpx Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        mock_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"HTTP {status_code}", request=MagicMock(), response=mock_resp
            )
        )
    return mock_resp


class TestAsyncFetchPage:
    """Тесты async_fetch_page."""

    @pytest.mark.asyncio
    async def test_returns_html_on_success(self):
        mock_resp = _make_mock_response(200, "<html>Hello</html>")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                result = await async_fetch_page("http://example.com")
        assert result == "<html>Hello</html>"

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self):
        mock_resp = _make_mock_response(404)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                result = await async_fetch_page("http://example.com/missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_unsafe_url(self):
        result = await async_fetch_page("http://127.0.0.1/admin")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                result = await async_fetch_page("http://slow.example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                result = await async_fetch_page("http://unreachable.example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_403(self):
        mock_resp = _make_mock_response(403)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                result = await async_fetch_page("http://blocked.example.com")
        assert result is None


class TestAsyncHead:

    @pytest.mark.asyncio
    async def test_returns_status_code(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.head = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                result = await async_head("http://example.com")
        assert result == 200

    @pytest.mark.asyncio
    async def test_returns_none_on_unsafe(self):
        result = await async_head("http://169.254.169.254/latest")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_url(self):
        result = await async_head("")
        assert result is None


class TestAsyncGet:

    @pytest.mark.asyncio
    async def test_returns_response_on_200(self):
        mock_resp = _make_mock_response(200, "OK")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                result = await async_get("http://example.com", {}, timeout=5, max_retries=3)
        assert result is not None
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_retries_on_429(self):
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_ok = _make_mock_response(200, "OK")

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=[resp_429, resp_ok])

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                with patch("granite.http_client.asyncio.sleep", new_callable=AsyncMock):
                    result = await async_get("http://example.com", {}, max_retries=3)
        assert result is not None
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_on_exhausted_retries(self):
        resp_429 = MagicMock()
        resp_429.status_code = 429

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=resp_429)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                with patch("granite.http_client.asyncio.sleep", new_callable=AsyncMock):
                    result = await async_get("http://example.com", {}, max_retries=2)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_unsafe_url(self):
        result = await async_get("http://127.0.0.1", {}, max_retries=1)
        assert result is None


class TestAsyncAdaptiveDelay:

    @pytest.mark.asyncio
    async def test_returns_delay_value(self):
        with patch("granite.http_client.random.uniform", return_value=0.5):
            with patch("granite.http_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await async_adaptive_delay(0.5, 0.5)
        assert result == 0.5
        mock_sleep.assert_called_once_with(0.5)


class TestRunAsync:

    def test_run_async_simple_coroutine(self):
        async def simple():
            return 42

        result = run_async(simple())
        assert result == 42

    def test_run_async_with_exception(self):
        async def failing():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_async(failing())


# ===== MessengerScanner async =====


class TestMessengerScannerAsync:

    @pytest.fixture
    def scanner(self):
        return MessengerScanner({})

    @pytest.mark.asyncio
    async def test_scan_website_async_finds_telegram(self, scanner):
        main_html = (
            '<a href="https://t.me/granit_async">TG</a>'
            '<a href="mailto:info@granit.ru">info@granit.ru</a>'
        )
        with patch.object(scanner, "scan_website_async", wraps=scanner.scan_website_async):
            pass
        with patch("granite.enrichers.messenger_scanner.async_fetch_page",
                    return_value=main_html):
            with patch("granite.enrichers.messenger_scanner.async_adaptive_delay",
                        new_callable=AsyncMock):
                result = await scanner.scan_website_async("https://granit.ru")
        assert "telegram" in result
        assert result["telegram"] == "https://t.me/granit_async"

    @pytest.mark.asyncio
    async def test_scan_website_async_finds_whatsapp(self, scanner):
        main_html = '<a href="https://api.whatsapp.com/send?phone=79031234567">WA</a>'
        with patch("granite.enrichers.messenger_scanner.async_fetch_page",
                    return_value=main_html):
            with patch("granite.enrichers.messenger_scanner.async_adaptive_delay",
                        new_callable=AsyncMock):
                result = await scanner.scan_website_async("https://site.ru")
        assert "whatsapp" in result

    @pytest.mark.asyncio
    async def test_scan_website_async_empty_url(self, scanner):
        result = await scanner.scan_website_async("")
        assert result == {"_emails": [], "_phones": []}

    @pytest.mark.asyncio
    async def test_scan_website_async_none_url(self, scanner):
        result = await scanner.scan_website_async(None)
        assert result == {"_emails": [], "_phones": []}

    @pytest.mark.asyncio
    async def test_scan_website_async_unsafe_url(self, scanner):
        result = await scanner.scan_website_async("http://127.0.0.1")
        assert result == {"_emails": [], "_phones": []}

    @pytest.mark.asyncio
    async def test_scan_website_async_follows_contacts(self, scanner):
        main_html = '<a href="/kontakty">Контакты</a>'
        contacts_html = '<a href="https://t.me/granit2">TG2</a>'

        with patch("granite.enrichers.messenger_scanner.async_fetch_page",
                    side_effect=[main_html, contacts_html]):
            with patch("granite.enrichers.messenger_scanner.async_adaptive_delay",
                        new_callable=AsyncMock):
                result = await scanner.scan_website_async("https://granit.ru")
        assert "telegram" in result

    @pytest.mark.asyncio
    async def test_scan_website_async_handles_exception(self, scanner):
        with patch("granite.enrichers.messenger_scanner.async_fetch_page",
                    side_effect=Exception("network error")):
            with patch("granite.enrichers.messenger_scanner.async_adaptive_delay",
                        new_callable=AsyncMock):
                result = await scanner.scan_website_async("https://error.ru")
        assert result == {"_emails": [], "_phones": []}

    @pytest.mark.asyncio
    async def test_scan_website_async_extracts_emails(self, scanner):
        html = '<a href="mailto:test@example.com">Email</a>'
        with patch("granite.enrichers.messenger_scanner.async_fetch_page",
                    return_value=html):
            with patch("granite.enrichers.messenger_scanner.async_adaptive_delay",
                        new_callable=AsyncMock):
                result = await scanner.scan_website_async("https://site.ru")
        assert "test@example.com" in result["_emails"]


# ===== TG Finder async =====


class TestTgFinderAsync:

    @pytest.fixture
    def tg_config(self):
        return {"enrichment": {"tg_finder": {"check_delay": 0.01}}}

    @pytest.mark.asyncio
    async def test_find_tg_by_phone_async_with_contact(self, tg_config):
        mock_resp = _make_mock_response(200, "Telegram: Contact tgme_action_button_new")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.enrichers.tg_finder.is_safe_url", return_value=True):
                with patch("granite.enrichers.tg_finder.async_adaptive_delay", new_callable=AsyncMock):
                    result = await find_tg_by_phone_async("79031234567", tg_config)
        assert result == "https://t.me/+79031234567"

    @pytest.mark.asyncio
    async def test_find_tg_by_phone_async_no_contact(self, tg_config):
        mock_resp = _make_mock_response(200, "Telegram")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.enrichers.tg_finder.is_safe_url", return_value=True):
                result = await find_tg_by_phone_async("79031234567", tg_config)
        assert result is None

    @pytest.mark.asyncio
    async def test_find_tg_by_phone_async_short_number(self, tg_config):
        result = await find_tg_by_phone_async("123", tg_config)
        assert result is None

    @pytest.mark.asyncio
    async def test_find_tg_by_phone_async_empty(self, tg_config):
        result = await find_tg_by_phone_async("", tg_config)
        assert result is None

    @pytest.mark.asyncio
    async def test_find_tg_by_name_async_with_keywords(self, tg_config):
        mock_resp = _make_mock_response(200, (
            '<div class="tgme_page_title">Памятники Гранит</div>'
            '<div class="tgme_page_description">Ритуальные услуги</div>'
        ))
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.enrichers.tg_finder.is_safe_url", return_value=True):
                with patch("granite.enrichers.tg_finder.async_adaptive_delay", new_callable=AsyncMock):
                    result = await find_tg_by_name_async("Памятники Гранит", "79031234567", tg_config)
        assert result is not None
        assert "t.me/" in result

    @pytest.mark.asyncio
    async def test_find_tg_by_name_async_no_keywords(self, tg_config):
        mock_resp = _make_mock_response(200, (
            '<div class="tgme_page_title">Some User</div>'
            '<div class="tgme_page_description">Just a person</div>'
        ))
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.enrichers.tg_finder.is_safe_url", return_value=True):
                with patch("granite.enrichers.tg_finder.async_adaptive_delay", new_callable=AsyncMock):
                    result = await find_tg_by_name_async("Random Name", None, tg_config)
        assert result is None

    @pytest.mark.asyncio
    async def test_find_tg_by_name_async_none_name(self, tg_config):
        result = await find_tg_by_name_async(None, "79031234567", tg_config)
        assert result is None


# ===== TG Trust async =====


class TestTgTrustAsync:

    @pytest.mark.asyncio
    async def test_check_tg_trust_async_full_profile(self):
        mock_resp = _make_mock_response(200, (
            '<img class="tgme_page_photo_image" src="avatar.jpg">'
            '<div class="tgme_page_description">Описание</div>'
        ))
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                with patch("granite.enrichers.tg_trust.async_adaptive_delay", new_callable=AsyncMock):
                    result = await check_tg_trust_async("https://t.me/granit_master")
        assert result["has_avatar"] is True
        assert result["has_description"] is True
        assert result["trust_score"] == 2

    @pytest.mark.asyncio
    async def test_check_tg_trust_async_bot(self):
        mock_resp = _make_mock_response(200, '<div class="tgme_page_bot_button">Start</div>')
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                with patch("granite.enrichers.tg_trust.async_adaptive_delay", new_callable=AsyncMock):
                    result = await check_tg_trust_async("https://t.me/granit_bot")
        assert result["is_bot"] is True
        assert result["trust_score"] < 0

    @pytest.mark.asyncio
    async def test_check_tg_trust_async_empty(self):
        mock_resp = _make_mock_response(200, "<html>Telegram</html>")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                with patch("granite.enrichers.tg_trust.async_adaptive_delay", new_callable=AsyncMock):
                    result = await check_tg_trust_async("https://t.me/empty")
        assert result["trust_score"] == 0

    @pytest.mark.asyncio
    async def test_check_tg_trust_async_none_url(self):
        result = await check_tg_trust_async(None)
        assert result["trust_score"] == 0

    @pytest.mark.asyncio
    async def test_check_tg_trust_async_request_failure(self):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=None)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                with patch("granite.enrichers.tg_trust.async_adaptive_delay", new_callable=AsyncMock):
                    result = await check_tg_trust_async("https://t.me/notfound")
        assert result["trust_score"] == 0

    @pytest.mark.asyncio
    async def test_check_tg_trust_async_with_config(self):
        config = {
            "enrichment": {
                "tg_finder": {"request_timeout": 5, "max_retries": 1, "initial_backoff": 2},
                "tg_trust": {"check_delay_min": 0.5, "check_delay_max": 1.0},
            }
        }
        mock_resp = _make_mock_response(200, "<html>Telegram</html>")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.http_client.is_safe_url", return_value=True):
                with patch("granite.enrichers.tg_trust.async_adaptive_delay", new_callable=AsyncMock) as mock_delay:
                    result = await check_tg_trust_async("https://t.me/test", config)
        assert result["trust_score"] == 0
        mock_client.get.assert_called_once()
        call_kwargs = mock_client.get.call_args
        assert call_kwargs[1]["timeout"] == 5


# ===== TechExtractor async =====


class TestTechExtractorAsync:

    @pytest.fixture
    def extractor(self):
        return TechExtractor({})

    @pytest.mark.asyncio
    async def test_detect_wordpress_async(self, extractor):
        with patch("granite.enrichers.tech_extractor.async_fetch_page",
                    return_value="<html>wp-content</html>"):
            result = await extractor.extract_async("http://site.ru")
        assert result["cms"] == "wordpress"

    @pytest.mark.asyncio
    async def test_detect_bitrix_async(self, extractor):
        with patch("granite.enrichers.tech_extractor.async_fetch_page",
                    return_value="<html>bitrix</html>"):
            result = await extractor.extract_async("http://site.ru")
        assert result["cms"] == "bitrix"

    @pytest.mark.asyncio
    async def test_detect_tilda_async(self, extractor):
        with patch("granite.enrichers.tech_extractor.async_fetch_page",
                    return_value="<html>created on Tilda</html>"):
            result = await extractor.extract_async("http://site.ru")
        assert result["cms"] == "tilda"

    @pytest.mark.asyncio
    async def test_detect_marquiz_async(self, extractor):
        with patch("granite.enrichers.tech_extractor.async_fetch_page",
                    return_value="<html>marquiz.ru</html>"):
            result = await extractor.extract_async("http://site.ru")
        assert result["has_marquiz"] is True

    @pytest.mark.asyncio
    async def test_empty_url_async(self, extractor):
        result = await extractor.extract_async("")
        assert result["cms"] == "unknown"

    @pytest.mark.asyncio
    async def test_unsafe_url_async(self, extractor):
        result = await extractor.extract_async("http://127.0.0.1")
        assert result["cms"] == "unknown"

    @pytest.mark.asyncio
    async def test_fetch_failure_async(self, extractor):
        with patch("granite.enrichers.tech_extractor.async_fetch_page", return_value=None):
            result = await extractor.extract_async("http://site.ru")
        assert result["cms"] == "unknown"

    @pytest.mark.asyncio
    async def test_detect_joomla_async(self, extractor):
        with patch("granite.enrichers.tech_extractor.async_fetch_page",
                    return_value="<html>Joomla!</html>"):
            result = await extractor.extract_async("http://site.ru")
        assert result["cms"] == "joomla"


# ===== WebClient async =====


class TestWebClientAsync:

    @pytest.fixture
    def wc(self):
        return WebClient(timeout=60, search_limit=3, search_delay=0.01)

    @pytest.mark.asyncio
    async def test_search_async_returns_results(self, wc):
        html = (
            '<div class="g"><a href="http://site.ru"><h3>Site</h3></a></div>'
            '<div class="g"><a href="http://other.com"><h3>Other</h3></a></div>'
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=_make_mock_response(200, html))

        with patch("granite.http_client._client", mock_client):
            with patch("granite.pipeline.web_client.is_safe_url", return_value=True):
                with patch("granite.pipeline.web_client.asyncio.sleep", new_callable=AsyncMock):
                    result = await wc.search_async("test query")
        assert result is not None
        assert len(result["data"]["web"]) == 2

    @pytest.mark.asyncio
    async def test_search_async_empty_results(self, wc):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=_make_mock_response(200, "<html>No results</html>"))

        with patch("granite.http_client._client", mock_client):
            with patch("granite.pipeline.web_client.is_safe_url", return_value=True):
                with patch("granite.pipeline.web_client.asyncio.sleep", new_callable=AsyncMock):
                    result = await wc.search_async("nothing found")
        assert result is None

    @pytest.mark.asyncio
    async def test_search_async_fetch_failure(self, wc):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=None)

        with patch("granite.http_client._client", mock_client):
            with patch("granite.pipeline.web_client.is_safe_url", return_value=True):
                with patch("granite.pipeline.web_client.asyncio.sleep", new_callable=AsyncMock):
                    result = await wc.search_async("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_scrape_async_returns_contacts(self, wc):
        html = (
            '<html><body>'
            '<a href="tel:+79031234567">Позвонить</a>'
            '<a href="mailto:info@test.ru">Email</a>'
            '</body></html>'
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=_make_mock_response(200, html))

        with patch("granite.http_client._client", mock_client):
            with patch("granite.pipeline.web_client.is_safe_url", return_value=True):
                result = await wc.scrape_async("http://site.ru")
        assert result is not None
        assert len(result["phones"]) >= 1
        assert "info@test.ru" in result["emails"]

    @pytest.mark.asyncio
    async def test_scrape_async_short_content(self, wc):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=_make_mock_response(200, "abc"))

        with patch("granite.http_client._client", mock_client):
            with patch("granite.pipeline.web_client.is_safe_url", return_value=True):
                result = await wc.scrape_async("http://short.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_scrape_async_unsafe_url(self, wc):
        result = await wc.scrape_async("http://169.254.169.254/")
        assert result is None

    @pytest.mark.asyncio
    async def test_scrape_async_invalid_url(self, wc):
        result = await wc.scrape_async("not-a-url")
        assert result is None

    @pytest.mark.asyncio
    async def test_search_async_filters_google_urls(self, wc):
        html = (
            '<div class="g"><a href="/search?q=test"><h3>Google Link</h3></a></div>'
            '<div class="g"><a href="http://real.com"><h3>Real</h3></a></div>'
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=_make_mock_response(200, html))

        with patch("granite.http_client._client", mock_client):
            with patch("granite.pipeline.web_client.is_safe_url", return_value=True):
                with patch("granite.pipeline.web_client.asyncio.sleep", new_callable=AsyncMock):
                    result = await wc.search_async("test")
        assert result is not None
        assert len(result["data"]["web"]) == 1
        assert result["data"]["web"][0]["url"] == "http://real.com"

    @pytest.mark.asyncio
    async def test_search_async_rate_limiting(self, wc):
        """asyncio.sleep вызывается для rate limiting."""
        import time as _time
        wc._last_search_time = _time.time()  # simulate recent request
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(
            return_value=_make_mock_response(200, '<div class="g"><a href="http://s.ru"><h3>S</h3></a></div>')
        )

        with patch("granite.http_client._client", mock_client):
            with patch("granite.pipeline.web_client.is_safe_url", return_value=True):
                with patch("granite.pipeline.web_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    await wc.search_async("test")
        # sleep is called for rate limiting (search_delay)
        mock_sleep.assert_called_once()


# ===== EnrichmentPhase async =====


class TestEnrichmentPhaseAsync:

    def _make_config(self, async_enabled=False):
        return {
            "enrichment": {
                "max_concurrent": 3,
                "batch_flush": 10,
                "async_enabled": async_enabled,
            },
            "sources": {"web_search": {"enabled": False}},
        }

    def test_is_async_enabled_true(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase
        config = self._make_config(async_enabled=True)
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())
        assert phase._is_async_enabled() is True

    def test_is_async_enabled_false(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase
        config = self._make_config(async_enabled=False)
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())
        assert phase._is_async_enabled() is False

    def test_is_async_enabled_default(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase
        phase = EnrichmentPhase({}, db=MagicMock(), web_client=MagicMock())
        assert phase._is_async_enabled() is False

    @pytest.mark.asyncio
    async def test_enrich_one_company_async(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase
        from granite.database import EnrichedCompanyRow

        config = self._make_config()
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())

        snapshot = {
            "id": 1, "name_best": "Тест Компания", "phones": ["79031234567"],
            "address": "ул. Тест", "website": None, "emails": [],
            "city": "Тест", "messengers": {},
        }

        with patch("granite.pipeline.enrichment_phase.find_tg_by_phone_async", return_value=None):
            with patch("granite.pipeline.enrichment_phase.find_tg_by_name_async", return_value=None):
                result = await phase._enrich_one_company_async(
                    snapshot, MessengerScanner(config), TechExtractor(config)
                )

        assert isinstance(result, EnrichedCompanyRow)
        assert result.id == 1
        assert result.name == "Тест Компания"

    @pytest.mark.asyncio
    async def test_enrich_one_company_async_with_website(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase

        config = self._make_config()
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())

        snapshot = {
            "id": 2, "name_best": "Гранит Мастер", "phones": [],
            "address": "", "website": "https://granit.ru",
            "emails": [], "city": "Тест", "messengers": {},
        }

        site_data = {"telegram": "https://t.me/granit", "_emails": [], "_phones": []}
        tech_data = {"cms": "wordpress", "has_marquiz": False}

        with patch("granite.pipeline.enrichment_phase.validate_website",
                    return_value=("https://granit.ru", 200)):
            with patch.object(MessengerScanner, "scan_website_async", return_value=site_data):
                with patch.object(TechExtractor, "extract_async", return_value=tech_data):
                    with patch("granite.pipeline.enrichment_phase.find_tg_by_phone_async", return_value=None):
                        with patch("granite.pipeline.enrichment_phase.find_tg_by_name_async", return_value=None):
                            result = await phase._enrich_one_company_async(
                                snapshot, MessengerScanner(config), TechExtractor(config)
                            )

        assert result.messengers.get("telegram") == "https://t.me/granit"
        assert result.cms == "wordpress"

    @pytest.mark.asyncio
    async def test_enrich_companies_async_sequential(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase

        config = self._make_config()
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())
        scanner = MessengerScanner(config)
        tech_ext = TechExtractor(config)

        snapshots = [
            {"id": 1, "name_best": "Компания 1", "phones": [], "address": "",
             "website": None, "emails": [], "city": "Тест", "messengers": {}},
            {"id": 2, "name_best": "Компания 2", "phones": [], "address": "",
             "website": None, "emails": [], "city": "Тест", "messengers": {}},
        ]

        with patch("granite.pipeline.enrichment_phase.find_tg_by_phone_async", return_value=None):
            with patch("granite.pipeline.enrichment_phase.find_tg_by_name_async", return_value=None):
                results = await phase._enrich_companies_async_sequential(
                    snapshots, scanner, tech_ext
                )

        assert len(results) == 2
        assert all(r is not None for r in results)

    @pytest.mark.asyncio
    async def test_enrich_companies_async_parallel(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase

        config = self._make_config()
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())
        scanner = MessengerScanner(config)
        tech_ext = TechExtractor(config)

        snapshots = [
            {"id": i, "name_best": f"Компания {i}", "phones": [], "address": "",
             "website": None, "emails": [], "city": "Тест", "messengers": {}}
            for i in range(5)
        ]

        with patch("granite.pipeline.enrichment_phase.find_tg_by_phone_async", return_value=None):
            with patch("granite.pipeline.enrichment_phase.find_tg_by_name_async", return_value=None):
                results = await phase._enrich_companies_async_parallel(
                    snapshots, scanner, tech_ext, max_concurrent=2
                )

        assert len(results) == 5
        assert all(r is not None for r in results)

    @pytest.mark.asyncio
    async def test_enrich_companies_async_handles_errors(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase

        config = self._make_config()
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())
        scanner = MessengerScanner(config)
        tech_ext = TechExtractor(config)

        call_count = {"n": 0}

        original_method = phase._enrich_one_company_async

        async def _failing_enrich(snap, sc, te):
            call_count["n"] += 1
            if snap["id"] == 2:
                raise ConnectionError("test failure")
            return await original_method(snap, sc, te)

        snapshots = [
            {"id": 1, "name_best": "OK", "phones": [], "address": "",
             "website": None, "emails": [], "city": "Тест", "messengers": {}},
            {"id": 2, "name_best": "FAIL", "phones": [], "address": "",
             "website": None, "emails": [], "city": "Тест", "messengers": {}},
            {"id": 3, "name_best": "OK2", "phones": [], "address": "",
             "website": None, "emails": [], "city": "Тест", "messengers": {}},
        ]

        with patch("granite.pipeline.enrichment_phase.find_tg_by_phone_async", return_value=None):
            with patch("granite.pipeline.enrichment_phase.find_tg_by_name_async", return_value=None):
                with patch.object(phase, "_enrich_one_company_async", _failing_enrich):
                    results = await phase._enrich_companies_async_parallel(
                        snapshots, scanner, tech_ext, max_concurrent=2
                    )

        assert call_count["n"] == 3
        assert results[0] is not None
        assert results[1] is None  # error
        assert results[2] is not None

    @pytest.mark.asyncio
    async def test_find_tg_by_phone_async_in_enrichment(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase

        config = self._make_config()
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())

        snapshot = {
            "id": 10, "name_best": "Тест", "phones": ["79031234567"],
            "address": "", "website": None, "emails": [],
            "city": "Тест", "messengers": {},
        }

        with patch("granite.pipeline.enrichment_phase.find_tg_by_phone_async",
                    return_value="https://t.me/+79031234567") as mock_phone:
            with patch("granite.pipeline.enrichment_phase.find_tg_by_name_async", return_value=None):
                with patch("granite.pipeline.enrichment_phase.check_tg_trust_async",
                            return_value={"trust_score": 0}):
                    result = await phase._enrich_one_company_async(
                        snapshot, MessengerScanner(config), TechExtractor(config)
                    )

        assert result.messengers["telegram"] == "https://t.me/+79031234567"
        mock_phone.assert_called_once_with("79031234567", config)

    @pytest.mark.asyncio
    async def test_find_tg_by_name_async_fallback(self):
        from granite.pipeline.enrichment_phase import EnrichmentPhase

        config = self._make_config()
        phase = EnrichmentPhase(config, db=MagicMock(), web_client=MagicMock())

        snapshot = {
            "id": 11, "name_best": "Памятники Гранит", "phones": ["79031234567"],
            "address": "", "website": None, "emails": [],
            "city": "Тест", "messengers": {},
        }

        with patch("granite.pipeline.enrichment_phase.find_tg_by_phone_async",
                    return_value=None):
            with patch("granite.pipeline.enrichment_phase.find_tg_by_name_async",
                        return_value="https://t.me/pamyatnikigranit") as mock_name:
                with patch("granite.pipeline.enrichment_phase.check_tg_trust_async",
                            return_value={"trust_score": 0}):
                    result = await phase._enrich_one_company_async(
                        snapshot, MessengerScanner(config), TechExtractor(config)
                    )

        assert result.messengers["telegram"] == "https://t.me/pamyatnikigranit"
        mock_name.assert_called_once()


# ===== PipelineManager async detection =====


class TestPipelineManagerAsync:

    def test_run_phase_calls_sync(self):
        from granite.pipeline.manager import PipelineManager
        config = {}
        pm = PipelineManager(config, MagicMock())

        fn = MagicMock()
        pm._run_phase("test", fn)
        fn.assert_called_once()

    def test_run_phase_calls_async(self):
        from granite.pipeline.manager import PipelineManager
        config = {}
        pm = PipelineManager(config, MagicMock())

        async def async_fn():
            return 42

        with patch("granite.pipeline.manager.asyncio.run") as mock_run:
            pm._run_phase("test", async_fn)
        mock_run.assert_called_once()
