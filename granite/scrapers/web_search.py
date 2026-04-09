# scrapers/web_search.py — поиск компаний через DuckDuckGo/Google/Bing + BeautifulSoup
# Полная замена FirecrawlScraper без внешних зависимостей.
import re
from urllib.parse import quote_plus, urlparse, parse_qs
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
    get_random_ua,
)
from loguru import logger

import requests


class WebSearchScraper(BaseScraper):
    """Поиск и сбор контактов компаний через поисковики + парсинг сайтов.

    Работает без внешних CLI:
    1. Поиск запросов из конфигурации через DuckDuckGo Lite / Google / Bing
    2. Парсит каждый найденный сайт через requests+BeautifulSoup
    3. Извлекает телефоны, email, адреса
    """

    # Домены, которые не ведут на сайты компаний — пропускаем
    SKIP_DOMAINS = [
        "duckduckgo.com", "google.com", "bing.com", "yandex.ru",
        "youtube.com", "wikipedia.org", "vk.com", "telegram.org",
        "instagram.com", "facebook.com", "ok.ru", "twitter.com",
        "tiktok.com", "avito.ru", "hh.ru", "gismeteo.ru",
        "2gis.ru", "2gis.com",
    ]

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

    def _is_skip_domain(self, url: str) -> bool:
        """Проверяет, нужно ли пропустить URL (каталоги, соцсети)."""
        return any(d in url for d in self.SKIP_DOMAINS)

    def _search_duckduckgo(self, query: str) -> list[dict]:
        """DuckDuckGo Lite HTML search — надёжный endpoint без CAPTCHA.

        Использует https://lite.duckduckgo.com/lite/ — lite-версия
        отдаёт таблицы с прямыми ссылками, без JS-редиректов.
        """
        results = []
        search_url = "https://lite.duckduckgo.com/lite/"
        params = {"q": query, "kl": "ru-ru"}

        try:
            resp = requests.post(
                search_url,
                data=params,
                headers={"User-Agent": get_random_ua()},
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug(f"  WebSearch DDG Lite: status {resp.status_code}")
                return results

            soup = BeautifulSoup(resp.text, "html.parser")

            # Lite-версия: ссылки в тегах <a class="result-link">
            for a_tag in soup.select("a.result-link"):
                url = a_tag.get("href", "")
                title = a_tag.get_text(strip=True)

                if not url or not title:
                    continue
                if not url.startswith(("http://", "https://")):
                    continue
                if self._is_skip_domain(url):
                    continue

                results.append({"url": url, "title": title})

        except Exception as e:
            logger.warning(f"  WebSearch DDG Lite: ошибка — {e}")

        return results[:self.search_limit]

    def _search_duckduckgo_html(self, query: str) -> list[dict]:
        """DuckDuckGo HTML — второй вариант (html.duckduckgo.com/html/)."""
        results = []
        search_url = "https://html.duckduckgo.com/html/"
        params = {"q": query, "kl": "ru-ru"}

        try:
            resp = requests.post(
                search_url,
                data=params,
                headers={"User-Agent": get_random_ua()},
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                return results

            soup = BeautifulSoup(resp.text, "html.parser")

            for result_div in soup.select("div.result"):
                # URL
                url_el = result_div.select_one("a.result__url")
                if not url_el:
                    continue
                url = url_el.get("href", "")

                # DuckDuckGo URL format: "//duckduckgo.com/l/?uddg=...&rut=..."
                # Need to resolve redirect URL
                if "//duckduckgo.com/l/" in url:
                    try:
                        redirect_resp = requests.get(
                            "https:" + url if url.startswith("//") else url,
                            headers={"User-Agent": get_random_ua()},
                            timeout=10,
                            allow_redirects=False,
                        )
                        if redirect_resp.status_code in (301, 302, 303, 307, 308):
                            url = redirect_resp.headers.get("Location", url)
                    except Exception:
                        continue

                if not url or url.startswith("//duckduckgo.com"):
                    continue
                if not url.startswith(("http://", "https://")):
                    continue

                # Title
                title_el = result_div.select_one("a.result__a")
                title = title_el.get_text(strip=True) if title_el else ""

                if not url or not title:
                    continue

                if self._is_skip_domain(url):
                    continue

                results.append({"url": url, "title": title})

        except Exception as e:
            logger.debug(f"  WebSearch DDG HTML: {e}")

        return results[:self.search_limit]

    def _search_google(self, query: str) -> list[dict]:
        """Google SERP — фоллбэк если DuckDuckGo не дал результатов."""
        results = []
        search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={self.search_limit}&hl=ru"

        try:
            html = fetch_page(search_url, timeout=15)
            if not html:
                return results

            # Пропускаем consent/CAPTCHA страницы
            if "consent.google.com" in html or "captcha" in html.lower() or len(html) < 5000:
                logger.debug("  WebSearch Google: CAPTCHA/consent — пропускаем")
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
                if url.startswith("/search") or url.startswith("#"):
                    continue
                if self._is_skip_domain(url):
                    continue

                results.append({"url": url, "title": title})

        except Exception as e:
            logger.debug(f"  WebSearch Google: {e}")

        return results[:self.search_limit]

    def _search_bing(self, query: str) -> list[dict]:
        """Bing search — второй фоллбэк."""
        results = []
        search_url = f"https://www.bing.com/search?q={quote_plus(query)}&count={self.search_limit}&setlang=ru"

        try:
            html = fetch_page(search_url, timeout=15)
            if not html or len(html) < 5000:
                return results

            soup = BeautifulSoup(html, "html.parser")

            for li in soup.select("li.b_algo"):
                anchor = li.find("a", href=True)
                if not anchor:
                    continue

                url = anchor.get("href", "")
                title = anchor.get_text(strip=True)

                if not url or not title:
                    continue
                if self._is_skip_domain(url):
                    continue

                results.append({"url": url, "title": title})

        except Exception as e:
            logger.debug(f"  WebSearch Bing: {e}")

        return results[:self.search_limit]

    def _search(self, query: str) -> list[dict]:
        """Поиск через несколько поисковиков с фоллбэком."""
        # 1. DuckDuckGo Lite (самый надёжный для скрапинга)
        results = self._search_duckduckgo(query)
        if results:
            logger.debug(f"  WebSearch: DDG Lite — {len(results)} результатов")
            return results

        logger.warning(f"  WebSearch: DDG Lite пуст, пробуем DDG HTML")
        adaptive_delay(min_sec=1.0, max_sec=2.0)

        # 2. DuckDuckGo HTML (альтернативный endpoint)
        results = self._search_duckduckgo_html(query)
        if results:
            logger.debug(f"  WebSearch: DDG HTML — {len(results)} результатов")
            return results

        logger.warning(f"  WebSearch: DDG HTML пуст, пробуем Google")
        adaptive_delay(min_sec=1.0, max_sec=2.0)

        # 3. Google
        results = self._search_google(query)
        if results:
            logger.debug(f"  WebSearch: Google — {len(results)} результатов")
            return results

        logger.warning(f"  WebSearch: Google пуст, пробуем Bing")
        adaptive_delay(min_sec=1.0, max_sec=2.0)

        # 4. Bing
        results = self._search_bing(query)
        if results:
            logger.debug(f"  WebSearch: Bing — {len(results)} результатов")
        else:
            logger.warning(f"  WebSearch: все поисковики вернули 0 результатов")
        return results

    def scrape(self) -> list[RawCompany]:
        companies = []
        region_name = self.city_config.get("region", self.city)

        seen_urls = set()

        for query in self.queries:
            search_query = f"{query} {region_name}"
            logger.info(f"  WebSearch: {search_query}")

            web_results = self._search(search_query)
            if not web_results:
                continue

            for item in web_results:
                url = item["url"]
                title = item["title"]
                if not url or not title:
                    continue

                # Дедуп по URL
                if url in seen_urls:
                    continue
                seen_urls.add(url)

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

        return self._extract_contacts(html)

    def _extract_contacts(self, html: str) -> dict | None:
        """Извлечение контактов из HTML."""
        soup = BeautifulSoup(html, "html.parser")

        data_out: dict = {"phones": [], "emails": [], "addresses": []}

        # 1. Телефоны из tel: ссылок
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

        # 2. Email из mailto: ссылок (приоритет — обычно реальные)
        for mailto in soup.select('a[href^="mailto:"]'):
            href = mailto.get("href", "")
            email = href.replace("mailto:", "").strip().split("?")[0]
            if email and email not in data_out["emails"]:
                data_out["emails"].append(email)

        # Email из текста HTML
        html_emails = extract_emails(html)
        for em in html_emails:
            if em not in data_out["emails"]:
                data_out["emails"].append(em)

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

        has_data = data_out["phones"] or data_out["emails"] or data_out["addresses"]
        return data_out if has_data else None
