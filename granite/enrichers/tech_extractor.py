# enrichers/tech_extractor.py
import re
from granite.utils import fetch_page, is_safe_url
from granite.http_client import async_fetch_page
from loguru import logger

class TechExtractor:
    """Извлекает движок сайта (CMS) и наличие виджетов типа Marquiz."""

    def __init__(self, config: dict):
        pass  # config kept for API compatibility

    def extract(self, url: str) -> dict:
        result = {
            "cms": "unknown",
            "has_marquiz": False
        }
        
        if not url:
            return result

        if not is_safe_url(url):
            return result
            
        try:
            html = fetch_page(url, timeout=10)
            if not html:
                return result
                
            # Проверка CMS (case-insensitive)
            html_lower = html.lower()
            if "wp-content" in html_lower or "wordpress" in html_lower:
                result["cms"] = "wordpress"
            elif "bitrix" in html_lower or "1c-bitrix" in html_lower:
                result["cms"] = "bitrix"
            elif "tilda.ws" in html_lower or "tilda.cc" in html_lower or "created on tilda" in html_lower:
                result["cms"] = "tilda"
            elif "flexbe" in html_lower:
                result["cms"] = "flexbe"
            elif "lpmotor" in html_lower:
                result["cms"] = "lpmotor"
            elif "joomla" in html_lower:
                result["cms"] = "joomla"
            elif "opencart" in html_lower or "route=common/home" in html_lower:
                result["cms"] = "opencart"
                
            # Проверка Marquiz (квизы очень популярны у интеграторов)
            if "marquiz.ru" in html_lower:
                result["has_marquiz"] = True
                
        except Exception as e:
            logger.warning(f"Tech extractor error {url}: {e}")
            
        return result

    async def extract_async(self, url: str) -> dict:
        """Async версия extract — использует httpx.AsyncClient.

        Идентична по логике extract(), но неблокирующая.
        """
        result = {
            "cms": "unknown",
            "has_marquiz": False
        }

        if not url:
            return result

        if not is_safe_url(url):
            return result

        try:
            html = await async_fetch_page(url, timeout=10)
            if not html:
                return result

            html_lower = html.lower()
            if "wp-content" in html_lower or "wordpress" in html_lower:
                result["cms"] = "wordpress"
            elif "bitrix" in html_lower or "1c-bitrix" in html_lower:
                result["cms"] = "bitrix"
            elif "tilda.ws" in html_lower or "tilda.cc" in html_lower or "created on tilda" in html_lower:
                result["cms"] = "tilda"
            elif "flexbe" in html_lower:
                result["cms"] = "flexbe"
            elif "lpmotor" in html_lower:
                result["cms"] = "lpmotor"
            elif "joomla" in html_lower:
                result["cms"] = "joomla"
            elif "opencart" in html_lower or "route=common/home" in html_lower:
                result["cms"] = "opencart"

            if "marquiz.ru" in html_lower:
                result["has_marquiz"] = True

        except Exception as e:
            logger.warning(f"Tech extractor async error {url}: {e}")

        return result
