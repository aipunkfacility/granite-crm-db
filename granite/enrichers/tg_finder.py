# enrichers/tg_finder.py
import re
import time
import random
from granite.utils import adaptive_delay, TRANSLIT_MAP, get_random_ua, normalize_phone, is_safe_url, _sanitize_url_for_log
import requests
from loguru import logger
from granite.enrichers._tg_common import TG_MAX_RETRIES, TG_INITIAL_BACKOFF, get_tg_config
from granite.http_client import async_get, async_adaptive_delay


def tg_request(url: str, headers: dict, timeout: int = 10,
               max_retries: int = TG_MAX_RETRIES,
               initial_backoff: int = TG_INITIAL_BACKOFF) -> requests.Response | None:
    """HTTP GET с экспоненциальной выдержкой при HTTP 429 (Too Many Requests).

    Telegram блокирует IP при агрессивном парсинге. При получении 429 ждём
    с экспоненциальной выдержкой (5, 10, 20, 40, 80 сек). Сетевые ошибки
    (connection, timeout) также ретраятся с короткой выдержкой (2, 4, 8 сек).
    После исчерпания попыток — логируем warning и возвращаем None.
    """
    if not is_safe_url(url):
        return None
    rate_limit_backoff = initial_backoff
    conn_backoff = 2
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 429:
                wait = rate_limit_backoff + random.uniform(0, 2)
                logger.warning(
                    f"TG rate limit (429) для {_sanitize_url_for_log(url, 60)}, "
                    f"повтор через {wait:.0f}с (попытка {attempt + 1}/{max_retries})"
                )
                time.sleep(wait)
                rate_limit_backoff *= 2
                continue
            return r
        except requests.RequestException as e:
            wait = conn_backoff + random.uniform(0, 1)
            logger.warning(
                f"TG request error ({_sanitize_url_for_log(url, 60)}): {e}, "
                f"повтор через {wait:.0f}с (попытка {attempt + 1}/{max_retries})"
            )
            time.sleep(wait)
            conn_backoff *= 2
    logger.warning(f"TG: исчерпано {max_retries} попыток для {_sanitize_url_for_log(url, 60)} — пропуск")
    return None


def _translit(text: str) -> str:
    """Транслитерация кириллицы в латиницу. Использует тот же словарь что и slugify()."""
    if not text:
        return ""
    text = text.lower()
    for cyr, lat in TRANSLIT_MAP:
        text = text.replace(cyr, lat)
    return text


def find_tg_by_phone(phone: str, config: dict) -> str | None:
    """Метод 1: Прямая привязка телефона (t.me/+7XXX)."""
    if not phone or not isinstance(phone, str) or len(phone) < 11:
        return None

    # Нормализация телефона перед построением URL
    norm_phone = normalize_phone(phone)
    if not norm_phone or len(norm_phone) != 11:
        return None

    tg_cfg = get_tg_config(config)
    headers = {"User-Agent": get_random_ua()}
    url = f"https://t.me/+{norm_phone}"

    r = tg_request(url, headers, timeout=tg_cfg["request_timeout"],
                   max_retries=tg_cfg["max_retries"],
                   initial_backoff=tg_cfg["initial_backoff"])
    if r:
        has_button = "tgme_action_button_new" in r.text
        has_contact_title = "Telegram: Contact" in r.text
        if has_button or has_contact_title:
            adaptive_delay(tg_cfg["check_delay"], tg_cfg["check_delay"] + 1.0)
            return url
    return None


def generate_usernames(name: str, phone: str | None = None) -> list[str]:
    """Метод 2: Генерация юзернеймов из названия и телефона."""
    if not name:
        return []
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
    if not name:
        return None

    tg_cfg = get_tg_config(config)
    variants = generate_usernames(name, phone)
    headers = {"User-Agent": get_random_ua()}

    for v in variants:
        adaptive_delay(tg_cfg["check_delay"], tg_cfg["check_delay"] + 0.5)
        r = tg_request(f"https://t.me/{v}", headers,
                       timeout=tg_cfg["request_timeout"],
                       max_retries=tg_cfg["max_retries"],
                       initial_backoff=tg_cfg["initial_backoff"])
        if r and "tgme_page_title" in r.text:
            m1 = re.search(r"tgme_page_description[^>]*>([^<]+)", r.text)
            desc = m1.group(1).lower() if m1 else ""

            m2 = re.search(r"tgme_page_title[^>]*>([^<]+)", r.text)
            title = m2.group(1).lower() if m2 else ""

            keywords = ["ритуал", "похорон", "памятник", "мемориал", "funeral", "angel"]

            if any(k in desc for k in keywords) or any(k in title for k in keywords):
                return f"https://t.me/{v}"

    return None


# ===== Async variants =====


async def find_tg_by_phone_async(phone: str, config: dict) -> str | None:
    """Async версия find_tg_by_phone — использует httpx.AsyncClient.

    Идентична по логике find_tg_by_phone(), но неблокирующая.
    """
    if not phone or not isinstance(phone, str) or len(phone) < 11:
        return None

    norm_phone = normalize_phone(phone)
    if not norm_phone or len(norm_phone) != 11:
        return None

    tg_cfg = get_tg_config(config)
    headers = {"User-Agent": get_random_ua()}
    url = f"https://t.me/+{norm_phone}"

    r = await async_get(
        url, headers,
        timeout=tg_cfg["request_timeout"],
        max_retries=tg_cfg["max_retries"],
        initial_backoff=tg_cfg["initial_backoff"],
    )
    if r:
        has_button = "tgme_action_button_new" in r.text
        has_contact_title = "Telegram: Contact" in r.text
        if has_button or has_contact_title:
            await async_adaptive_delay(tg_cfg["check_delay"], tg_cfg["check_delay"] + 1.0)
            return url
    return None


async def find_tg_by_name_async(name: str, phone: str, config: dict) -> str | None:
    """Async версия find_tg_by_name — использует httpx.AsyncClient.

    Идентична по логике find_tg_by_name(), но неблокирующая.
    """
    if not name:
        return None

    tg_cfg = get_tg_config(config)
    variants = generate_usernames(name, phone)
    headers = {"User-Agent": get_random_ua()}

    for v in variants:
        await async_adaptive_delay(tg_cfg["check_delay"], tg_cfg["check_delay"] + 0.5)
        r = await async_get(
            f"https://t.me/{v}", headers,
            timeout=tg_cfg["request_timeout"],
            max_retries=tg_cfg["max_retries"],
            initial_backoff=tg_cfg["initial_backoff"],
        )
        if r and "tgme_page_title" in r.text:
            m1 = re.search(r"tgme_page_description[^>]*>([^<]+)", r.text)
            desc = m1.group(1).lower() if m1 else ""

            m2 = re.search(r"tgme_page_title[^>]*>([^<]+)", r.text)
            title = m2.group(1).lower() if m2 else ""

            keywords = ["ритуал", "похорон", "памятник", "мемориал", "funeral", "angel"]

            if any(k in desc for k in keywords) or any(k in title for k in keywords):
                return f"https://t.me/{v}"

    return None
