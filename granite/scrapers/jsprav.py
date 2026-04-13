# scrapers/jsprav.py — рефакторинг scripts/scrape_fast.py (JSON-LD, быстрая версия)
import re
import base64
import requests
import json
import time
from urllib.parse import urlparse, urlunparse
from bs4 import BeautifulSoup
from granite.scrapers.base import BaseScraper
from granite.models import RawCompany, Source
from granite.utils import normalize_phone, normalize_phones, extract_domain, extract_emails, slugify, get_random_ua, adaptive_delay, _sanitize_url_for_log
from loguru import logger

JSPRAV_CATEGORY = "izgotovlenie-i-ustanovka-pamyatnikov-i-nadgrobij"


class JspravScraper(BaseScraper):
    """Скрепер jsprav.ru через JSON-LD — быстрый, не требует Playwright."""

    def __init__(
        self,
        config: dict,
        city: str,
        categories: list[str] | None = None,
        subdomain: str | None = None,
    ):
        super().__init__(config, city)
        self.source_config = config.get("sources", {}).get("jsprav", {})
        self.subdomain_map = self.source_config.get("subdomain_map", {})
        self._cached_subdomain = subdomain
        if categories:
            self.categories = categories
        else:
            self.categories = [JSPRAV_CATEGORY]

        self._city_lower = city.lower().strip()
        self._declared_total = None  # для Playwright fallback: сколько всего компаний
        self._needs_playwright = False  # устанавливается в scrape() если нужно добрать через PW

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

    def _parse_total_from_summary(self, soup) -> int | None:
        """Ищет в саммари количество компаний для города."""
        benefits = soup.find("div", class_="cat-benefits")
        if not benefits:
            return None
        for li in benefits.find_all("li"):
            text = li.get_text(strip=True)
            m = re.search(r"(\d+)\s+компани", text)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _extract_page_num(url: str) -> int:
        """Извлекает номер страницы из URL."""
        m = re.search(r"page-?(\d+)", url) or re.search(r"page=(\d+)", url)
        return int(m.group(1)) if m else 1

    def _get_next_page_url(self, soup, base_dir: str, page_num: int) -> str | None:
        """Ищет кнопку 'Показать ещё' и берёт URL из data-url.

        Если кнопка не найдена — возвращает fallback URL через ?page=N.
        """
        btn = soup.find("a", class_="company-list-next-link")
        if btn:
            data_url = btn.get("data-url")
            if data_url:
                return data_url

        # Fallback: пробуем ?page=N (jsprav иногда не генерирует /page-N/ после 5-й)
        # Guard against infinite pagination — stop after 50 pages
        if page_num >= 50:
            return None
        parsed = urlparse(base_dir)
        fallback = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, "", f"page={page_num + 1}", "")
        )
        return fallback

    def _parse_companies_from_soup(self, soup, seen_urls: set) -> list[RawCompany]:
        """Парсит JSON-LD из soup, фильтрует дубли (по URL) и чужой город."""
        companies = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                raw = script.string
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

                    # Дубль по URL организации
                    org_url = c.get("url", "")
                    if org_url and org_url in seen_urls:
                        continue

                    # Фильтр по городу
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
                                lat = float(lat_raw)
                                lon = float(lon_raw)
                                geo = [lat, lon]
                        except (ValueError, TypeError):
                            pass

                    # ── Email ──
                    # JSON-LD может содержать поле "email"
                    item_emails = c.get("email", [])
                    if isinstance(item_emails, str):
                        item_emails = [item_emails]
                    elif not isinstance(item_emails, list):
                        item_emails = []

                    companies.append(
                        RawCompany(
                            source=Source.JSPRAV,
                            source_url=org_url,  # URL detail-страницы компании
                            name=name,
                            phones=phones,
                            address_raw=f"{addr.get('streetAddress', '')}, "
                            f"{addr.get('addressLocality', '')}".strip(", "),
                            website=website,
                            emails=item_emails,
                            city=self.city,
                            geo=geo,
                        )
                    )
            except (json.JSONDecodeError, KeyError, AttributeError):
                continue
        return companies

    def scrape(self) -> list[RawCompany]:
        companies = []
        subdomain = self._get_subdomain()
        if not re.match(r'^[a-z0-9][a-z0-9-]*$', subdomain):
            logger.warning(f"Invalid subdomain '{subdomain}' for city '{self.city}'")
            return []
        ua = {
            "User-Agent": get_random_ua()
        }

        for category in self.categories:
            seen_urls = set()
            companies_before = len(companies)
            declared_total = None
            url = f"https://{subdomain}.jsprav.ru/{category}/"
            empty_streak = 0
            last_page_num = 1
            max_pages = 5  # статическая пагинация jsprav отдаёт max ~5 страниц

            while url:
                page_num = self._extract_page_num(url)
                last_page_num = page_num
                logger.info(f"  JSprav: {_sanitize_url_for_log(url)}")

                # Ретраи при таймауте/ошибках сети
                r = None
                for attempt in range(3):
                    try:
                        r = requests.get(url, timeout=60, headers=ua)
                        break
                    except (requests.Timeout, requests.ConnectionError) as e:
                        logger.warning(
                            f"  JSprav: попытка {attempt + 1}/3 не удалась: {e}"
                        )
                        time.sleep(3)

                try:
                    if r is None:
                        logger.error(
                            f"  JSprav: не удалось загрузить {url} за 3 попытки"
                        )
                        continue

                    if r.status_code == 404:
                        logger.warning(
                            f"  JSprav: 404 для /page-{page_num}/ — пробуем fallback ?page="
                        )
                        # Fallback: если /page-N/ = 404, пробуем ?page=N
                        base_parsed = urlparse(
                            f"https://{subdomain}.jsprav.ru/{category}/"
                        )
                        fallback_url = urlunparse(
                            (
                                base_parsed.scheme,
                                base_parsed.netloc,
                                base_parsed.path,
                                "",
                                f"page={page_num}",
                                "",
                            )
                        )
                        r_fb = requests.get(fallback_url, timeout=30, headers=ua)
                        if r_fb.status_code == 200 and "LocalBusiness" in r_fb.text:
                            r = r_fb
                            url = fallback_url
                            logger.info(f"  JSprav: fallback ?page={page_num} успешен")
                        else:
                            logger.info(f"  JSprav: fallback тоже пуст — стоп")
                            break

                    soup = BeautifulSoup(r.text, "html.parser")

                    # На первой странице берём total из саммари
                    if declared_total is None:
                        declared_total = self._parse_total_from_summary(soup)
                        if declared_total is not None:
                            self._declared_total = declared_total
                            logger.info(
                                f"  JSprav: саммари — {declared_total} компаний в {self.city}"
                            )

                    page_companies = self._parse_companies_from_soup(soup, seen_urls)
                    # source_url уже заполнен URL detail-страницы в _parse_companies_from_soup
                    companies.extend(page_companies)
                    logger.info(
                        f"  JSprav: +{len(page_companies)} компаний (всего {len(companies)})"
                    )

                    # Набрали declared total — стоп
                    if declared_total is not None and (len(companies) - companies_before) >= declared_total:
                        logger.info(
                            f"  Jsprav: набрано {len(companies) - companies_before} из {declared_total} для категории {category} — стоп"
                        )
                        break

                    # Нет новых компаний — считаем пустую страницу
                    if len(page_companies) == 0:
                        empty_streak += 1
                        if empty_streak >= 2:
                            logger.info(
                                f"  JSprav: {empty_streak} пустых страниц подряд — стоп"
                            )
                            break
                    else:
                        empty_streak = 0

                    # Стоп после max_pages статической пагинации
                    if page_num >= max_pages:
                        logger.info(
                            f"  JSprav: достигнут лимит статической пагинации ({max_pages} стр.)"
                        )
                        break

                    # Ищем ссылку на следующую страницу через кнопку "Показать ещё"
                    next_url = self._get_next_page_url(soup, url, page_num)
                    if not next_url:
                        break

                    # Не зацикливаемся на одном и том же URL
                    if next_url == url:
                        break

                    url = next_url
                    adaptive_delay(0.8, 1.5)

                except Exception as e:
                    logger.error(f"  JSprav error ({_sanitize_url_for_log(url)}): {e}")
                    continue  # не теряем набранные компании при ошибке страницы

            # Всегда помечаем что нужен Playwright fallback если есть declared_total
            # и мы не добрали — scraping_phase.py запустит JspravPlaywrightScraper
            cat_count = len(companies) - companies_before
            if declared_total is not None and cat_count < declared_total:
                self._needs_playwright = True
                logger.warning(
                    f"  JSprav: получено {cat_count} из {declared_total} для {self.city}/{category}. "
                    f"Потрібен Playwright fallback для добора."
                )
            elif declared_total is None and cat_count > 0:
                # declared_total не найден — тоже помечаем для PW на всякий случай
                # (jsprav может скрывать summary на некоторых городах)
                self._needs_playwright = True

        # ═══════════════════════════════════════════════════════════════
        #  Второй проход: enrichment detail-страниц — мессенджеры, сайт, email
        # ═══════════════════════════════════════════════════════════════
        companies = self._enrich_from_detail_pages(companies)

        logger.info(f"  JSprav: итого {len(companies)} компаний для {self.city}")
        return companies

    def _enrich_from_detail_pages(self, companies: list[RawCompany]) -> list[RawCompany]:
        """Второй проход: обходит detail-страницы компаний и извлекает
        мессенджеры (TG, VK, WA, Viber), сайт и email из base64 data-link.
        """
        # Карта detail URL → company для быстрого поиска
        url_to_company: dict[str, RawCompany] = {}
        for c in companies:
            if c.source_url and c.source_url.startswith("http"):
                url_to_company[c.source_url] = c

        if not url_to_company:
            logger.debug("  JSprav: нет detail URL для enrichment — пропуск")
            return companies

        total = len(url_to_company)
        enriched = 0
        logger.info(f"  JSprav: enrichment {total} detail-страниц...")

        for i, (detail_url, company) in enumerate(url_to_company.items()):
            if i > 0 and i % 50 == 0:
                logger.info(
                    f"  JSprav: enrichment {i}/{total} "
                    f"(messengers: {enriched})"
                )

            try:
                detail = self._fetch_detail_page(detail_url)
                if detail["messengers"]:
                    company.messengers = detail["messengers"]
                    enriched += 1
                if detail["website"] and not company.website:
                    company.website = detail["website"]
                if detail["emails"] and not company.emails:
                    company.emails = detail["emails"]
                if detail["phones"] and not company.phones:
                    company.phones = detail["phones"]
            except Exception as e:
                logger.debug(f"  JSprav: enrichment error for {detail_url}: {e}")

            # Задержка между запросами к detail-страницам
            if i < total - 1:
                adaptive_delay(0.3, 0.7)

        logger.info(
            f"  JSprav: enrichment завершён — {enriched}/{total} "
            f"с мессенджерами"
        )
        return companies

    def _fetch_detail_page(self, detail_url: str) -> dict:
        """Загружает detail-страницу компании и извлекает:
        - messengers из base64 data-link (TG, VK, WA, Viber)
        - website из base64 data-link (org-link)
        - phones из data-props JSON
        - emails из HTML regex
        """
        result = {
            "messengers": {},
            "website": None,
            "emails": [],
            "phones": [],
        }

        r = None
        for attempt in range(3):
            try:
                r = requests.get(
                    detail_url,
                    timeout=20,
                    headers={"User-Agent": get_random_ua()},
                )
                if r.status_code == 200:
                    break
                elif r.status_code in (403, 404):
                    return result
            except (requests.Timeout, requests.ConnectionError):
                time.sleep(2)

        if r is None or r.status_code != 200:
            return result

        soup = BeautifulSoup(r.text, "html.parser")

        # ── Мессенджеры и сайт из base64 data-link ──
        for a in soup.find_all("a", attrs={"data-link": True}):
            try:
                decoded = base64.b64decode(a["data-link"]).decode("utf-8")
                dtype = a.get("data-type", "")
                if dtype == "org-link":
                    result["website"] = decoded
                elif dtype == "org-social-link":
                    self._classify_messenger(decoded, result["messengers"])
            except Exception:
                pass

        # ── Полные телефоны из data-props JSON ──
        for el in soup.find_all(attrs={"data-props": True}):
            try:
                props = json.loads(el.get("data-props", "{}"))
                if "phones" in props:
                    result["phones"] = normalize_phones(props["phones"])
            except Exception:
                pass

        # ── Email из HTML (бонус — jsprav обычно не показывает email) ──
        result["emails"] = extract_emails(r.text)

        return result

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
