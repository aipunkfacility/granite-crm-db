# enrichers/tg_trust.py
import requests
from loguru import logger
from granite.utils import adaptive_delay, get_random_ua
from granite.enrichers.tg_finder import tg_request
from granite.enrichers._tg_common import TG_MAX_RETRIES, TG_INITIAL_BACKOFF


def check_tg_trust(url: str) -> dict:
    """Анализирует Telegram-профиль: живой ли это контакт."""
    if not url:
        return {"trust_score": 0}

    headers = {"User-Agent": get_random_ua()}

    result = {
        "has_avatar": False,
        "has_description": False,
        "is_bot": False,
        "is_channel": False,
        "trust_score": 0,
    }

    r = tg_request(url, headers)
    if not r:
        return result

    html = r.text

    if "tgme_page_photo_image" in html:
        result["has_avatar"] = True
        result["trust_score"] += 1

    if "tgme_page_description" in html:
        result["has_description"] = True
        result["trust_score"] += 1

    if "tgme_page_extra" in html and ("subscribers" in html or "members" in html):
        result["is_channel"] = True
        result["trust_score"] -= 1

    if "tgme_page_extra" in html and "bot" in html.lower():
        result["is_bot"] = True
        result["trust_score"] -= 1

    adaptive_delay(1.0, 2.0)
    return result
