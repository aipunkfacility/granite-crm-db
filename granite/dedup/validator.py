# dedup/validator.py
import re
import ipaddress
from urllib.parse import urlparse
from granite.utils import normalize_phone, check_site_alive, is_safe_url
from loguru import logger

# Email validation regex (precompiled for performance)
_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def validate_phone(phone: str) -> bool:
    """Проверка что телефон валиден: 11 цифр, начинается с 7."""
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    return digits.startswith("7") and len(digits) == 11


def validate_phones(phones: list[str]) -> list[str]:
    """Оставляем только валидные и нормализованные номера."""
    seen: set[str] = set()
    unique = []
    for p in phones:
        norm = normalize_phone(p)
        if norm and validate_phone(norm) and norm not in seen:
            seen.add(norm)
            unique.append(norm)
    return unique


def validate_website(url: str) -> tuple[str | None, int | None]:
    """HEAD-запрос к сайту. Возвращает (url, status_code).

    Если сайт мёртв — возвращает (url, None).
    Нормализует URL: добавляет https:// если нет схемы.
    """
    if not url or url.strip().lower() in ("", "-", "n/a"):
        return None, None

    url = url.strip()
    # Clean whitespace/null bytes before scheme check
    url = re.sub(r'[\s\x00]+', '', url).split()[0]
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    # SSRF protection: single check via is_safe_url (single source of truth)
    if not is_safe_url(url):
        logger.debug(f"  SSRF blocked: {url}")
        return None, None

    status = check_site_alive(url)
    if status is None:
        logger.debug(f"  Site unreachable: {url}")
    return url, status


def validate_email(email: str) -> bool:
    """Базовая валидация email по регулярке."""
    if not email:
        return False
    return bool(_EMAIL_PATTERN.match(email.strip()))


def validate_emails(emails: list[str]) -> list[str]:
    """Фильтрация валидных email с дедупликацией."""
    return list(dict.fromkeys(e.strip() for e in emails if validate_email(e)))
