# enrichers/tg_trust.py
import requests
from bs4 import BeautifulSoup
from loguru import logger
from granite.utils import adaptive_delay, get_random_ua
from granite.enrichers.tg_finder import tg_request
from granite.enrichers._tg_common import get_tg_config
from granite.http_client import async_get, async_adaptive_delay


def check_tg_trust(url: str, config: dict | None = None) -> dict:
    """Анализирует Telegram-профиль: живой ли это контакт."""
    if not url or "t.me/" not in url:
        return {
            "has_avatar": False,
            "has_description": False,
            "is_bot": False,
            "is_channel": False,
            "trust_score": 0,
        }

    headers = {"User-Agent": get_random_ua()}

    result = {
        "has_avatar": False,
        "has_description": False,
        "is_bot": False,
        "is_channel": False,
        "trust_score": 0,
    }

    # Читаем timeout/retries из конфига, если передан
    if config:
        tg_cfg = get_tg_config(config)
        r = tg_request(url, headers, timeout=tg_cfg["request_timeout"],
                       max_retries=tg_cfg["max_retries"],
                       initial_backoff=tg_cfg["initial_backoff"])
    else:
        r = tg_request(url, headers)

    if not r:
        return result
    if r.status_code != 200:
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    # Avatar: проверяем наличие изображения профиля
    if soup.select(".tgme_page_photo_image"):
        result["has_avatar"] = True
        result["trust_score"] += 1

    # Description: проверяем наличие блока описания
    if soup.select(".tgme_page_description"):
        result["has_description"] = True
        result["trust_score"] += 1

    # Channel: проверяем наличие информации о подписчиках
    extra = soup.select(".tgme_page_extra")
    if extra:
        extra_text = extra[0].get_text().lower()
        if "subscribers" in extra_text or "members" in extra_text:
            result["is_channel"] = True
            result["trust_score"] -= 1

    # Bot: проверяем класс бота
    if soup.select(".tgme_page_bot_button"):
        result["is_bot"] = True
        result["trust_score"] -= 1

    # Задержка из конфига
    if config:
        tg_trust_cfg = config.get("enrichment", {}).get("tg_trust", {})
        delay_min = tg_trust_cfg.get("check_delay_min", 1.0)
        delay_max = tg_trust_cfg.get("check_delay_max", 2.0)
    else:
        delay_min, delay_max = 1.0, 2.0

    adaptive_delay(delay_min, delay_max)
    return result


async def check_tg_trust_async(url: str, config: dict | None = None) -> dict:
    """Async версия check_tg_trust — использует httpx.AsyncClient.

    Идентична по логике check_tg_trust(), но неблокирующая.
    """
    if not url or "t.me/" not in url:
        return {
            "has_avatar": False,
            "has_description": False,
            "is_bot": False,
            "is_channel": False,
            "trust_score": 0,
        }

    headers = {"User-Agent": get_random_ua()}

    result = {
        "has_avatar": False,
        "has_description": False,
        "is_bot": False,
        "is_channel": False,
        "trust_score": 0,
    }

    if config:
        tg_cfg = get_tg_config(config)
        r = await async_get(
            url, headers,
            timeout=tg_cfg["request_timeout"],
            max_retries=tg_cfg["max_retries"],
            initial_backoff=tg_cfg["initial_backoff"],
        )
    else:
        r = await async_get(url, headers)

    if not r:
        return result
    if r.status_code != 200:
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    if soup.select(".tgme_page_photo_image"):
        result["has_avatar"] = True
        result["trust_score"] += 1

    if soup.select(".tgme_page_description"):
        result["has_description"] = True
        result["trust_score"] += 1

    extra = soup.select(".tgme_page_extra")
    if extra:
        extra_text = extra[0].get_text().lower()
        if "subscribers" in extra_text or "members" in extra_text:
            result["is_channel"] = True
            result["trust_score"] -= 1

    if soup.select(".tgme_page_bot_button"):
        result["is_bot"] = True
        result["trust_score"] -= 1

    if config:
        tg_trust_cfg = config.get("enrichment", {}).get("tg_trust", {})
        delay_min = tg_trust_cfg.get("check_delay_min", 1.0)
        delay_max = tg_trust_cfg.get("check_delay_max", 2.0)
    else:
        delay_min, delay_max = 1.0, 2.0

    await async_adaptive_delay(delay_min, delay_max)
    return result
