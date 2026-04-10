# scrapers/yell.py — Crawlee-based Yell scraper (Phase 7)
"""Скрепер yell.ru через Crawlee PlaywrightCrawler.

Управляет собственным браузером через Crawlee (НЕ требует внешнего playwright_page).
Поддерживает категории от category_finder или fallback из config.
Извлекает: название, телефоны, адрес, сайт, email, мессенджеры.
Пагинация: клик по кнопке «Показать ещё» (до max_pages).
"""

import asyncio
import random
import re
from urllib.parse import quote

from loguru import logger

from granite.scrapers.base import BaseScraper
from granite.models import RawCompany, Source
from granite.utils import (
    normalize_phones,
    extract_emails,
    extract_phones,
    slugify,
)


class YellScraper(BaseScraper):
    """Скрепер yell.ru через Crawlee PlaywrightCrawler.

    Управляет собственным браузером через Crawlee.
    Параметр playwright_page принимается для обратной совместимости, но игнорируется.

    Config (sources.yell):
        enabled: bool
        base_path: str (fallback если категории не найдены)
        max_pages: int (default: 5)
        delay: float (default: 2.0, секунд между действиями)
    """

    def __init__(
        self,
        config: dict,
        city: str,
        playwright_page=None,  # backward compat — ignored
        categories: list[str] | None = None,
    ):
        super().__init__(config, city)
        self.source_config = config.get("sources", {}).get("yell", {})
        self.categories = categories  # от category_finder: ["/catalog/izgotovlenie-pamyatnikov", ...]
        self.base_path = self.source_config.get("base_path")
        self.max_pages = self.source_config.get("max_pages", 5)
        self._delay = self.source_config.get("delay", 2.0)

    # ─────────────────────────────────────────────
    # Public API (BaseScraper)
    # ─────────────────────────────────────────────

    def scrape(self) -> list[RawCompany]:
        """Запуск скрапинга Yell для города.

        Returns:
            Список RawCompany.
        """
        urls = self._get_urls()
        if not urls:
            logger.warning("  Yell: нет URL для парсинга, пропуск")
            return []

        all_companies: list[RawCompany] = []
        for url in urls:
            logger.info(f"  Yell Crawlee: {url}")
            try:
                companies = asyncio.run(
                    self._async_crawlee_scrape(url)
                )
                all_companies.extend(companies)
                logger.info(f"  Yell: +{len(companies)} компаний с {url}")
            except Exception as e:
                logger.error(f"  Yell Crawlee ошибка ({url}): {e}")

        return all_companies

    # ─────────────────────────────────────────────
    # URL generation
    # ─────────────────────────────────────────────

    def _get_urls(self) -> list[str]:
        """Список URL для парсинга.

        Приоритет: categories (от category_finder) > base_path (из config).
        """
        urls: list[str] = []

        if self.categories:
            for cat in self.categories:
                if not cat.startswith("/"):
                    continue
                urls.append(f"https://www.yell.ru{cat}/".rstrip("/"))

        elif self.base_path:
            city_slug = slugify(self.city)
            path = self.base_path.replace("{city_slug}", city_slug)
            urls.append(f"https://www.yell.ru{path}")

        return urls

    # ─────────────────────────────────────────────
    # Crawlee PlaywrightCrawler
    # ─────────────────────────────────────────────

    async def _async_crawlee_scrape(self, start_url: str) -> list[RawCompany]:
        """Async Crawlee: парсинг результатов поиска Yell.

        Логика:
        1. Загрузить страницу результатов.
        2. Извлечь карточки компаний.
        3. Кликнуть «Показать ещё» (пагинация).
        4. Повторить до max_pages.
        """
        from crawlee.crawlers import PlaywrightCrawler

        companies: list[RawCompany] = []
        seen_names: set[str] = set()  # дедупликация по имени
        pages_loaded = 0

        async def handler(context):
            nonlocal pages_loaded

            page = context.page
            if not page:
                return

            # Ждём загрузки
            await page.wait_for_load_state("domcontentloaded", timeout=20000)

            # Извлекаем карточки с текущей страницы
            page_companies = await self._extract_companies(page, seen_names)
            companies.extend(page_companies)
            pages_loaded += 1

            # Пагинация: клик «Показать ещё»
            for _ in range(self.max_pages - 1):
                try:
                    # Ищем кнопку пагинации
                    show_more = page.locator(
                        "button:has-text('Показать ещё'), "
                        "a:has-text('Показать ещё'), "
                        "div:has-text('Показать ещё')"
                    ).first

                    if await show_more.count() == 0:
                        logger.debug("  Yell: кнопка «Показать ещё» не найдена")
                        break

                    # Скролл к кнопке
                    await show_more.scroll_into_view_if_needed()
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                    # Клик
                    await show_more.click(timeout=10000)
                    # Ждём загрузки новых результатов
                    await page.wait_for_timeout(2000 + random.randint(500, 1500))

                    pages_loaded += 1
                    new_companies = await self._extract_companies(page, seen_names)
                    companies.extend(new_companies)

                    if not new_companies:
                        logger.debug("  Yell: нет новых компаний, завершаем пагинацию")
                        break

                    logger.info(
                        f"  Yell: страница {pages_loaded}, "
                        f"всего {len(companies)}"
                    )

                except Exception as e:
                    logger.debug(f"  Yell: пагинация прервана: {e}")
                    break

        crawler = PlaywrightCrawler(
            request_handler=handler,
            max_requests_per_crawl=1,
            headless=True,
        )

        await crawler.run([start_url])
        return companies

    async def _extract_companies(
        self, page, seen_names: set[str]
    ) -> list[RawCompany]:
        """Извлечь карточки компаний со страницы Yell.

        Args:
            page: Playwright page object.
            seen_names: Множество уже обработанных имён (для дедупликации).

        Returns:
            Список новых RawCompany.
        """
        companies: list[RawCompany] = []

        # Селекторы карточек Yell — несколько вариантов для устойчивости
        card_selectors = [
            "[class*='company-card']",
            "[class*='listing-item']",
            "[class*='search-card']",
            "[class*='org-card']",
            "a[href*='/company/']",
        ]
        card_selector = ", ".join(card_selectors)

        cards = await page.query_selector_all(card_selector)
        logger.debug(f"  Yell: найдено {len(cards)} элементов")

        for card in cards:
            try:
                # ── Название ──
                name_elem = await card.query_selector(
                    "h3 a, h2 a, a[class*='name'], "
                    "a[class*='title'], span[class*='name'], "
                    "a[class*='company']"
                )
                if not name_elem:
                    continue

                name = (await name_elem.inner_text()).strip()
                if not name or len(name) < 3:
                    continue

                # Дедупликация по имени
                name_lower = name.lower()
                if name_lower in seen_names:
                    continue
                seen_names.add(name_lower)

                # ── Адрес ──
                address = ""
                addr_elem = await card.query_selector(
                    "address, [class*='address'], span[class*='address']"
                )
                if addr_elem:
                    address = (await addr_elem.inner_text()).strip()

                # ── Телефоны ──
                phones: list[str] = []
                phone_elems = await card.query_selector_all(
                    "a[href^='tel:'], [class*='phone'], span[class*='phone']"
                )
                for pe in phone_elems:
                    phone_text = await pe.inner_text()
                    phones.extend(normalize_phones([phone_text]))

                # ── Сайт ──
                website = None
                site_elem = await card.query_selector(
                    "a[class*='website'], a[href*='http']:not([href*='yell.ru'])"
                )
                if site_elem:
                    href = await site_elem.get_attribute("href")
                    if href and "yell.ru" not in href:
                        website = href

                # ── Email ──
                card_html = await card.inner_html()
                emails = extract_emails(card_html)

                # ── Мессенджеры ──
                messengers: dict[str, str] = {}
                link_elems = await card.query_selector_all("a[href]")
                for link in link_elems[:30]:
                    try:
                        href = await link.get_attribute("href") or ""
                        if not href:
                            continue
                        if "vk.com" in href and "vk" not in messengers:
                            messengers["vk"] = href
                        elif "t.me" in href and "share" not in href and "telegram" not in messengers:
                            messengers["telegram"] = href
                        elif "wa.me" in href and "whatsapp" not in messengers:
                            messengers["whatsapp"] = href
                        elif "instagram.com" in href and "instagram" not in messengers:
                            messengers["instagram"] = href
                    except Exception:
                        continue

                # ── Категория Yell ──
                category = ""
                cat_elem = await card.query_selector(
                    "[class*='category'], [class*='rubric'], span[class*='tag']"
                )
                if cat_elem:
                    category = (await cat_elem.inner_text()).strip()

                companies.append(RawCompany(
                    source=Source.YELL,
                    source_url=start_url if hasattr(self, '_current_url') else "",
                    name=name,
                    phones=phones,
                    address_raw=address,
                    website=website,
                    emails=emails,
                    city=self.city,
                    messengers=messengers,
                ))

            except Exception as e:
                logger.debug(f"  Yell: пропущена карточка: {e}")
                continue

        return companies
