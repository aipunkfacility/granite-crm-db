# enrichers/reverse_lookup.py
"""Reverse Lookup: поиск компании в 2GIS и Yell по имени/телефону.

Используется ПОСЛЕ основного обогащения, для компаний где:
- Нет мессенджеров (TG, WA, VK)
- Нет email
- Мало данных в целом (CRM-скор < min_crm_score)

Вход: EnrichedCompanyRow из БД.
Выход: обновлённые messengers, phones, emails, website.
"""

import asyncio
import os
import re
import time
from urllib.parse import quote

import httpx
from loguru import logger

from granite.database import Database, EnrichedCompanyRow
from granite.utils import normalize_phones, normalize_phone, extract_emails, extract_phones, slugify
from granite.pipeline.status import print_status
from granite.scrapers.dgis_constants import get_dgis_region_id, DGIS_REGION_IDS


def _run_async(coro):
    """Безопасный запуск coroutine из sync-контекста.

    Если уже есть запущенный event loop (например в ThreadPoolExecutor
    внутри async enrichment), использует loop.run_until_complete().
    Иначе — asyncio.run().

    Предотвращает RuntimeError: 'This event loop is already running'
    при вложенных вызовах.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Создаём новый поток с собственным event loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)



# ===== Reverse Lookup Enricher =====


class ReverseLookupEnricher:
    """Ищет компанию в 2GIS и Yell по имени/телефону.

    Используется ПОСЛЕ основного обогащения, для компаний где:
    - Нет мессенджеров (TG, WA, VK)
    - Нет email
    - Мало данных в целом (CRM-скор < min_crm_score)
    """

    def __init__(self, config: dict, db: Database):
        self.config = config
        self.db = db

        rl_config = config.get("enrichment", {}).get("reverse_lookup", {})
        self._enabled = rl_config.get("enabled", False)
        self._min_crm_score = rl_config.get("min_crm_score", 30)
        self._delay = rl_config.get("delay_between_requests", 2.0)

        # 2GIS config
        dgis_cfg = rl_config.get("sources", {}).get("dgis", {})
        self._dgis_enabled = dgis_cfg.get("enabled", True)
        self._dgis_api_key = dgis_cfg.get("api_key", "")
        self._dgis_max_per_day = dgis_cfg.get("max_requests_per_day", 100)
        # Load API key from env if not in config
        if not self._dgis_api_key:
            self._dgis_api_key = os.environ.get("DGIS_API_KEY", "")

        # Yell config
        yell_cfg = rl_config.get("sources", {}).get("yell", {})
        self._yell_enabled = yell_cfg.get("enabled", True)
        self._yell_max_per_day = yell_cfg.get("max_requests_per_day", 50)

        # Request counters (per day)
        self._dgis_requests_today = 0
        self._yell_requests_today = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def run(self, city: str) -> int:
        """Запуск reverse lookup для города.

        Returns:
            Количество компаний с новыми данными.
        """
        if not self._enabled:
            logger.info("Reverse lookup отключён в конфигурации")
            return 0

        print_status("Reverse Lookup: поиск в 2GIS и Yell", "info")

        # Фильтруем кандидатов
        candidates = self._get_candidates(city)
        if not candidates:
            print_status("Reverse lookup: нет кандидатов (все компании с данными)", "info")
            return 0

        print_status(
            f"Reverse lookup: {len(candidates)} кандидатов для обогащения",
            "info",
        )

        enriched_count = 0
        for i, company in enumerate(candidates, 1):
            try:
                updated = self._enrich_one(company)
                if updated:
                    enriched_count += 1
                    logger.info(
                        f"  [{i}/{len(candidates)}] ✓ {company.name}: "
                        f"найдено {', '.join(updated)}"
                    )
                else:
                    logger.debug(
                        f"  [{i}/{len(candidates)}] — {company.name}: ничего нового"
                    )
            except Exception as e:
                logger.exception(
                    f"Reverse lookup ошибка для {company.name}: {e}"
                )

            # Rate limiting
            if i < len(candidates):
                self._apply_delay()

        print_status(
            f"Reverse lookup: дополнено {enriched_count}/{len(candidates)}",
            "success",
        )
        return enriched_count

    def _get_candidates(self, city: str) -> list[EnrichedCompanyRow]:
        """Выбрать компании-кандидаты для reverse lookup.

        Условия: нет мессенджеров, нет email, crm_score < min_crm_score.

        Returns:
            Список EnrichedCompanyRow.
        """
        candidates = []
        with self.db.session_scope() as session:
            companies = session.query(EnrichedCompanyRow).filter_by(city=city).all()

            for c in companies:
                # Нет мессенджеров
                messengers = c.messengers or {}
                if messengers:
                    continue
                # Нет email
                if c.emails:
                    continue
                # CRM score ниже порога (или 0)
                if (c.crm_score or 0) >= self._min_crm_score:
                    continue

                candidates.append(c)

        return candidates

    def _enrich_one(self, company: EnrichedCompanyRow) -> list[str]:
        """Обогащение одной компании через 2GIS и Yell.

        Returns:
            Список обновлённых полей (например ["phones", "website"]).
        """
        updated = []

        # Формируем поисковый запрос
        query_name = f"{company.name} {company.city}"
        query_phone = company.phones[0] if company.phones else None

        # 1. 2GIS API (если есть API ключ)
        if self._dgis_enabled and self._dgis_api_key:
            if self._dgis_requests_today < self._dgis_max_per_day:
                try:
                    dgis_data = self._query_dgis_api(query_name, company.city)
                    if dgis_data:
                        fields = self._merge_data(company, dgis_data)
                        updated.extend(fields)
                except Exception as e:
                    logger.debug(f"2GIS API ошибка для {company.name}: {e}")
                self._dgis_requests_today += 1

        # 2. 2GIS Crawlee fallback (если API не дал результатов или нет ключа)
        if self._dgis_enabled and not updated and self._dgis_requests_today < self._dgis_max_per_day:
            try:
                dgis_data = self._query_dgis_crawlee(query_name, company.city)
                if dgis_data:
                    fields = self._merge_data(company, dgis_data)
                    updated.extend(fields)
            except Exception as e:
                logger.debug(f"2GIS Crawlee fallback ошибка для {company.name}: {e}")
            self._dgis_requests_today += 1

        # 3. Yell Crawlee (Playwright)
        if self._yell_enabled and self._yell_requests_today < self._yell_max_per_day:
            try:
                query = query_phone if query_phone else query_name
                yell_data = self._query_yell_crawlee(query)
                if yell_data:
                    fields = self._merge_data(company, yell_data)
                    updated.extend(fields)
            except Exception as e:
                logger.debug(f"Yell Crawlee ошибка для {company.name}: {e}")
            self._yell_requests_today += 1

        # Записываем обновления в БД
        if updated:
            self._save_updates(company, updated)

        return list(set(updated))  # уникальные поля

    def _query_dgis_api(self, query: str, city: str) -> dict | None:
        """Поиск через 2GIS Catalog API.

        Returns:
            Словарь с найденными данными или None.
        """
        region_id = get_dgis_region_id(city)
        url = "https://catalog.api.2gis.ru/3.0/items"
        params = {
            "q": query,
            "key": self._dgis_api_key,
            "fields": "items.contact_groups,items.point,items.articles",
        }
        if region_id:
            params["region_id"] = region_id

        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(url, params=params)
                if resp.status_code != 200:
                    logger.debug(f"2GIS API status {resp.status_code}")
                    return None

                data = resp.json()
                items = data.get("result", {}).get("items", [])
                if not items:
                    return None

                # Берём первый результат (самый релевантный)
                item = items[0]
                return self._parse_dgis_api_item(item)

        except Exception as e:
            logger.debug(f"2GIS API request failed: {e}")
            return None

    def _parse_dgis_api_item(self, item: dict) -> dict:
        """Парсинг одного элемента из ответа 2GIS API.

        Returns:
            Словарь: {phones, website, email, address, messengers}.
        """
        result: dict = {
            "phones": [],
            "website": None,
            "email": None,
            "address": "",
            "messengers": {},
        }

        # Контактные группы (телефоны, email, сайт, мессенджеры)
        contact_groups = item.get("contact_groups", [])
        for group in contact_groups:
            contacts = group.get("contacts", [])
            for contact in contacts:
                ctype = contact.get("type", "")
                cvalue = contact.get("value", "")

                if ctype == "phone" and cvalue:
                    phones = normalize_phones([cvalue])
                    result["phones"].extend(phones)
                elif ctype == "email" and cvalue:
                    result["email"] = cvalue
                elif ctype == "website" and cvalue:
                    if not cvalue.startswith(("http://", "https://")):
                        cvalue = f"https://{cvalue}"
                    result["website"] = cvalue
                elif ctype == "telegram" and cvalue:
                    result["messengers"]["telegram"] = cvalue
                elif ctype == "whatsapp" and cvalue:
                    result["messengers"]["whatsapp"] = cvalue
                elif ctype == "vk" and cvalue:
                    result["messengers"]["vk"] = cvalue

        # Адрес
        address = item.get("address_name", "") or ""
        if not address:
            # Формируем из full_address_parts
            addr_parts = item.get("full_address_name", "") or ""
            if addr_parts:
                address = addr_parts
        result["address"] = address

        return result

    def _query_dgis_crawlee(self, query: str, city: str) -> dict | None:
        """Поиск через 2GIS (BeautifulSoupCrawler fallback).

        Returns:
            Словарь с найденными данными или None.
        """
        city_slug = slugify(city)
        encoded_query = quote(query)
        url = f"https://2gis.ru/{city_slug}/search/{encoded_query}"

        try:
            return _run_async(self._async_dgis_crawlee(url))
        except Exception as e:
            logger.debug(f"2GIS Crawlee error: {e}")
            return None

    async def _async_dgis_crawlee(self, url: str) -> dict | None:
        """Async Crawlee fallback для 2GIS (BeautifulSoupCrawler)."""
        from crawlee.crawlers import BeautifulSoupCrawler

        result_data: dict | None = None

        async def request_handler(context):
            nonlocal result_data
            soup = context.soup
            if not soup:
                return

            result_data = {"phones": [], "website": None, "email": None,
                           "address": "", "messengers": {}}

            # Извлекаем телефон
            text = soup.get_text(separator=" ")
            phones = extract_phones(text)
            result_data["phones"] = normalize_phones(phones)

            # Извлекаем email
            emails = extract_emails(text)
            if emails:
                result_data["email"] = emails[0]

            # Извлекаем ссылки на мессенджеры
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if "vk.com" in href and "vk" not in result_data["messengers"]:
                    result_data["messengers"]["vk"] = href
                elif "t.me" in href and "telegram" not in result_data["messengers"]:
                    if "share" not in href and "joinchat" not in href:
                        result_data["messengers"]["telegram"] = href

            # Адрес
            for elem in soup.find_all(class_=re.compile(r"address", re.I)):
                addr_text = elem.get_text(strip=True)
                if addr_text and len(addr_text) > 10:
                    result_data["address"] = addr_text
                    break

        crawler = BeautifulSoupCrawler(
            request_handler=request_handler,
            max_requests_per_crawl=1,
        )

        await crawler.run([url])
        return result_data

    def _query_yell_crawlee(self, query: str) -> dict | None:
        """Поиск через Yell (PlaywrightCrawler).

        Returns:
            Словарь с найденными данными или None.
        """
        encoded_query = quote(query)
        url = f"https://www.yell.ru/search?text={encoded_query}"

        try:
            return _run_async(self._async_yell_crawlee(url))
        except Exception as e:
            logger.debug(f"Yell Crawlee error: {e}")
            return None

    async def _async_yell_crawlee(self, url: str) -> dict | None:
        """Async Crawlee для Yell (PlaywrightCrawler)."""
        from crawlee.crawlers import PlaywrightCrawler

        result_data: dict | None = None

        async def request_handler(context):
            nonlocal result_data
            page = context.page
            if not page:
                return

            result_data = {"phones": [], "website": None, "email": None,
                           "address": "", "messengers": {}}

            # Ждём загрузку результатов
            await page.wait_for_load_state("domcontentloaded", timeout=15000)

            text = await page.inner_text("body")
            phones = extract_phones(text)
            result_data["phones"] = normalize_phones(phones)

            emails = extract_emails(text)
            if emails:
                result_data["email"] = emails[0]

            # Мессенджеры из ссылок
            links = await page.query_selector_all("a[href]")
            for link in links[:50]:
                try:
                    href = await link.get_attribute("href") or ""
                    if "vk.com" in href and "vk" not in result_data["messengers"]:
                        result_data["messengers"]["vk"] = href
                    elif "t.me" in href and "telegram" not in result_data["messengers"]:
                        if "share" not in href and "joinchat" not in href:
                            result_data["messengers"]["telegram"] = href
                    elif "wa.me" in href and "whatsapp" not in result_data["messengers"]:
                        result_data["messengers"]["whatsapp"] = href
                except Exception:
                    continue

            # Сайт из результатов
            link_elems = await page.query_selector_all("a[href]")
            for link_elem in link_elems[:20]:
                try:
                    href = await link_elem.get_attribute("href") or ""
                    if href and not any(x in href for x in ["yell.ru", "vk.com", "t.me", "wa.me"]):
                        if href.startswith("http") and result_data["website"] is None:
                            result_data["website"] = href
                            break
                except Exception:
                    continue

        crawler = PlaywrightCrawler(
            request_handler=request_handler,
            max_requests_per_crawl=1,
            headless=True,
        )

        await crawler.run([url])
        return result_data

    def _merge_data(
        self, company: EnrichedCompanyRow, new_data: dict
    ) -> list[str]:
        """Слияние новых данных с существующими (union, без перезаписи).

        Returns:
            Список обновлённых полей.
        """
        updated = []

        # Phones (union)
        new_phones = new_data.get("phones", [])
        if new_phones:
            existing = set(company.phones or [])
            for p in new_phones:
                if p and p not in existing:
                    existing.add(p)
            if len(existing) > len(company.phones or []):
                company.phones = list(existing)
                updated.append("phones")

        # Website (не перезаписываем существующий)
        new_website = new_data.get("website")
        if new_website and not company.website:
            company.website = new_website
            updated.append("website")

        # Email (union)
        new_email = new_data.get("email")
        if new_email:
            existing_emails = set(company.emails or [])
            if new_email not in existing_emails:
                existing_emails.add(new_email)
                company.emails = list(existing_emails)
                updated.append("emails")

        # Messengers (union, без перезаписи)
        new_messengers = new_data.get("messengers", {})
        if new_messengers:
            existing_msg = dict(company.messengers or {})
            for k, v in new_messengers.items():
                if k not in existing_msg:
                    existing_msg[k] = v
                    updated.append(k)
            company.messengers = existing_msg

        # Address (не перезаписываем)
        new_address = new_data.get("address", "")
        if new_address and not company.address_raw:
            company.address_raw = new_address
            updated.append("address")

        return updated

    def _save_updates(self, company: EnrichedCompanyRow, updated_fields: list[str]) -> None:
        """Запись обновлений в БД."""
        try:
            with self.db.session_scope() as session:
                # Re-fetch to get managed state
                erow = session.get(EnrichedCompanyRow, company.id)
                if erow:
                    if "phones" in updated_fields:
                        erow.phones = company.phones
                    if "website" in updated_fields:
                        erow.website = company.website
                    if "emails" in updated_fields:
                        erow.emails = company.emails
                    if "address" in updated_fields:
                        erow.address_raw = company.address_raw
                    for key in ("telegram", "whatsapp", "vk"):
                        if key in updated_fields:
                            msg = dict(erow.messengers or {})
                            msg.update(company.messengers or {})
                            erow.messengers = msg
                            break

                    # Также обновляем CompanyRow
                    from granite.database import CompanyRow
                    crow = session.get(CompanyRow, company.id)
                    if crow:
                        if "phones" in updated_fields:
                            crow.phones = company.phones
                        if "website" in updated_fields:
                            crow.website = company.website
                        if "emails" in updated_fields:
                            crow.emails = company.emails
                        if company.messengers:
                            msg = dict(crow.messengers or {})
                            msg.update(company.messengers or {})
                            crow.messengers = msg
        except Exception as e:
            logger.warning(f"Не удалось сохранить обновления для {company.name}: {e}")

    def _apply_delay(self) -> None:
        """Задержка между запросами (rate limiting)."""
        delay = self._delay
        # Small random jitter ±30%
        import random
        jitter = delay * 0.3 * (random.random() * 2 - 1)
        actual = max(0.5, delay + jitter)
        time.sleep(actual)
