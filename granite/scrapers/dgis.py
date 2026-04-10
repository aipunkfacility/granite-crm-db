# scrapers/dgis.py — Crawlee-based 2GIS scraper (Phase 7)
"""Скрепер 2GIS через Crawlee + 2GIS Catalog API.

Два режима:
1. 2GIS API (приоритет) — httpx, если есть DGIS_API_KEY.
   Пагинация через параметр page, до max_pages.
2. Crawlee BeautifulSoupCrawler — fallback, парсинг страниц поиска.

Извлекает: название, телефоны, адрес, сайт, email, мессенджеры, гео, рейтинг.
"""

import asyncio
import os
import re
import time
from urllib.parse import quote, urljoin

import httpx
from loguru import logger

from granite.scrapers.base import BaseScraper
from granite.models import RawCompany, Source
from granite.utils import (
    normalize_phones,
    extract_emails,
    extract_phones,
    slugify,
    adaptive_delay,
)


# ===== 2GIS region_id mapping =====
# Совпадает со словарём из reverse_lookup.py. В идеале — вынести в общую утилиту,
# но для Phase 7 оставляем здесь для независимости модулей.

DGIS_REGION_IDS: dict[str, int] = {
    "москва": 32,
    "санкт-петербург": 49,
    "новосибирск": 131,
    "екатеринбург": 81,
    "казань": 72,
    "нижний новгород": 115,
    "красноярск": 54,
    "челябинск": 143,
    "уфа": 105,
    "самара": 124,
    "ростов-на-дону": 111,
    "краснодар": 40,
    "омск": 103,
    "воронеж": 50,
    "пермь": 109,
    "волгоград": 48,
    "саратов": 120,
    "тюмень": 134,
    "тольятти": 125,
    "махачкала": 62,
    "барнаул": 25,
    "ижевск": 56,
    "хабаровск": 147,
    "ульяновск": 140,
    "иркутск": 53,
    "владивосток": 46,
    "ярославль": 153,
    "севастополь": 122,
    "сочи": 121,
    "кемерово": 67,
    "томск": 130,
    "ставрополь": 119,
    "набережные челны": 96,
    "тула": 136,
    "оренбург": 104,
    "новокузнецк": 100,
    "балашиха": 20,
    "рязань": 114,
    "киров": 68,
    "чебоксары": 144,
    "калининград": 63,
    "пенза": 107,
    "липецк": 84,
    "астрахань": 22,
    # Малые города → region_id области
    "тара": 131,
    "исилькуль": 131,
    "калачинск": 131,
    "называевск": 131,
    "тюкалинск": 131,
}


def _get_dgis_region_id(city: str) -> str:
    """Получить 2GIS region_id для города.

    Returns:
        Строку с region_id или пустую строку (глобальный поиск).
    """
    city_lower = city.lower().strip()
    rid = DGIS_REGION_IDS.get(city_lower)
    if rid:
        return str(rid)
    return ""


class DgisScraper(BaseScraper):
    """Скрепер 2GIS через Crawlee + 2GIS Catalog API.

    Управляет собственным HTTP-клиентом / Crawlee-браузером.
    НЕ требует передачи playwright_page (в отличие от старой версии).
    Параметр playwright_page принимается для обратной совместимости, но игнорируется.

    Config (sources.dgis):
        enabled: bool
        api_key: str (из .env: DGIS_API_KEY)
        search_category: str (default: "изготовление памятников")
        max_pages: int (default: 5)
        delay: float (default: 1.5, секунд между страницами)
    """

    def __init__(
        self,
        config: dict,
        city: str,
        playwright_page=None,  # backward compat — ignored
    ):
        super().__init__(config, city)
        self.source_config = config.get("sources", {}).get("dgis", {})
        self.search_category = self.source_config.get(
            "search_category", "изготовление памятников"
        )
        self.max_pages = self.source_config.get("max_pages", 5)
        self._delay = self.source_config.get("delay", 1.5)
        # API key: config > env
        self.api_key = self.source_config.get("api_key", "")
        if not self.api_key:
            self.api_key = os.environ.get("DGIS_API_KEY", "")

    # ─────────────────────────────────────────────
    # Public API (BaseScraper)
    # ─────────────────────────────────────────────

    def scrape(self) -> list[RawCompany]:
        """Основной метод: выбрать стратегию и запустить."""
        if self.api_key:
            logger.info(f"  2GIS: API mode (ключ установлен)")
            return self._scrape_api()
        else:
            logger.info(f"  2GIS: Crawlee mode (нет API ключа)")
            return self._scrape_crawlee()

    # ─────────────────────────────────────────────
    # Strategy 1: 2GIS Catalog API
    # ─────────────────────────────────────────────

    def _scrape_api(self) -> list[RawCompany]:
        """Массовый поиск через 2GIS Catalog API с пагинацией.

        Returns:
            Список RawCompany.
        """
        companies: list[RawCompany] = []
        region_id = _get_dgis_region_id(self.city)
        page = 1
        total_fetched = 0

        url = "https://catalog.api.2gis.ru/3.0/items"
        base_params: dict = {
            "q": self.search_category,
            "key": self.api_key,
            "fields": (
                "items.contact_groups,items.point,items.articles,"
                "items.rating,items.schedule,items.name_synonyms"
            ),
            "page_size": 30,
        }
        if region_id:
            base_params["region_id"] = region_id

        while page <= self.max_pages:
            params = {**base_params, "page": page}
            logger.info(f"  2GIS API: страница {page}/{self.max_pages}")

            try:
                with httpx.Client(timeout=20) as client:
                    resp = client.get(url, params=params)
                    status = resp.status_code

                    # Anti-bot: при 403/429 — пауза и retry
                    if status in (403, 429):
                        wait = 30 + (page - 1) * 10  # escalating backoff
                        logger.warning(
                            f"  2GIS API: {status}, ждём {wait}с..."
                        )
                        time.sleep(wait)
                        continue

                    if status != 200:
                        logger.warning(f"  2GIS API: ошибка {status}")
                        break

                    data = resp.json()
                    result = data.get("result", {})
                    items = result.get("items", [])

                    if not items:
                        logger.info("  2GIS API: пустая страница, завершаем")
                        break

                    for item in items:
                        company = self._parse_api_item(item)
                        if company:
                            companies.append(company)

                    total_fetched += len(items)
                    total = result.get("total", 0)
                    logger.info(
                        f"  2GIS API: +{len(items)} (всего {total_fetched}/{total})"
                    )

                    if total_fetched >= total:
                        break

                    page += 1
                    # Адаптивная задержка между страницами
                    adaptive_delay(self._delay, self._delay * 1.5)

            except httpx.TimeoutException:
                logger.warning("  2GIS API: таймаут, пробуем следующую страницу")
                page += 1
            except Exception as e:
                logger.error(f"  2GIS API ошибка: {e}")
                break

        logger.info(f"  2GIS: всего {len(companies)} компаний")
        return companies

    def _parse_api_item(self, item: dict) -> RawCompany | None:
        """Парсинг одного элемента из ответа 2GIS API.

        Returns:
            RawCompany или None (если имя слишком короткое / нет данных).
        """
        name = (item.get("name") or "").strip()
        if not name or len(name) < 3:
            return None

        phones: list[str] = []
        emails: list[str] = []
        website: str | None = None
        messengers: dict[str, str] = {}
        address = ""
        geo: list[float] | None = None

        # ── Контактные группы ──
        for group in item.get("contact_groups", []):
            for contact in group.get("contacts", []):
                ctype = contact.get("type", "")
                cvalue = contact.get("value", "")

                if ctype == "phone" and cvalue:
                    phones.extend(normalize_phones([cvalue]))
                elif ctype == "email" and cvalue:
                    emails.append(cvalue)
                elif ctype == "website" and cvalue:
                    if not cvalue.startswith(("http://", "https://")):
                        cvalue = f"https://{cvalue}"
                    if not website:
                        website = cvalue
                elif ctype == "telegram" and cvalue and "telegram" not in messengers:
                    messengers["telegram"] = cvalue
                elif ctype == "whatsapp" and cvalue and "whatsapp" not in messengers:
                    messengers["whatsapp"] = cvalue
                elif ctype == "vk" and cvalue and "vk" not in messengers:
                    messengers["vk"] = cvalue

        # ── Адрес ──
        address = (
            item.get("address_name")
            or item.get("full_address_name")
            or ""
        )

        # ── Гео-координаты ──
        point = item.get("point")
        if isinstance(point, dict):
            try:
                lat = float(point.get("lat", 0))
                lon = float(point.get("lon", 0))
                if lat != 0 and lon != 0:
                    geo = [lat, lon]
            except (ValueError, TypeError):
                pass

        # ── Source URL ──
        firm_id = item.get("id", "")
        source_url = f"https://2gis.ru/{_get_dgis_region_id(self.city)}/firm/{firm_id}" if firm_id else ""

        return RawCompany(
            source=Source.DGIS,
            source_url=source_url,
            name=name,
            phones=phones,
            address_raw=address,
            website=website,
            emails=emails,
            geo=geo,
            city=self.city,
            messengers=messengers,
        )

    # ─────────────────────────────────────────────
    # Strategy 2: Crawlee BeautifulSoupCrawler
    # ─────────────────────────────────────────────

    def _scrape_crawlee(self) -> list[RawCompany]:
        """Fallback: Crawlee BeautifulSoupCrawler для парсинга 2GIS.

        Используется когда нет API ключа. Парсит страницу результатов поиска
        и извлекает данные из карточек организаций.

        Returns:
            Список RawCompany.
        """
        city_slug = slugify(self.city)
        encoded_query = quote(self.search_category)
        url = f"https://2gis.ru/{city_slug}/search/{encoded_query}"
        logger.info(f"  2GIS Crawlee: {url}")

        try:
            return asyncio.run(self._async_crawlee_scrape(url))
        except Exception as e:
            logger.error(f"  2GIS Crawlee ошибка: {e}")
            return []

    async def _async_crawlee_scrape(self, start_url: str) -> list[RawCompany]:
        """Async Crawlee: парсинг результатов поиска 2GIS."""
        from crawlee.crawlers import BeautifulSoupCrawler

        companies: list[RawCompany] = []
        seen_names: set[str] = set()  # дедупликация по имени

        async def handler(context):
            soup = context.soup
            if not soup:
                return

            # 2GIS React: карточки организаций содержат ссылки /firm/
            cards = soup.find_all("a", href=re.compile(r"/firm/"))

            for card in cards:
                try:
                    name = card.get_text(strip=True)
                    if not name or len(name) < 3:
                        continue

                    # Дедупликация по имени
                    name_lower = name.lower()
                    if name_lower in seen_names:
                        continue
                    seen_names.add(name_lower)

                    href = card.get("href", "")
                    source_url = urljoin("https://2gis.ru", href) if href else ""

                    # Родительский контейнер для адреса/телефона
                    parent = card.find_parent()
                    if not parent:
                        parent = card

                    parent_text = parent.get_text(separator=" ")
                    phones = normalize_phones(extract_phones(parent_text))
                    emails = extract_emails(parent_text)

                    # Мессенджеры из ссылок
                    messengers: dict[str, str] = {}
                    for a in parent.find_all("a", href=True):
                        h = a["href"]
                        if "vk.com" in h and "vk" not in messengers:
                            messengers["vk"] = h
                        elif "t.me" in h and "share" not in h and "telegram" not in messengers:
                            messengers["telegram"] = h
                        elif "instagram.com" in h and "instagram" not in messengers:
                            messengers["instagram"] = h

                    # Адрес: ищем элемент с классом содержащим "address"
                    address = ""
                    for elem in parent.find_all(class_=re.compile(r"address", re.I)):
                        addr_text = elem.get_text(strip=True)
                        if addr_text and len(addr_text) > 10:
                            address = addr_text
                            break

                    companies.append(RawCompany(
                        source=Source.DGIS,
                        source_url=source_url,
                        name=name,
                        phones=phones,
                        address_raw=address,
                        website=None,
                        emails=emails,
                        city=self.city,
                        messengers=messengers,
                    ))

                except Exception as e:
                    logger.debug(f"  2GIS: пропущена карточка: {e}")
                    continue

        crawler = BeautifulSoupCrawler(
            request_handler=handler,
            max_requests_per_crawl=1,
        )
        await crawler.run([start_url])
        return companies
