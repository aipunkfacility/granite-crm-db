# enrichers/tg_finder.py
import re
import time
import random
from granite.utils import adaptive_delay, TRANSLIT_MAP, get_random_ua
import requests
from loguru import logger
from granite.enrichers._tg_common import TG_MAX_RETRIES, TG_INITIAL_BACKOFF


def tg_request(url: str, headers: dict, timeout: int = 10) -> requests.Response | None:
    """HTTP GET с экспоненциальной выдержкой при HTTP 429 (Too Many Requests).

    Telegram блокирует IP при агрессивном парсинге. При получении 429 ждём
    с экспоненциальной выдержкой (5, 10, 20, 40, 80 сек). После исчерпания
    попыток — логируем warning и возвращаем None.
    """
    backoff = TG_INITIAL_BACKOFF
    for attempt in range(TG_MAX_RETRIES):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 429:
                wait = backoff + random.uniform(0, 2)
                logger.warning(
                    f"TG rate limit (429) для {url[:60]}, "
                    f"повтор через {wait:.0f}с (попытка {attempt + 1}/{TG_MAX_RETRIES})"
                )
                time.sleep(wait)
                backoff *= 2
                continue
            return r
        except requests.RequestException as e:
            logger.warning(f"TG request error ({url[:60]}): {e}")
            return None
    logger.warning(f"TG: исчерпано {TG_MAX_RETRIES} попыток для {url[:60]} — пропуск")
    return None


def _translit(text: str) -> str:
    """Транслитерация кириллицы в латиницу. Использует тот же словарь что и slugify()."""
    text = text.lower()
    for cyr, lat in TRANSLIT_MAP:
        text = text.replace(cyr, lat)
    return text


def find_tg_by_phone(phone: str, config: dict) -> str | None:
    """Метод 1: Прямая привязка телефона (t.me/+7XXX)."""
    if not phone or len(phone) < 11:
        return None

    # Нормализация телефона перед построением URL
    from granite.utils import normalize_phone

    norm_phone = normalize_phone(phone)
    if not norm_phone or len(norm_phone) != 11:
        return None

    enrich_config = config.get("enrichment", {})
    tg_config = enrich_config.get("tg_finder", {})
    tg_delay = tg_config.get("check_delay", 1.5)

    headers = {"User-Agent": get_random_ua()}
    url = f"https://t.me/+{norm_phone}"

    r = tg_request(url, headers)
    if r:
        has_button = "tgme_action_button_new" in r.text
        has_contact_title = "Telegram: Contact" in r.text
        if has_button or has_contact_title:
            adaptive_delay(tg_delay, tg_delay + 1.0)
            return url
    return None


def generate_usernames(name: str, phone: str = None) -> list[str]:
    """Метод 2: Генерация юзернеймов из названия и телефона."""
    base = _translit(name)
    base = re.sub(r"[^a-z0-9]", "", base)

    if not base:
        return []

    variants = [
        base[:30],
        base.replace("ritualnyeuslugi", "ritual")[:30],
        f"{base[:20]}_ritual",
        f"ritual_{base[:20]}",
    ]

    if phone and len(phone) >= 11:
        variants.append(f"{base[:15]}{phone[-4:]}")
        variants.append(phone)

    # Возвращаем уникальные
    # Сохраняем порядок
    seen = set()
    result = []
    for v in variants:
        if v not in seen and len(v) >= 5:
            seen.add(v)
            result.append(v)

    return result


def find_tg_by_name(name: str, phone: str, config: dict) -> str | None:
    """Генерация и проверка юзернеймов."""
    enrich_config = config.get("enrichment", {})
    tg_config = enrich_config.get("tg_finder", {})
    tg_delay = tg_config.get("check_delay", 1.5)

    variants = generate_usernames(name, phone)
    headers = {"User-Agent": get_random_ua()}

    for v in variants:
        r = tg_request(f"https://t.me/{v}", headers)
        if r and "tgme_page_title" in r.text:
            m1 = re.search(r"tgme_page_description[^>]*>([^<]+)", r.text)
            desc = m1.group(1).lower() if m1 else ""

            m2 = re.search(r"tgme_page_title[^>]*>([^<]+)", r.text)
            title = m2.group(1).lower() if m2 else ""

            keywords = ["ритуал", "похорон", "памятник", "мемориал", "funeral", "angel"]

            if any(k in desc for k in keywords) or any(k in title for k in keywords):
                return f"https://t.me/{v}"
        adaptive_delay(tg_delay, tg_delay + 0.5)

    return None
