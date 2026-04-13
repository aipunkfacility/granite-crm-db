# category_finder.py — поиск поддоменов jsprav через API
# POST /api/cities/ с JSON {"q":"Город"} → [{name, region, url}]
import yaml
import re
import threading
import time
import requests
from pathlib import Path
from loguru import logger
from granite.utils import is_safe_url

__all__ = [
    "CACHE_PATH",
    "DEFAULT_HEADERS",
    "JSPRAV_CATEGORY",
    "find_jsprav",
    "discover_categories",
    "get_categories",
    "get_subdomain",
]

CACHE_PATH = str(Path(__file__).parent.parent / "data" / "category_cache.yaml")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
JSPRAV_CATEGORY = "izgotovlenie-i-ustanovka-pamyatnikov-i-nadgrobij"

# Thread-safe: session stored per-thread via threading.local()
_jsprav_local = threading.local()


def _get_jsprav_session() -> requests.Session | None:
    """Создать сессию с CSRF-токеном для jsprav.ru (thread-safe).

    При таймаутах делает до 3 повторных попыток с паузой 5 сек.
    Возвращает None если все попытки провалились.
    """
    if not hasattr(_jsprav_local, "session") or _jsprav_local.session is None:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)

        # Получаем главную — там CSRF-токен в JS и cookie
        # Ретраи при таймаутах
        for attempt in range(3):
            try:
                r = session.get("https://jsprav.ru/", timeout=20)
                if r.status_code == 200:
                    m = re.search(r'window\["csrf_token"\]\s*=\s*"([^"]+)"', r.text)
                    if m:
                        session.headers["X-CSRFToken"] = m.group(1)
                        logger.info("  jsprav.ru: CSRF получен")
                else:
                    logger.warning("  jsprav.ru: главная недоступна")
                break
            except (requests.Timeout, requests.ConnectionError) as e:
                logger.warning(
                    f"  jsprav.ru: попытка {attempt + 1}/3 — {e}"
                )
                if attempt < 2:
                    time.sleep(5)
                else:
                    logger.error(
                        "  jsprav.ru: все 3 попытки провалились, сессия не создана"
                    )
                    return None

        _jsprav_local.session = session
    return _jsprav_local.session


def _search_city(city: str) -> dict | None:
    """POST /api/cities/ → поиск города по названию.

    Возвращает {"name": "Камышин", "region": "Волгоградская область", "url": "http://kamyishin.jsprav.ru"}
    или None.
    """
    session = _get_jsprav_session()
    if session is None:
        logger.warning(f"    jsprav API: сессия недоступна, пропуск {city}")
        return None
    try:
        r = session.post(
            "https://jsprav.ru/api/cities/",
            json={"q": city},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning(f"    jsprav API: {r.status_code} для {city}")
            return None

        results = r.json()
        if not results:
            return None

        # Ищем точное совпадение по названию
        for item in results:
            name = item.get("name", "").strip()
            if name == city:
                return item

        # Если нет точного — берём первый с похожим названием (не менее 6 символов совпадения)
        first = results[0]
        city_lower = city.lower()
        first_name_lower = first.get("name", "").lower()
        if len(city_lower) >= 6 and len(first_name_lower) >= 6:
            # Проверяем минимум 6 общих символов или начало
            if city_lower[:6] == first_name_lower[:6]:
                return first
        elif len(first_name_lower) >= 3 and (city_lower.startswith(first_name_lower) or first_name_lower.startswith(
            city_lower
        )):
            return first

        return None
    except Exception as e:
        logger.warning(f"    jsprav API: ошибка — {e}")
        return None


def _extract_subdomain(url: str) -> str:
    """http://kamyishin.jsprav.ru → kamyishin"""
    m = re.search(r"https?://([a-z0-9-]+)\.jsprav\.ru", url, re.IGNORECASE)
    return m.group(1) if m else ""


def _check_head(url: str, timeout: int = 8) -> bool:
    if not is_safe_url(url):
        logger.warning(f"_check_head: skipping unsafe URL '{url}'")
        return False
    try:
        r = requests.head(
            url, timeout=timeout, headers=DEFAULT_HEADERS, allow_redirects=True
        )
        return r.status_code == 200
    except Exception as e:
        logger.debug(f"_check_head failed for '{url}': {e}")
        return False


def find_jsprav(city: str, config: dict) -> dict:
    """Найти поддомен и проверить категорию.

    1. Ищем через API /api/cities/
    2. Проверяем категорию HEAD-запросом
    """
    # Сначала config subdomain_map (для нестандартных)
    subdomain_map = config.get("sources", {}).get("jsprav", {}).get("subdomain_map", {})
    subdomain = subdomain_map.get(city.lower())

    if not subdomain:
        # Поиск через API
        result = _search_city(city)
        if result:
            subdomain = _extract_subdomain(result["url"])
            region = result.get("region", "")
            logger.info(f"    jsprav {city}: найден через API ({region})")
        else:
            logger.info(f"    jsprav {city}: не найден")
            return {}

    # Проверяем категорию
    cat_url = f"https://{subdomain}.jsprav.ru/{JSPRAV_CATEGORY}/"
    if _check_head(cat_url):
        logger.info(f"    jsprav {city}: {subdomain}.jsprav.ru — категория OK")
        return {"subdomain": subdomain, "categories": [JSPRAV_CATEGORY]}

    logger.warning(f"    jsprav {city}: поддомен {subdomain}, категория не найдена")
    return {"subdomain": subdomain, "categories": []}


def _load_cache() -> dict:
    path = Path(CACHE_PATH)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_cache(cache: dict):
    Path(CACHE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cache, f, allow_unicode=True, default_flow_style=False)


_cache_lock = threading.Lock()


def discover_categories(cities: list[str], config: dict) -> dict:
    """Поиск поддоменов и категорий для городов области."""
    with _cache_lock:
        cache = _load_cache()
        found_any = False

        for city in cities:
            logger.info(f"  Поиск категорий: {city}")

            cached = cache.get("jsprav", {}).get(city, [])
            if cached:
                logger.info(f"    jsprav {city}: из кэша — {cached}")
                continue

            try:
                result = find_jsprav(city, config)
            except Exception as e:
                logger.warning(f"  Пропуск {city}: jsprav недоступен — {e}")
                continue

            if result.get("categories"):
                cache.setdefault("jsprav", {})[city] = result["categories"]
                cache.setdefault("_subdomains", {}).setdefault("jsprav", {})[city] = result[
                    "subdomain"
                ]
                found_any = True

        if found_any:
            _save_cache(cache)
            logger.info(f"Кэш категорий обновлён: {CACHE_PATH}")
        else:
            logger.info("Кэш категорий не изменён")

        return cache


def get_categories(
    cache: dict, source: str, city: str, fallback: list[str] | None = None
) -> list[str]:
    found = cache.get(source, {}).get(city, [])
    return found if found else (fallback or [])


def get_subdomain(
    cache: dict, source: str, city: str, config: dict | None = None
) -> str | None:
    subdomain = cache.get("_subdomains", {}).get(source, {}).get(city)
    if subdomain:
        return subdomain
    if source == "jsprav" and config:
        subdomain_map = (
            config.get("sources", {}).get("jsprav", {}).get("subdomain_map", {})
        )
        return subdomain_map.get(city.lower())
    return None
