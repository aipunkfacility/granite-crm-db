# pipeline/web_client.py
"""Клиент для поиска и скрапинга сайтов (requests + BeautifulSoup).

Полная замена firecrawl_client.py — не требует внешних CLI или API-ключей.
Использует Google SERP для поиска и requests+BeautifulSoup для парсинга.
"""

import re
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from loguru import logger
from granite.utils import extract_emails, is_safe_url, _sanitize_url_for_log, fetch_page, adaptive_delay

MIN_CONTENT_LENGTH = 100


class WebClient:
    """Обёртка для веб-поиска и скрапинга (search + scrape).

    Совместим по интерфейсу с FirecrawlClient для безболезненной замены.
    """

    def __init__(
        self, timeout: int = 60, search_limit: int = 3
    ):
        self.timeout = timeout
        self.search_limit = search_limit

    def search(self, query: str) -> dict | None:
        """Поиск через Google SERP.

        Returns:
            dict с ключом "data.web" — список результатов, или None.
            Формат совместим с firecrawl search output.
        """
        try:
            search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={self.search_limit}&hl=ru"
            html = fetch_page(search_url, timeout=15)

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
            logger.debug(f"WebClient search ошибка: {e}")
            return None

    def scrape(self, url: str) -> dict | None:
        """Скрапинг сайта через requests + BeautifulSoup.

        Returns:
            {"phones": [...], "emails": [...]} или None.
            Формат совместим с firecrawl scrape output.
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
            text_phones = re.findall(
                r"(\+?7[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2})",
                text,
            )
            for p in text_phones:
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
