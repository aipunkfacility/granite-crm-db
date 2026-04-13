# scrapers/jsprav_playwright.py — Playwright-версия JSprav (глубокий сбор)
# Поддерживает два режима:
#   - "click_more": быстрый — кликает «Показать ещё», собирает JSON-LD со всех страниц
#   - "deep": медленный — обходит каждую страницу компании для детальной информации
import re
import base64
import json
from granite.scrapers.base import BaseScraper
from granite.models import RawCompany, Source
from granite.utils import normalize_phones, extract_emails, adaptive_delay, slugify, _sanitize_url_for_log
from loguru import logger

JSPRAV_CATEGORY = "izgotovlenie-i-ustanovka-pamyatnikov-i-nadgrobij"


class JspravPlaywrightScraper(BaseScraper):
    """Playwright-версия JSprav.

    Режимы:
    - click_more: кликает «Показать ещё» для загрузки всех компаний через JS,
      затем собирает JSON-LD — быстрый, но требует Playwright.
    - deep: обходит страницы компаний для детальной информации — медленнее.

    Используется как fallback когда JspravScraper не может собрать все компании
    (jsprav.ru отдаёт только первые 5 страниц через статический HTML).
    """

    def __init__(
        self,
        config: dict,
        city: str,
        playwright_page=None,
        mode: str = "click_more",
        categories: list[str] | None = None,
        subdomain: str | None = None,
        target_count: int | None = None,
    ):
        super().__init__(config, city)
        self.page = playwright_page
        self.mode = mode  # "click_more" or "deep"
        self.source_config = config.get("sources", {}).get("jsprav", {})
        self.subdomain_map = self.source_config.get("subdomain_map", {})
        self._cached_subdomain = subdomain
        if categories:
            self.categories = categories
        else:
            self.categories = self.source_config.get(
                "categories", [JSPRAV_CATEGORY]
            )
        self._city_lower = city.lower().strip()
        self.target_count = target_count  # если задан — стоп после набора

    def _get_subdomain(self) -> str:
        if self._cached_subdomain:
            return self._cached_subdomain
        city_lower = self._city_lower
        if city_lower in self.subdomain_map:
            return self.subdomain_map[city_lower]
        base = slugify(self.city)
        if base.endswith("iy"):
            base = base[:-2] + "ij"
        return base

    def _is_local(self, address: dict) -> bool:
        """Проверяет, относится ли компания к искомому городу."""
        locality = address.get("addressLocality", "")
        if not locality:
            return True
        loc_lower = locality.lower().strip()
        if loc_lower == self._city_lower:
            return True
        if self._city_lower.startswith(loc_lower) or loc_lower.startswith(
            self._city_lower
        ):
            shorter = min(len(self._city_lower), len(loc_lower))
            longer = max(len(self._city_lower), len(loc_lower))
            if shorter * 100 / longer >= 70:
                return True
        if len(loc_lower) >= 3:
            stem = loc_lower.rstrip("аеоуияью")
            if stem and stem == self._city_lower.rstrip("аеоуияью"):
                return True
        return False

    # ═══════════════════════════════════════════════════════════════════
    #  РЕЖИМ: click_more — быстрый сбор через JSON-LD + Playwright
    # ═══════════════════════════════════════════════════════════════════

    def _scrape_click_more(self) -> list[RawCompany]:
        """Кликает «Показать ещё» пока кнопка есть, собирает JSON-LD."""
        if not self.page:
            logger.warning("  JSprav PW: Playwright page не передан, пропуск")
            return []

        companies = []
        subdomain = self._get_subdomain()
        if not re.match(r'^[a-z0-9][a-z0-9-]*$', subdomain):
            logger.warning(f"Invalid subdomain '{subdomain}' for city '{self.city}'")
            return []

        for category in self.categories:
            seen_urls = set()
            companies_before = len(companies)
            base_url = f"https://{subdomain}.jsprav.ru/{category}/"
            logger.info(f"  JSprav PW (click_more): {base_url}")

            try:
                self.page.goto(base_url, timeout=60000)
                adaptive_delay(1.5, 2.5)
                self.page.wait_for_load_state("domcontentloaded", timeout=30000)

                # Кликаем «Показать ещё» пока кнопка доступна
                click_count = 0
                max_clicks = 60  # защита от бесконечного цикла
                while click_count < max_clicks:
                    btn = self.page.query_selector(
                        "a.company-list-next-link"
                    )
                    if not btn:
                        # Пробуем альтернативные селекторы
                        btn = self.page.query_selector(
                            "button.company-list-next-link, a[data-ajax], .show-more"
                        )
                    if not btn:
                        break

                    try:
                        # Скроллим к кнопке и кликаем
                        btn.scroll_into_view_if_needed(timeout=5000)
                        adaptive_delay(0.3, 0.6)
                        btn.click(timeout=10000)
                        adaptive_delay(1.0, 2.0)
                        # Ждём загрузки новых элементов
                        self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                        click_count += 1

                        # Парсим текущие JSON-LD (инкрементально)
                        page_companies = self._parse_jsonld_from_page(seen_urls)
                        companies.extend(page_companies)
                        logger.debug(
                            f"  JSprav PW: после клика #{click_count} — "
                            f"{len(companies) - companies_before} компаний"
                        )

                        if (self.target_count is not None
                                and (len(companies) - companies_before) >= self.target_count):
                            logger.info(
                                f"  JSprav PW: набрано target {self.target_count} — стоп"
                            )
                            break

                    except Exception as e:
                        logger.debug(f"  JSprav PW: клик #{click_count + 1} не удался: {e}")
                        break

                # Финальный парсинг JSON-LD со всей страницы
                page_companies = self._parse_jsonld_from_page(seen_urls)
                companies.extend(page_companies)

                logger.info(
                    f"  JSprav PW: итого {len(companies) - companies_before} компаний "
                    f"для {self.city} ({click_count} кликов)"
                )

            except Exception as e:
                logger.error(f"  JSprav PW error ({category}): {e}")

        return companies

    def _parse_jsonld_from_page(self, seen_urls: set) -> list[RawCompany]:
        """Парсит JSON-LD из текущего состояния страницы Playwright."""
        companies = []
        scripts = self.page.query_selector_all('script[type="application/ld+json"]')

        for script in scripts:
            try:
                raw = script.inner_text()
                if not raw:
                    continue
                data = json.loads(raw)
                if data.get("@type") != "ItemList":
                    continue
                for item in data.get("itemListElement", []):
                    c = item.get("item", {})
                    if c.get("@type") != "LocalBusiness":
                        continue
                    name = c.get("name", "")
                    if not name:
                        continue

                    addr = c.get("address", {})
                    org_url = c.get("url", "")
                    if org_url and org_url in seen_urls:
                        continue

                    if not self._is_local(addr):
                        continue

                    if org_url:
                        seen_urls.add(org_url)

                    same = c.get("sameAs", [])
                    tel = c.get("telephone", [])
                    if isinstance(tel, str):
                        tel = [tel]
                    phones = normalize_phones(tel)
                    if isinstance(same, str):
                        website = same if same else None
                    else:
                        website = same[0] if same else None

                    geo = None
                    if c.get("geo"):
                        try:
                            lat_raw = c["geo"].get("latitude")
                            lon_raw = c["geo"].get("longitude")
                            if lat_raw is not None and lon_raw is not None:
                                geo = [float(lat_raw), float(lon_raw)]
                        except (ValueError, TypeError):
                            pass

                    companies.append(
                        RawCompany(
                            source=Source.JSPRAV_PW,
                            source_url=org_url,  # URL detail-страницы компании
                            name=name,
                            phones=phones,
                            address_raw=f"{addr.get('streetAddress', '')}, "
                            f"{addr.get('addressLocality', '')}".strip(", "),
                            website=website,
                            emails=[],
                            city=self.city,
                            geo=geo,
                        )
                    )
            except (json.JSONDecodeError, KeyError, AttributeError):
                continue

        # ── Enrichment detail-страниц (мессенджеры из base64 data-link) ──
        companies = self._enrich_from_detail_pages(companies)

        return companies

    def _enrich_from_detail_pages(self, companies: list[RawCompany]) -> list[RawCompany]:
        """Второй проход: обходит detail-страницы через Playwright и извлекает
        мессенджеры (TG, VK, WA, Viber) и сайт из base64 data-link.
        """
        url_to_company: dict[str, RawCompany] = {}
        for c in companies:
            if c.source_url and c.source_url.startswith("http"):
                url_to_company[c.source_url] = c

        if not url_to_company:
            return companies

        total = len(url_to_company)
        enriched = 0
        logger.info(f"  JSprav PW: enrichment {total} detail-страниц...")

        for i, (detail_url, company) in enumerate(url_to_company.items()):
            if i > 0 and i % 50 == 0:
                logger.info(
                    f"  JSprav PW: enrichment {i}/{total} "
                    f"(messengers: {enriched})"
                )

            try:
                if not self.page:
                    break
                self.page.goto(detail_url, timeout=20000)
                self.page.wait_for_load_state("domcontentloaded", timeout=15000)

                page_content = self.page.content()
                soup = self.page.query_selector("body")

                # Мессенджеры и сайт из base64 data-link
                messengers = {}
                website = None
                for a in self.page.query_selector_all("a[data-link]"):
                    try:
                        raw_b64 = a.get_attribute("data-link") or ""
                        decoded = base64.b64decode(raw_b64).decode("utf-8")
                        dtype = a.get_attribute("data-type") or ""
                        if dtype == "org-link":
                            website = decoded
                        elif dtype == "org-social-link":
                            self._classify_messenger(decoded, messengers)
                    except Exception:
                        pass

                # Полные телефоны из data-props
                for el in self.page.query_selector_all("[data-props]"):
                    try:
                        props = json.loads(el.get_attribute("data-props") or "{}")
                        if "phones" in props and not company.phones:
                            company.phones = normalize_phones(props["phones"])
                    except Exception:
                        pass

                if messengers:
                    company.messengers = messengers
                    enriched += 1
                if website and not company.website:
                    company.website = website
                if not company.emails:
                    company.emails = extract_emails(page_content)

            except Exception as e:
                logger.debug(
                    f"  JSprav PW: enrichment error for {detail_url}: {e}"
                )

            if i < total - 1:
                adaptive_delay(0.5, 1.0)

        logger.info(
            f"  JSprav PW: enrichment завершён — {enriched}/{total} "
            f"с мессенджерами"
        )
        return companies

    @staticmethod
    def _classify_messenger(url: str, messengers: dict) -> None:
        """Классифицирует URL мессенджера и добавляет в dict."""
        url_lower = url.lower()
        if "t.me" in url_lower:
            messengers["telegram"] = url
        elif "vk.com" in url_lower or "vkontakte" in url_lower:
            messengers["vk"] = url
        elif "viber" in url_lower:
            messengers["viber"] = url
        elif "wa.me" in url_lower or "whatsapp" in url_lower:
            messengers["whatsapp"] = url
        elif "ok.ru" in url_lower:
            messengers["odnoklassniki"] = url
        elif "youtube" in url_lower or "youtu.be" in url_lower:
            pass  # YouTube — не мессенджер, пропускаем
        elif "instagram" in url_lower:
            messengers["instagram"] = url

    # ═══════════════════════════════════════════════════════════════════
    #  РЕЖИМ: deep — обход каждой страницы компании
    # ═══════════════════════════════════════════════════════════════════

    def _scrape_deep(self) -> list[RawCompany]:
        """Обходит страницы компаний для детальной информации (медленно)."""
        if not self.page:
            logger.warning("  JSprav PW: Playwright page не передан, пропуск")
            return []

        companies = []
        subdomain = self._get_subdomain()
        if not re.match(r'^[a-z0-9][a-z0-9-]*$', subdomain):
            logger.warning(f"Invalid subdomain '{subdomain}' for city '{self.city}'")
            return []

        for category in self.categories:
            base_url = f"https://{subdomain}.jsprav.ru/{category}/"
            logger.info(f"  JSprav PW (deep): {base_url}")

            try:
                self.page.goto(base_url, timeout=60000)
                adaptive_delay(2.0, 3.0)
                self.page.wait_for_load_state("domcontentloaded", timeout=30000)

                # Кликаем «Показать ещё» для загрузки всех компаний
                for _ in range(20):
                    btn = self.page.query_selector("a.company-list-next-link")
                    if not btn:
                        break
                    try:
                        btn.scroll_into_view_if_needed(timeout=5000)
                        btn.click(timeout=10000)
                        adaptive_delay(1.0, 2.0)
                        self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        break

                # Собираем ссылки на компании
                safe_category = re.sub(r"([()\[\]{}\\\"'])", r"\\\g<1>", category)
                company_links = self.page.query_selector_all(
                    f"a[href*='/{safe_category}/']"
                )
                seen_urls: set = set()
                hrefs = []
                for link in company_links:
                    href = link.get_attribute("href")
                    if not href or href in seen_urls:
                        continue
                    if not href.startswith(('http://', 'https://', '/')):
                        continue
                    if href.startswith('javascript:'):
                        continue
                    if href.rstrip("/").endswith(category):
                        continue
                    seen_urls.add(href)
                    hrefs.append(href)

                logger.info(f"  JSprav PW: найдено {len(hrefs)} ссылок")

                for href in hrefs:
                    try:
                        company_url = (
                            f"https://{subdomain}.jsprav.ru{href}"
                            if href.startswith("/")
                            else href
                        )
                        self.page.goto(company_url, timeout=20000)
                        self.page.wait_for_load_state("domcontentloaded", timeout=15000)

                        title = self.page.query_selector("h1")
                        name = title.inner_text().strip() if title else ""
                        if not name:
                            continue

                        phone_elems = self.page.query_selector_all("a[href^='tel:']")
                        phones_raw = [pe.inner_text() for pe in phone_elems]
                        phones = normalize_phones(phones_raw)

                        addr_elem = self.page.query_selector("address")
                        address = addr_elem.inner_text().strip() if addr_elem else ""

                        site_elem = self.page.query_selector(
                            f"a[href*='http']:not([href*='jsprav'])"
                        )
                        website = site_elem.get_attribute("href") if site_elem else None

                        page_content = self.page.content()
                        emails = extract_emails(page_content)

                        # Мессенджеры
                        messengers: dict = {}
                        for a_tag in self.page.query_selector_all(
                            "a[href*='t.me'], a[href*='vk.com']"
                        ):
                            a_href = a_tag.get_attribute("href") or ""
                            if "t.me" in a_href:
                                messengers["telegram"] = a_href
                            elif "vk.com" in a_href:
                                messengers["vk"] = a_href

                        companies.append(
                            RawCompany(
                                source=Source.JSPRAV_PW,
                                source_url=company_url,
                                name=name,
                                phones=phones,
                                address_raw=address,
                                website=website,
                                emails=emails,
                                city=self.city,
                                messengers=messengers,
                            )
                        )

                    except Exception as e:
                        logger.warning(f"  JSprav PW: ошибка для {href}: {e}")
                        continue

            except Exception as e:
                logger.error(f"  JSprav PW error ({category}): {e}")

        return companies

    def scrape(self) -> list[RawCompany]:
        if self.mode == "deep":
            return self._scrape_deep()
        return self._scrape_click_more()
