# dedup/validator.py
import re
import ipaddress
from urllib.parse import urlparse
import requests
from granite.utils import normalize_phone, check_site_alive
from loguru import logger

# Private/loopback IP ranges to block (SSRF protection)
ALLOWED_HOSTS = frozenset(
    [
        "localhost",
        "127.0.0.1",
        "::1",
    ]
)

# Email validation regex (precompiled for performance)
_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # AWS metadata
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::1/128"),
]


def _is_internal_url(url: str) -> bool:
    """Проверка что URL не указывает на internal/private сеть (SSRF protection)."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return True
        if host in ALLOWED_HOSTS:
            return True
        try:
            ip = ipaddress.ip_address(host)
            for network in BLOCKED_IP_RANGES:
                if ip in network:
                    return True
        except ValueError:
            pass
        return False
    except Exception:
        return True


def validate_phone(phone: str) -> bool:
    """Проверка что телефон валиден: 11 цифр, начинается с 7."""
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    return digits.startswith("7") and len(digits) == 11


def validate_phones(phones: list[str]) -> list[str]:
    """Оставляем только валидные и нормализованные номера."""
    result = []
    for p in phones:
        norm = normalize_phone(p)
        if norm and validate_phone(norm):
            result.append(norm)
    # Дедупликация с сохранением порядка
    seen: set = set()
    unique = []
    for p in result:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def validate_website(url: str) -> tuple[str | None, int | None]:
    """HEAD-запрос к сайту. Возвращает (url, status_code).

    Если сайт мёртв — возвращает (url, None).
    Нормализует URL: добавляет https:// если нет схемы.
    """
    if not url or url.strip() in ("", "-", "N/A"):
        return None, None

    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    # SSRF protection: block internal/private URLs
    if _is_internal_url(url):
        logger.debug(f"  SSRF blocked: {url}")
        return None, None

    # Убираем мусор который иногда прилетает из скреперов
    if " " in url or "\n" in url:
        url = url.split()[0]

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
    return list(dict.fromkeys(e for e in emails if validate_email(e)))
