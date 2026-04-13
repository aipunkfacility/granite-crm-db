# utils.py
import re
import time
import random
from urllib.parse import urlparse
from rapidfuzz import fuzz
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
import requests
from loguru import logger


# ===== User-Agent =====
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
]


def get_random_ua() -> str:
    """Возвращает случайный User-Agent из списка."""
    return random.choice(_USER_AGENTS)


# Словарь транслитерации: сначала многосимвольные, потом односимвольные
# Порядок важен — щ, ш, ч, ж, ю, я обрабатываются до остальных
TRANSLIT_MAP = [
    ('щ', 'shch'), ('ш', 'sh'), ('ч', 'ch'), ('ж', 'zh'),
    ('ю', 'yu'), ('я', 'ya'), ('ё', 'yo'), ('э', 'e'),
    ('х', 'kh'), ('ц', 'ts'),
    ('а', 'a'), ('б', 'b'), ('в', 'v'), ('г', 'g'), ('д', 'd'),
    ('е', 'e'), ('з', 'z'), ('и', 'i'), ('й', 'y'), ('к', 'k'),
    ('л', 'l'), ('м', 'm'), ('н', 'n'), ('о', 'o'), ('п', 'p'),
    ('р', 'r'), ('с', 's'), ('т', 't'), ('у', 'u'), ('ф', 'f'),
    ('ъ', ''), ('ы', 'y'), ('ь', ''),
]


def slugify(text: str) -> str:
    """Транслитерация кириллицы в латиницу для URL (slug).
    Пример: "Волгоград" -> "volgograd", "Санкт-Петербург" -> "sankt-peterburg"
    """
    if not text:
        return ""
    
    text = text.lower().strip()
    for cyr, lat in TRANSLIT_MAP:
        text = text.replace(cyr, lat)
    
    # Очистка от спецсимволов, замена пробелов на дефис
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text).strip('-')
    
    return text


def adaptive_delay(min_sec: float = 1.0, max_sec: float = 3.5) -> float:
    """Случайная задержка между запросами. Имитирует поведение человека.

    Диапазон по умолчанию 1.0–3.5с вместо фиксированного sleep.
    Для Telegram использовать min=1.5 (из config: tg_finder.check_delay).
    """
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)
    return delay


def normalize_phone(phone: str) -> str | None:
    """Нормализация телефона к формату E.164: 7XXXXXXXXXX (без +).

    Обрабатывает: +79031234567, 89031234567, 9031234567,
                  +7 (903) 123-45-67, 8 (903) 123 45 67
    Возвращает: "79031234567" или None
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    # Если начинается с 8 (российский формат) — заменяем на 7
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    # Если 10 цифр — добавляем 7 (местный номер)
    elif len(digits) == 10:
        digits = "7" + digits
    # Проверяем валидность: 11 цифр, начинается с 7
    if digits.startswith("7") and len(digits) == 11:
        return digits
    return None


def normalize_phones(phones: list[str]) -> list[str]:
    """Нормализация списка телефонов с дедупликацией."""
    result = []
    seen = set()
    for p in phones:
        norm = normalize_phone(p)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def extract_phones(text: str) -> list[str]:
    """Извлечение российских телефонных номеров из текста.

    Ищет номера формата: +7(903)123-45-67, 8 903 123 45 67,
    79031234567 и вариации с пробелами/дефисами/скобками.

    Returns:
        Список уникальных найденных телефонов (в оригинальном формате из текста).
    """
    if not text:
        return []
    return list(dict.fromkeys(re.findall(
        r"(\+?7[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2})",
        text,
    )))


def extract_emails(text: str) -> list[str]:
    """Извлечение email из текста."""
    if not text:
        return []
    return list(dict.fromkeys(re.findall(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        text, re.IGNORECASE
    )))


def extract_domain(url: str) -> str | None:
    """Извлечение домена из URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain if domain else None
    except Exception as e:
        logger.debug(f"extract_domain failed for '{url}': {e}")
        return None


def compare_names(name_a: str, name_b: str, threshold: int = 88) -> bool:
    """Сравнение названий компаний. Возвращает True если схожи выше порога.

    Использует token_sort_ratio из rapidfuzz — устойчив к перестановке слов:
    "Гранит-Мастер Иванов" ≈ "Иванов Гранит-Мастер"
    """
    if not name_a or not name_b:
        return False
    a = name_a.lower().strip()
    b = name_b.lower().strip()
    # Точное совпадение (после нормализации)
    if a == b:
        return True
    # Fuzzy match
    score = fuzz.token_sort_ratio(a, b)
    return score >= threshold


def extract_street(address: str) -> str:
    """Базовое извлечение улицы из адреса.

    "г. Новосибирск, ул. Ленина, 45" → "ленина"
    "Новосибирск, проспект Маркса 12" → "маркса"
    """
    if not address:
        return ""
    address_lower = address.lower()
    # Убираем город
    for prefix in ["г. ", "город "]:
        if prefix in address_lower:
            address_lower = address_lower.split(prefix, 1)[-1]
            break
    # Извлекаем улицу
    match = re.search(r"(?:ул\.?|улица|пр-т\.?|проспект|пер\.?|переулок)\s*(.+?)[,\d]", address_lower)
    if match:
        return match.group(1).strip()
    return address_lower.split(",")[0].strip() if "," in address_lower else address_lower


# ===== URL Sanitization for Logs =====

def _sanitize_url_for_log(url: str, max_len: int = 80) -> str:
    """Sanitize URL before logging to avoid leaking PII.

    1. Strip query parameters (may contain phone numbers, session tokens)
    2. For wa.me/send?phone=... patterns, replace phone digits with ***
    3. Truncate to max_len characters
    """
    if not url or not isinstance(url, str):
        return "<no url>"
    # Handle wa.me phone pattern before stripping query params
    sanitized = re.sub(r'(wa\.me/send\?phone=)\d+', r'\1***', url)
    # Strip query parameters
    sanitized = sanitized.split('?')[0]
    # Strip fragment
    sanitized = sanitized.split('#')[0]
    # Truncate
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len] + "..."
    return sanitized


# ===== HTTP-запросы с retry =====

class NetworkError(Exception):
    """Сайт не отвечает после всех попыток."""
    pass


class SiteNotFoundError(Exception):
    """Сайт возвращает 404 — не нужно повторять."""
    pass


# Retry для временных ошибок (502, 503, timeout, connection)
# НЕ retry для 404, 403 и 429 (заблокировали / rate limit)
def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, SiteNotFoundError):
        return False
    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        if response is not None and response.status_code in (403, 404, 429):
            return False
    return True


# ИСПРАВЛЕНО: retry_if_exception (callable) вместо retry_if_exception_type (тип)
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception(_should_retry),
    reraise=True,
)
def fetch_page(url: str, timeout: int = 15) -> str:
    """Получение HTML страницы с retry и логированием.

    Raises:
        NetworkError: после 3 неудачных попыток
        SiteNotFoundError: при 404
        ValueError: если URL не прошёл проверку безопасности (SSRF)
    """
    if not is_safe_url(url):
        raise ValueError(f"URL blocked by safety check: {url[:60]}")
    headers = {"User-Agent": get_random_ua()}
    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if response.status_code == 404:
            logger.warning(f"404 — {_sanitize_url_for_log(url)}")
            raise SiteNotFoundError(f"404: {url}")
        response.raise_for_status()
        return response.text
    except requests.exceptions.SSLError as e:
        # SSL verification failed (self-signed cert, hostname mismatch) —
        # retry with verify=False as fallback
        logger.debug(f"SSL error for {_sanitize_url_for_log(url)}, retrying with verify=False")
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            response = requests.get(url, headers=headers, timeout=timeout,
                                   allow_redirects=True, verify=False)
            if response.status_code == 404:
                raise SiteNotFoundError(f"404: {url}")
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e2:
            logger.warning(f"SSL fallback also failed: {_sanitize_url_for_log(url)} — {e2}")
            raise NetworkError(f"SSL failed: {url}") from e2
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"Connection error: {_sanitize_url_for_log(url)} — {e}")
        raise NetworkError(f"Connection failed: {url}") from e
    except requests.exceptions.Timeout:
        logger.warning(f"Timeout: {_sanitize_url_for_log(url)}")
        raise NetworkError(f"Timeout: {url}")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        logger.warning(f"HTTP {status}: {_sanitize_url_for_log(url)}")
        raise


def check_site_alive(url: str) -> int | None:
    """HEAD-запрос для проверки, живой ли сайт. Возвращает статус-код или None.

    Использует allow_redirects=True для корректной обработки HTTP→HTTPS
    редиректов (301/302). Без follow redirects сайты с HTTP→HTTPS считались
    бы «мёртвыми», и обогащение (мессенджеры, CMS) бы пропускалось.
    """
    if not url:
        return None
    if not is_safe_url(url):
        raise ValueError(f"URL blocked by safety check: {url[:60]}")
    try:
        headers = {"User-Agent": get_random_ua()}
        r = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        return r.status_code
    except requests.exceptions.SSLError:
        # SSL verification failed — retry with verify=False
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            headers = {"User-Agent": get_random_ua()}
            r = requests.head(url, headers=headers, timeout=10, allow_redirects=True, verify=False)
            return r.status_code
        except Exception:
            return None
    except Exception as e:
        logger.debug(f"check_site_alive failed for '{_sanitize_url_for_log(url, 60)}': {e}")
        return None


def sanitize_filename(name: str) -> str:
    """Санитизация имени файла: убираем path traversal и небезопасные символы.

    Используется в экспортерах и дедуп-модулях для безопасного создания файлов
    из пользовательских данных (названия городов, компаний).
    """
    if not name:
        return "unnamed"
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_-]", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")
    return name[:100]


def pick_best_value(*values: str) -> str:
    """Из нескольких значений берёт самое длинное (полное)."""
    candidates = [v.strip() for v in values if v and v.strip()]
    if not candidates:
        return ""
    return max(candidates, key=len)


# ===== URL Safety =====

def is_safe_url(url: str) -> bool:
    """Check that URL is not pointing to internal/private resources.

    Blocks: localhost, private IPs (RFC 1918), link-local, loopback,
    cloud-metadata (169.254), CGNAT (100.64/10), IPv6 ULA (fd00::/7),
    and other internal ranges.  Uses ipaddress module for reliable
    IPv4/IPv6 parsing (handles IPv6-mapped IPv4, brackets, etc.).
    """
    if not url or not isinstance(url, str):
        return False
    cleaned = re.sub(r'[\s\x00]+', '', url).split()[0]
    if not cleaned:
        return False
    try:
        parsed = urlparse(cleaned)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    hostname_lower = hostname.lower()
    # Block known internal hostnames
    if hostname_lower in ("localhost", "metadata.google.internal", "metadata"):
        return False
    # Try ipaddress-based check (handles IPv4, IPv6, brackets, mapped addrs)
    try:
        import ipaddress
        ip = ipaddress.ip_address(hostname_lower)
        # Handle IPv6-mapped IPv4 (e.g. ::ffff:127.0.0.1)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        private_ranges = [
            ipaddress.ip_network("127.0.0.0/8"),      # loopback
            ipaddress.ip_network("10.0.0.0/8"),        # RFC 1918
            ipaddress.ip_network("172.16.0.0/12"),     # RFC 1918
            ipaddress.ip_network("192.168.0.0/16"),    # RFC 1918
            ipaddress.ip_network("169.254.0.0/16"),    # link-local / cloud metadata
            ipaddress.ip_network("0.0.0.0/8"),         # "this" network
            ipaddress.ip_network("100.64.0.0/10"),     # CGNAT / shared address space
            ipaddress.ip_network("192.0.0.0/24"),      # IETF protocol assignments
            ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1 (documentation)
            ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2
            ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3
            ipaddress.ip_network("::1/128"),            # loopback
            ipaddress.ip_network("::/128"),            # unspecified
            ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
            ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
        ]
        for net in private_ranges:
            if ip in net:
                return False
    except ValueError:
        pass  # hostname is not an IP — continue with string checks below

    # Fast string-based checks for hostnames that resolve to internal IPs
    # (defense-in-depth; ipaddress above handles pure-IP hostnames)
    if hostname_lower.startswith(("127.", "10.", "192.168.", "169.254.", "0.")):
        return False
    if hostname_lower.startswith("172."):
        parts = hostname_lower.split(".")
        if len(parts) >= 2:
            try:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return False
            except ValueError:
                pass
    if hostname_lower.startswith("100."):
        parts = hostname_lower.split(".")
        if len(parts) >= 2:
            try:
                second = int(parts[1])
                if 64 <= second <= 127:
                    return False
            except ValueError:
                pass
    # Block IPv6 private range prefixes (e.g. fd12:..., fe80:...)
    if hostname_lower.startswith("fc") or hostname_lower.startswith("fe80"):
        return False
    return True


def is_safe_link_url(url: str) -> bool:
    """Check URL is safe for embedding in markdown links / hrefs.
    Rejects javascript:, data:, vbscript: and other dangerous schemes.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)
