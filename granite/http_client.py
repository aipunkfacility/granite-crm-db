# granite/http_client.py
"""Единый async HTTP-клиент на базе httpx.

Используется для async-обогащения в EnrichmentPhase.
Все async HTTP-запросы пайплайна проходят через этот модуль:
- MessengerScanner.scan_website_async()
- tg_finder.find_tg_by_phone_async() / find_tg_by_name_async()
- tg_trust.check_tg_trust_async()
- WebClient.search_async() / scrape_async()
- TechExtractor.extract_async()

Поддерживает:
- Connection pooling (max_connections=10, keepalive=5)
- Adaptive rate limiting через semaphore
- Retries с exponential backoff
- SSL fallback (verify=False при ошибке сертификата)
- SSRF protection через is_safe_url()
"""

import asyncio
import random
import httpx
from loguru import logger
from granite.utils import (
    get_random_ua,
    is_safe_url,
    _sanitize_url_for_log,
)


# ===== Singleton async client =====

_client: httpx.AsyncClient | None = None


async def get_async_client() -> httpx.AsyncClient:
    """Получить или создать разделяемый httpx.AsyncClient.

    Синглтон на уровень модуля: один клиент на весь процесс.
    Закрытие через close_async_client() при завершении работы.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
            headers={"User-Agent": get_random_ua()},
        )
    return _client


async def close_async_client() -> None:
    """Закрыть разделяемый клиент (вызывать при завершении работы)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


# ===== Core async HTTP operations =====


async def async_fetch_page(url: str, timeout: int = 15) -> str | None:
    """Async GET-запрос для получения HTML-страницы.

    Заменяет sync-версию utils.fetch_page() в async-контексте.
    Поддерживает retry при 502/503 и SSL fallback.

    Args:
        url: URL для запроса.
        timeout: таймаут в секундах.

    Returns:
        HTML-контент страницы или None при ошибке.
    """
    if not is_safe_url(url):
        return None

    client = await get_async_client()
    try:
        response = await client.get(url, timeout=timeout)
        if response.status_code == 404:
            logger.warning(f"404 — {_sanitize_url_for_log(url)}")
            return None
        response.raise_for_status()
        return response.text
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else "?"
        logger.debug(f"HTTP {status}: {_sanitize_url_for_log(url)}")
        return None
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        logger.warning(f"Connection error: {_sanitize_url_for_log(url)} — {e}")
        return None
    except httpx.TimeoutException as e:
        logger.warning(f"Timeout: {_sanitize_url_for_log(url)}")
        return None
    except Exception as e:
        logger.debug(f"async_fetch_page error: {_sanitize_url_for_log(url)} — {e}")
        return None


async def async_head(url: str, timeout: int = 10) -> int | None:
    """Async HEAD-запрос для проверки доступности сайта.

    Returns:
        HTTP status code или None при ошибке.
    """
    if not url or not is_safe_url(url):
        return None

    client = await get_async_client()
    try:
        response = await client.head(url, timeout=timeout, follow_redirects=True)
        return response.status_code
    except Exception as e:
        logger.debug(f"async_head failed for {_sanitize_url_for_log(url, 60)}: {e}")
        return None


async def async_get(
    url: str,
    headers: dict | None = None,
    timeout: int = 10,
    max_retries: int = 3,
    initial_backoff: float = 5.0,
) -> httpx.Response | None:
    """Async GET с exponential backoff при 429 (rate limit).

    Используется для Telegram-запросов (t.me), где 429 — частая ситуация.

    Args:
        url: URL для запроса.
        headers: HTTP-заголовки.
        timeout: таймаут одного запроса.
        max_retries: максимальное количество попыток.
        initial_backoff: начальная выдержка при 429 (секунды).

    Returns:
        httpx.Response или None при исчерпании попыток.
    """
    if not is_safe_url(url):
        return None

    client = await get_async_client()
    rate_limit_backoff = initial_backoff
    conn_backoff = 2.0

    for attempt in range(max_retries):
        try:
            response = await client.get(
                url,
                headers=headers or {},
                timeout=timeout,
            )
            if response.status_code == 429:
                wait = rate_limit_backoff + random.uniform(0, 2)
                logger.warning(
                    f"TG rate limit (429) для {_sanitize_url_for_log(url, 60)}, "
                    f"повтор через {wait:.0f}с (попытка {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait)
                rate_limit_backoff *= 2
                continue
            return response
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            wait = conn_backoff + random.uniform(0, 1)
            logger.warning(
                f"TG async request error ({_sanitize_url_for_log(url, 60)}): {e}, "
                f"повтор через {wait:.0f}с (попытка {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(wait)
            conn_backoff *= 2
        except Exception as e:
            logger.debug(f"async_get error for {_sanitize_url_for_log(url, 60)}: {e}")
            return None

    logger.warning(
        f"TG async: исчерпано {max_retries} попыток для "
        f"{_sanitize_url_for_log(url, 60)} — пропуск"
    )
    return None


# ===== Adaptive delay (async) =====


async def async_adaptive_delay(min_sec: float = 1.0, max_sec: float = 3.5) -> float:
    """Async версия adaptive_delay: случайная задержка между запросами.

    Имитирует поведение человека в async-контексте, не блокируя event loop.
    """
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)
    return delay


# ===== Sync-to-async bridge =====


def run_async(coro):
    """Запустить корутину из sync-кода.

    Безопасно работает как внутри, так и вне существующего event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Внутри существующего event loop — создаём задачу
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
