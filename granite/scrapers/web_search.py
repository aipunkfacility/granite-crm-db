# scrapers/web_search.py — поиск компаний через Google (requests + BeautifulSoup)
# Полная замена FirecrawlScraper без внешних зависимостей.
import re
from urllib.parse import quote_plus, urljoin
from bs4 import BeautifulSoup
from granite.scrapers.base import BaseScraper
from granite.models import RawCompany, Source
from granite.utils import (
    normalize_phones,
    extract_emails,
    extract_domain,
    is_safe_url,
    fetch_page,
    adaptive_delay,
)
from loguru import logger


class WebSearchScraper(BaseScraper):
    """Поиск и сбор контактов компаний через Google SERP + парсинг сайтов.

    Работает без внешних CLI:
    1. Google-поиск запросов из конфигурации → собирает URL + названия
    2. Парсит каждый найденный сайт через requests+BeautifulSoup
    3. Извлекает телефоны, email, адреса
    """

    def __init__(self, config: dict, city: str):
        super().__init__(config, city)
        self.source_config = config.get("sources", {}).get("web_search", {})
        # Fallback: берём queries из firecrawl если web_search не настроен
        if not self.source_config.get("queries"):
            fc_config = config.get("sources", {}).get("firecrawl", {})
            self.queries = fc_config.get("queries", [])
        else:
            self.queries = self.source_config.get("queries", [])
        self.search_limit = self.source_config.get("search_limit", 10)

    def _google_search(self, query: str) -> list[dict]:
        """Парсинг Google SERP через requests. Возвращает [{url, title}, ...]."""
        results = []
        search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={self.search_limit}&hl=ru"

        try:
            html = fetch_page(search_url, timeout=15)
            if not html:
                return results

            soup = BeautifulSoup(html, "html.parser")

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

                results.append({"url": url, "title": title})

        except Exception as e:
            logger.warning(f"  WebSearch: ошибка поиска '{query[:50]}': {e}")

        return results

    def scrape(self) -> list[RawCompany]:
        companies = []
        region_name = self.city_config.get("region", self.city)

        for query in self.queries:
            search_query = f"{query} {region_name}"
            logger.info(f"  WebSearch: {search_query}")

            web_results = self._google_search(search_query)
            if not web_results:
                logger.debug(f"  WebSearch: 0 результатов для '{search_query}'")
                continue

            for item in web_results:
                url = item["url"]
                title = item["title"]
                if not url or not title:
                    continue

                companies.append(
                    RawCompany(
                        source=Source.WEB_SEARCH,
                        source_url=url,
                        name=title,
                        phones=[],
                        address_raw="",
                        website=url,
                        emails=[],
                        city=self.city,
                    )
                )

            adaptive_delay(min_sec=2.0, max_sec=5.0)

        logger.info(f"  WebSearch: найдено {len(companies)} компаний (поиск)")

        # Детальный сбор со всех уникальных сайтов
        seen_domains = set()
        enriched = 0
        for company in companies:
            if not company.website:
                continue
            domain = extract_domain(company.website)
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)

            logger.info(f"  Scrape: {company.website}")
            details = self._scrape_details(company.website)
            if details:
                company.phones = normalize_phones(
                    company.phones + details.get("phones", [])
                )
                company.emails = list(set(company.emails + details.get("emails", [])))
                if not company.address_raw and details.get("addresses"):
                    company.address_raw = details["addresses"][0]
                enriched += 1

            adaptive_delay(min_sec=1.0, max_sec=2.5)

        logger.info(f"  WebSearch: обогащено {enriched}/{len(seen_domains)} сайтов")
        return companies

    def _scrape_details(self, url: str) -> dict | None:
        """Детальный скрапинг сайта через requests + BeautifulSoup."""
        if not is_safe_url(url):
            return None

        try:
            html = fetch_page(url, timeout=15)
            if not html or len(html) < 100:
                return None
        except Exception as e:
            logger.debug(f"  WebSearch: не удалось загрузить {url}: {e}")
            return None

        soup = BeautifulSoup(html, "html.parser")

        data_out: dict = {"phones": [], "emails": [], "addresses": []}

        # 1. Телефоны из HTML
        # Сначала из tel: ссылок (надёжнее)
        for tel_link in soup.select('a[href^="tel:"]'):
            href = tel_link.get("href", "")
            phone = href.replace("tel:", "").strip()
            if phone:
                data_out["phones"].append(phone)

        # Также из текста страницы
        text = soup.get_text(separator=" ")
        text_phones = re.findall(
            r"(\+?7[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2})",
            text,
        )
        for p in text_phones:
            if p not in data_out["phones"]:
                data_out["phones"].append(p)

        # 2. Email из HTML
        data_out["emails"] = extract_emails(html)

        # 3. Адреса
        address_patterns = [
            r"г\.?\s+[А-Яа-яё]+\s*,?\s*ул\.?\s+[А-Яа-яё]+",
            r"г\.?\s+[А-Яа-яё]+\s*,?\s*[А-Яа-яё]+\s+\d+",
        ]
        for pattern in address_patterns:
            found = re.findall(pattern, text)
            for addr in found:
                if addr not in data_out["addresses"]:
                    data_out["addresses"].append(addr)

        # Также ищем email из mailto: ссылок
        for mailto in soup.select('a[href^="mailto:"]'):
            href = mailto.get("href", "")
            email = href.replace("mailto:", "").strip().split("?")[0]
            if email and email not in data_out["emails"]:
                data_out["emails"].append(email)

        has_data = data_out["phones"] or data_out["emails"] or data_out["addresses"]
        return data_out if has_data else None
