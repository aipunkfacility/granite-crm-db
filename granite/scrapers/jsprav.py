# scrapers/jsprav.py — рефакторинг scripts/scrape_fast.py (JSON-LD, быстрая версия)
import re
import requests
import json
import time
from urllib.parse import urlparse, urlunparse
from bs4 import BeautifulSoup
from granite.scrapers.base import BaseScraper
from granite.models import RawCompany, Source
from granite.utils import normalize_phone, normalize_phones, extract_domain, slugify
from loguru import logger

JSPRAV_CATEGORY = "izgotovlenie-i-ustanovka-pamyatnikov-i-nadgrobij"


class JspravScraper(BaseScraper):
    """Скрепер jsprav.ru через JSON-LD — быстрый, не требует Playwright."""

    def __init__(
        self,
        config: dict,
        city: str,
        categories: list[str] = None,
        subdomain: str = None,
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

    def _get_subdomain(self) -> str:
        if self._cached_subdomain:
            return self._cached_subdomain
        city_lower = self.city.lower()
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
            return True
        if len(loc_lower) > 4:
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
                data = json.loads(script.string)
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
                    phones = normalize_phones(c.get("telephone", []))
                    website = same[0] if same else None

                    geo = None
                    if c.get("geo"):
                        try:
                            lat = float(c["geo"].get("latitude", 0))
                            lon = float(c["geo"].get("longitude", 0))
                            if lat and lon:
                                geo = (lat, lon)
                        except (ValueError, TypeError):
                            pass

                    companies.append(
                        RawCompany(
                            source=Source.JSPRAV,
                            source_url="",
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
        return companies

    def scrape(self) -> list[RawCompany]:
        companies = []
        subdomain = self._get_subdomain()
        ua = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        for category in self.categories:
            seen_urls = set()
            declared_total = None
            url = f"https://{subdomain}.jsprav.ru/{category}/"
            empty_streak = 0
            last_page_num = 1

            while url:
                page_num = self._extract_page_num(url)
                last_page_num = page_num
                logger.info(f"  JSprav: {url}")

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
                            logger.info(
                                f"  JSprav: саммари — {declared_total} компаний в {self.city}"
                            )

                    page_companies = self._parse_companies_from_soup(soup, seen_urls)
                    for c in page_companies:
                        c.source_url = url
                    companies.extend(page_companies)
                    logger.info(
                        f"  JSprav: +{len(page_companies)} компаний (всего {len(companies)})"
                    )

                    # Набрали declared total — стоп
                    if declared_total is not None and len(companies) >= declared_total:
                        logger.info(
                            f"  JSprav: набрано {len(companies)} из {declared_total} — стоп"
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

                    # Ищем ссылку на следующую страницу через кнопку "Показать ещё"
                    next_url = self._get_next_page_url(soup, url, page_num)
                    if not next_url:
                        break

                    # Не зацикливаемся на одном и том же URL
                    if next_url == url:
                        break

                    url = next_url
                    time.sleep(1.0)

                except Exception as e:
                    logger.error(f"  JSprav error ({url}): {e}")
                    continue  # не теряем набранные компании при ошибке страницы

            # Предупреждение если не добрали до саммари
            if declared_total is not None and len(companies) < declared_total:
                logger.warning(
                    f"  JSprav: получено {len(companies)} из {declared_total} для {self.city}. "
                    f"jsprav.ru отдаёт только {last_page_num} стр. через статическую пагинацию. "
                    f"Остальные компании недоступны без JavaScript."
                )

        logger.info(f"  JSprav: итого {len(companies)} компаний для {self.city}")
        return companies
