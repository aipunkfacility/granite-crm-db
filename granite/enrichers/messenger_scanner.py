# enrichers/messenger_scanner.py
import re
from urllib.parse import urljoin, urlparse
from loguru import logger
from granite.utils import fetch_page, adaptive_delay, is_safe_url, extract_emails, extract_phones
from granite.http_client import async_fetch_page, async_adaptive_delay


class MessengerScanner:
    """Сканирует сайт на наличие контактов: мессенджеры, email, телефоны."""

    def __init__(self, config: dict):
        pass

    def scan_website(self, base_url: str) -> dict:
        """Сканирует сайт и возвращает найденные контакты.

        Returns:
            dict с ключами: telegram, whatsapp, vk, _emails, _phones.
            Ключи с "_" — технические, используются для обогащения.
        """
        found: dict = {
            "_emails": [],
            "_phones": [],
        }

        if not base_url:
            return found

        base_url_clean = base_url.rstrip("/")

        if not is_safe_url(base_url_clean):
            return found

        html = None

        # 1. Сканируем главную страницу
        try:
            html = fetch_page(base_url_clean + "/", timeout=10)
            if html:
                self._extract_social_links(html, found)
                self._extract_emails(html, found)
                self._extract_phones(html, found)
        except Exception as e:
            logger.debug(f"MessengerScanner scan_website main page error: {e}")

        # Если уже нашли telegram — скорее всего этого достаточно для мессенджеров,
        # но email/телефоны всё равно ищем дальше
        if "telegram" in found:
            pass  # продолжаем искать email

        # 2. Ищем ссылки на странице контактов с главной
        try:
            adaptive_delay()
            contacts_url = self._find_contacts_link(base_url_clean, html)
            if contacts_url:
                if not is_safe_url(contacts_url):
                    contacts_url = None
                    chtml = None
                else:
                    chtml = fetch_page(contacts_url, timeout=10)
                if chtml:
                    self._extract_social_links(chtml, found)
                    self._extract_emails(chtml, found)
                    self._extract_phones(chtml, found)
                    # На странице контактов ищем ссылки на другие страницы
                    extra_links = self._find_relevant_links(chtml, base_url_clean)
                    for link in extra_links:
                        if link == contacts_url:
                            continue
                        if (
                            "telegram" in found
                            and "whatsapp" in found
                            and found.get("_emails")
                        ):
                            break
                        if not is_safe_url(link):
                            continue
                        try:
                            adaptive_delay()
                            ehtml = fetch_page(link, timeout=10)
                            if ehtml:
                                self._extract_social_links(ehtml, found)
                                self._extract_emails(ehtml, found)
                                self._extract_phones(ehtml, found)
                        except Exception as e:
                            logger.debug(f"MessengerScanner extra page error: {e}")
                            continue
        except Exception as e:
            logger.debug(f"MessengerScanner contacts page error: {e}")

        return found

    def _extract_emails(self, html: str, result: dict):
        """Извлекает email из HTML: mailto: ссылки + текст."""
        if not html:
            return

        emails = result.setdefault("_emails", [])

        # mailto: ссылки (приоритет — обычно реальные)
        for m in re.finditer(r'href=["\']mailto:([^"\'\s?]+)', html, re.IGNORECASE):
            email = m.group(1).strip()
            if email and email not in emails:
                emails.append(email)

        # Email из текста
        text_emails = extract_emails(html)
        for em in text_emails:
            if em not in emails:
                emails.append(em)

    def _extract_phones(self, html: str, result: dict):
        """Извлекает телефоны из HTML: tel: ссылки + текст."""
        if not html:
            return

        phones = result.setdefault("_phones", [])

        # tel: ссылки (приоритет)
        for m in re.finditer(r'href=["\']tel:([^"\'\s]+)', html, re.IGNORECASE):
            phone = m.group(1).strip()
            if phone and phone not in phones:
                phones.append(phone)

        # Текст страницы
        # Простой текст: убираем теги
        text = re.sub(r"<[^>]+>", " ", html)
        for p in extract_phones(text):
            if p not in phones:
                phones.append(p)

    def _find_contacts_link(self, base_url: str, html: str) -> str | None:
        """Ищет ссылку на страницу контактов в HTML главной страницы."""
        if not html:
            return None

        # Ищем ссылки по тексту и URL
        soup_pattern = re.compile(
            r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        found_links = []
        seen_hrefs = set()

        for match in soup_pattern.finditer(html):
            href = match.group(1)
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip().lower()

            if href.startswith(("#", "javascript:", "tel:", "mailto:")):
                continue
            if href in seen_hrefs:
                continue

            # По тексту ссылки
            if any(
                kw in text
                for kw in ["контакт", "связ", "телефон", "обратн", "написать"]
            ):
                full_url = urljoin(base_url + "/", href)
                found_links.append(full_url)
                seen_hrefs.add(href)
                continue

            # По URL
            href_lower = href.lower()
            if any(
                p in href_lower for p in ["contact", "kontakt", "kontakty", "kontaktyi"]
            ):
                full_url = urljoin(base_url + "/", href)
                found_links.append(full_url)
                seen_hrefs.add(href)

        if found_links:
            return found_links[0]
        return None

    def _find_relevant_links(self, html: str, base_url: str) -> list[str]:
        """Находит ссылки на полезные страницы (о нас, производство, каталог)."""
        links = []
        seen = set()

        link_pattern = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\']', re.IGNORECASE)

        for match in link_pattern.finditer(html):
            href = match.group(1)
            if href.startswith(("#", "javascript:", "tel:", "mailto:")):
                continue
            href_lower = href.lower()
            if href_lower in seen:
                continue

            # Только ссылки на тот же домен
            full_url = urljoin(base_url + "/", href)
            if urlparse(full_url).netloc != urlparse(base_url).netloc:
                continue

            # Интересующие страницы
            if any(
                kw in href_lower
                for kw in [
                    "about",
                    "o-nas",
                    "o_kompanii",
                    "production",
                    "proizvodstvo",
                    "catalog",
                    "katalog",
                    "uslugi",
                    "services",
                ]
            ):
                links.append(full_url)
                seen.add(href_lower)

        return links[:3]  # не более 3 доп. страниц

    def _extract_social_links(self, html: str, result: dict):
        """Парсинг ссылок из HTML и запись в result dict."""
        if not html:
            return

        # Telegram: t.me, telegram.me
        for m in re.finditer(
            r'href=["\'](https?://(?:t\.me|telegram\.me)/([^"\'\s]+))["\']', html
        ):
            link = m.group(1).rstrip("/")
            if not any(
                kw in link.lower() for kw in ["share", "joinchat"]
            ):  # пропускаем кнопки "поделиться"
                if "telegram" not in result:
                    result["telegram"] = link

        # WhatsApp: wa.me, api.whatsapp.com
        for m in re.finditer(
            r'href=["\'](https?://(?:wa\.me|api\.whatsapp\.com/send\?phone=[^"\'\s]+))["\']',
            html,
        ):
            if "whatsapp" not in result:
                result["whatsapp"] = m.group(1)

        # VK
        for m in re.finditer(
            r'href=["\'](https?://(?:www\.)?vk\.com/([^"\'\s]+))["\']', html
        ):
            if "vk" not in result:
                result["vk"] = m.group(1)

    # ===== Async variants =====

    async def scan_website_async(self, base_url: str) -> dict:
        """Async версия scan_website — сканирование сайта через httpx.

        Идентична по логике scan_website(), но использует async_fetch_page()
        и async_adaptive_delay() для неблокирующего I/O.
        Используется в EnrichmentPhase.run_async() для параллельного обогащения.

        Returns:
            dict с ключами: telegram, whatsapp, vk, _emails, _phones.
        """
        found: dict = {
            "_emails": [],
            "_phones": [],
        }

        if not base_url:
            return found

        base_url_clean = base_url.rstrip("/")

        if not is_safe_url(base_url_clean):
            return found

        html = None

        # 1. Сканируем главную страницу
        try:
            html = await async_fetch_page(base_url_clean + "/", timeout=10)
            if html:
                self._extract_social_links(html, found)
                self._extract_emails(html, found)
                self._extract_phones(html, found)
        except Exception as e:
            logger.debug(f"MessengerScanner scan_website_async main page error: {e}")

        # 2. Ищем ссылки на странице контактов
        try:
            await async_adaptive_delay()
            contacts_url = self._find_contacts_link(base_url_clean, html)
            if contacts_url:
                if not is_safe_url(contacts_url):
                    contacts_url = None
                    chtml = None
                else:
                    chtml = await async_fetch_page(contacts_url, timeout=10)
                if chtml:
                    self._extract_social_links(chtml, found)
                    self._extract_emails(chtml, found)
                    self._extract_phones(chtml, found)
                    # На странице контактов ищем ссылки на другие страницы
                    extra_links = self._find_relevant_links(chtml, base_url_clean)
                    for link in extra_links:
                        if link == contacts_url:
                            continue
                        if (
                            "telegram" in found
                            and "whatsapp" in found
                            and found.get("_emails")
                        ):
                            break
                        if not is_safe_url(link):
                            continue
                        try:
                            await async_adaptive_delay()
                            ehtml = await async_fetch_page(link, timeout=10)
                            if ehtml:
                                self._extract_social_links(ehtml, found)
                                self._extract_emails(ehtml, found)
                                self._extract_phones(ehtml, found)
                        except Exception as e:
                            logger.debug(f"MessengerScanner extra page async error: {e}")
                            continue
        except Exception as e:
            logger.debug(f"MessengerScanner contacts page async error: {e}")

        return found
