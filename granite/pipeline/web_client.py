# pipeline/web_client.py
"""Клиент для поиска и скрапинга сайтов (requests + BeautifulSoup).

Использует Google SERP для поиска и requests+BeautifulSoup для парсинга.
Также предоставляет async-версии для параллельного обогащения.
"""

import asyncio
import threading
import time
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from loguru import logger
from granite.utils import extract_phones, extract_emails, is_safe_url, _sanitize_url_for_log, fetch_page, adaptive_delay
from granite.http_client import async_fetch_page, async_adaptive_delay

MIN_CONTENT_LENGTH = 100


class WebClient:
    """Обёртка для веб-поиска и скрапинга (search + scrape).

    Потокобезопасность: search() сериализован через threading.Lock для
    rate limiting Google SERP. scrape() — без блокировки (разные домены).
    """

    # Adaptive backoff при Google 429
    _SEARCH_DELAY_MIN = 2.0
    _SEARCH_DELAY_MAX = 120.0

    def __init__(
        self, timeout: int = 60, search_limit: int = 3,
        search_delay: float = 2.0,
    ):
        self.timeout = timeout
        self.search_limit = search_limit
        self.search_delay = search_delay
        self._search_lock = threading.Lock()
        self._async_search_lock = asyncio.Lock()
        self._last_search_time = 0.0

    def search(self, query: str) -> dict | None:
        """Поиск через Google SERP (с adaptive rate limiting).

        Сериализован через Lock: при параллельном обогащении (ThreadPoolExecutor)
        Google-запросы выполняются последовательно с задержкой search_delay
        между ними. При HTTP 429 задержка удваивается (до 120 сек).

        Returns:
            dict с ключом "data.web" — список результатов, или None.
        """
        with self._search_lock:
            # Rate limiting: минимальная задержка между Google-запросами
            if self.search_delay > 0:
                now = time.time()
                wait = self.search_delay - (now - self._last_search_time)
                if wait > 0:
                    time.sleep(wait)
            self._last_search_time = time.time()

            try:
                search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={self.search_limit}&hl=ru"
                html = fetch_page(search_url, timeout=15)

                # Google OK — сбрасываем задержку к базовому значению
                self._on_search_success()

                if not html:
                    return None

                soup = BeautifulSoup(html, "html.parser")
                web_results = []

                for g in soup.select("div.g"):
                    anchor = g.find("a", href=True)
                    title_el = g.find("h3")
                    if not anchor or not title_el:
                        continue

                    url = anchor["href"]
                    title = title_el.get_text(strip=True)

                    if not url or not title:
                        continue

                    # Пропускаем не-URL результаты Google
                    if url.startswith("/search") or url.startswith("#"):
                        continue
                    if "google.com" in url and "/search?" in url:
                        continue

                    web_results.append({"url": url, "title": title})

                if web_results:
                    return {"data": {"web": web_results}}

                logger.debug(f"WebClient search: 0 результатов для '{query[:60]}'")
                return None

            except Exception as e:
                err_str = str(e)
                if "429" in err_str:
                    self._on_search_429()
                logger.debug(f"WebClient search ошибка: {e}")
                return None

    def _on_search_success(self) -> None:
        """Сброс задержки к базовому значению при успешном запросе."""
        self.search_delay = self._SEARCH_DELAY_MIN

    def _on_search_429(self) -> None:
        """Удвоить задержку при 429 (adaptive backoff)."""
        self.search_delay = min(self.search_delay * 2, self._SEARCH_DELAY_MAX)
        logger.warning(
            f"Google 429 — задержка увеличена до {self.search_delay:.0f} сек"
        )

    def scrape(self, url: str) -> dict | None:
        """Скрапинг сайта через requests + BeautifulSoup.

        Returns:
            {"phones": [...], "emails": [...]} или None.
        """
        if url and not url.startswith(("http://", "https://")):
            logger.warning(f"Skipping invalid URL: {_sanitize_url_for_log(url)}")
            return None

        if not is_safe_url(url):
            logger.warning(f"SSRF blocked (web scrape): {_sanitize_url_for_log(url)}")
            return None

        try:
            html = fetch_page(url, timeout=15)

            if not html or len(html) < MIN_CONTENT_LENGTH:
                return None

            soup = BeautifulSoup(html, "html.parser")

            phones = []

            # Телефоны из tel: ссылок
            for tel_link in soup.select('a[href^="tel:"]'):
                href = tel_link.get("href", "")
                phone = href.replace("tel:", "").strip()
                if phone:
                    phones.append(phone)

            # Телефоны из текста страницы
            text = soup.get_text(separator=" ")
            for p in extract_phones(text):
                if p not in phones:
                    phones.append(p)

            # Email из HTML + mailto
            emails = extract_emails(html)
            for mailto in soup.select('a[href^="mailto:"]'):
                href = mailto.get("href", "")
                email = href.replace("mailto:", "").strip().split("?")[0]
                if email and email not in emails:
                    emails.append(email)

            return {"phones": phones, "emails": emails}

        except Exception as e:
            logger.debug(f"WebClient scrape ошибка: {e}")
            return None

    # ===== Async variants =====

    async def search_async(self, query: str) -> dict | None:
        """Async версия search — использует httpx.AsyncClient.

        Rate limiting через asyncio.Lock + adaptive backoff при 429.
        """
        async with self._async_search_lock:
            # Rate limiting: минимальная задержка между Google-запросами
            if self.search_delay > 0:
                now = time.time()
                wait = self.search_delay - (now - self._last_search_time)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_search_time = time.time()

            try:
                search_url = (
                    f"https://www.google.com/search?q={quote_plus(query)}"
                    f"&num={self.search_limit}&hl=ru"
                )
                html = await async_fetch_page(search_url, timeout=15)

                # Google OK — сбрасываем задержку
                self._on_search_success()

                if not html:
                    return None

                soup = BeautifulSoup(html, "html.parser")
                web_results = []

                for g in soup.select("div.g"):
                    anchor = g.find("a", href=True)
                    title_el = g.find("h3")
                    if not anchor or not title_el:
                        continue

                    url = anchor["href"]
                    title = title_el.get_text(strip=True)

                    if not url or not title:
                        continue

                    if url.startswith("/search") or url.startswith("#"):
                        continue
                    if "google.com" in url and "/search?" in url:
                        continue

                    web_results.append({"url": url, "title": title})

                if web_results:
                    return {"data": {"web": web_results}}

                logger.debug(
                    f"WebClient search_async: 0 результатов для '{query[:60]}'"
                )
                return None

            except Exception as e:
                err_str = str(e)
                if "429" in err_str:
                    self._on_search_429()
                logger.debug(f"WebClient search_async ошибка: {e}")
                return None

    async def scrape_async(self, url: str) -> dict | None:
        """Async версия scrape — использует httpx.AsyncClient.

        Returns:
            {"phones": [...], "emails": [...]} или None.
        """
        if url and not url.startswith(("http://", "https://")):
            logger.warning(f"Skipping invalid URL: {_sanitize_url_for_log(url)}")
            return None

        if not is_safe_url(url):
            logger.warning(
                f"SSRF blocked (web scrape): {_sanitize_url_for_log(url)}"
            )
            return None

        try:
            html = await async_fetch_page(url, timeout=15)

            if not html or len(html) < MIN_CONTENT_LENGTH:
                return None

            soup = BeautifulSoup(html, "html.parser")

            phones = []

            for tel_link in soup.select('a[href^="tel:"]'):
                href = tel_link.get("href", "")
                phone = href.replace("tel:", "").strip()
                if phone:
                    phones.append(phone)

            text = soup.get_text(separator=" ")
            for p in extract_phones(text):
                if p not in phones:
                    phones.append(p)

            emails = extract_emails(html)
            for mailto in soup.select('a[href^="mailto:"]'):
                href = mailto.get("href", "")
                email = href.replace("mailto:", "").strip().split("?")[0]
                if email and email not in emails:
                    emails.append(email)

            return {"phones": phones, "emails": emails}

        except Exception as e:
            logger.debug(f"WebClient scrape_async ошибка: {e}")
            return None
