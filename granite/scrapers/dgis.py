# scrapers/dgis.py — рефакторинг части 2GIS из scripts/scrape_city.py
from granite.scrapers.base import BaseScraper
from granite.models import RawCompany, Source
from granite.utils import normalize_phones, adaptive_delay
from loguru import logger
from urllib.parse import quote


class DgisScraper(BaseScraper):
    """Скрепер 2GIS через Playwright. Страница передаётся извне."""

    def __init__(self, config: dict, city: str, playwright_page=None):
        super().__init__(config, city)
        self.page = playwright_page
        self.source_config = config.get("sources", {}).get("dgis", {})
        self.search_category = self.source_config.get(
            "search_category", "изготовление памятников"
        )

    def scrape(self) -> list[RawCompany]:
        if not self.page:
            logger.warning("  2GIS: Playwright page не передан, пропуск")
            return []

        companies = []
        from granite.utils import slugify

        city_slug = slugify(self.city)
        url = f"https://2gis.ru/{city_slug}/search/{quote(self.search_category)}"
        logger.info(f"  2GIS: {url}")

        try:
            self.page.goto(url, timeout=30000, wait_until="domcontentloaded")
            self.page.wait_for_load_state("domcontentloaded", timeout=20000)

            for _ in range(3):
                self.page.evaluate("window.scrollBy(0, 1000)")
                adaptive_delay(0.8, 2.0)

            cards = self.page.query_selector_all(
                "div[class*='card'], div[class*='firm'], a[href*='/firm/']"
            )

            for card in cards:
                try:
                    name_elem = card.query_selector(
                        "div[class*='name'], a[class*='name'], span[class*='title']"
                    )
                    if not name_elem:
                        continue
                    name = name_elem.inner_text().strip()
                    if not name or len(name) < 3:
                        continue

                    addr_elem = card.query_selector(
                        "div[class*='address'], span[class*='address']"
                    )
                    address = addr_elem.inner_text().strip() if addr_elem else ""

                    phone_elem = card.query_selector(
                        "div[class*='phone'], span[class*='phone']"
                    )
                    phone_text = phone_elem.inner_text() if phone_elem else ""
                    phones = normalize_phones([phone_text])

                    link_elem = card.query_selector("a[href*='/firm/']")
                    source_url = ""
                    if link_elem:
                        href = link_elem.get_attribute("href")
                        if href:
                            from urllib.parse import urljoin

                            source_url = urljoin("https://2gis.ru", href)

                    # 2GIS часто показывает VK/Telegram прямо в карточке
                    card_messengers: dict = {}
                    for a_tag in card.query_selector_all(
                        "a[href*='vk.com'], a[href*='t.me'], a[href*='instagram.com']"
                    ):
                        href = a_tag.get_attribute("href") or ""
                        if "vk.com" in href:
                            card_messengers["vk"] = href
                        elif "t.me" in href:
                            card_messengers["telegram"] = href
                        elif "instagram.com" in href:
                            card_messengers["instagram"] = href

                    companies.append(
                        RawCompany(
                            source=Source.DGIS,
                            source_url=source_url,
                            name=name,
                            phones=phones,
                            address_raw=address,
                            website=None,
                            emails=[],
                            city=self.city,
                            messengers=card_messengers,
                        )
                    )
                except Exception as e:
                    logger.debug(f"  2GIS: пропущена карточка (ошибка: {e})")
                    continue

        except Exception as e:
            logger.error(f"  2GIS error: {e}")

        return companies
