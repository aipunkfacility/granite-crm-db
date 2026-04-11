# scrapers/_playwright.py
from contextlib import contextmanager
from loguru import logger
import random

try:
    from playwright.sync_api import sync_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright не установлен. Playwright-скреперы недоступны. "
                   "Установите: pip install playwright && playwright install chromium")


def _get_random_desktop_ua() -> str:
    """Случайный User-Agent из популярных десктопных браузеров.

    Не используется fake_useragent — он генерирует слишком экзотические UA,
    которые сами по себе являются сигнатурой ботов.
    """
    uas = [
        # Chrome 134 на Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        # Chrome 134 на macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        # Firefox 135 на Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
        # Edge 134 на Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
    ]
    return random.choice(uas)


if PLAYWRIGHT_AVAILABLE:
    @contextmanager
    def playwright_session(headless: bool = True):
        """Контекстный менеджер: один браузер на всю сессию.

        Использование:
            with playwright_session() as (browser, page):
                dgis = DgisScraper(config, city, playwright_page=page)
                yell = YellScraper(config, city, playwright_page=page)
                results_dgis = dgis.run()
                results_yell = yell.run()
        """
        _stealth_apply = None
        # playwright-stealth >= 1.0: Stealth().apply(page)
        # playwright-stealth < 1.0: stealth_sync(page) или stealth(page)
        try:
            from playwright_stealth import Stealth
            _stealth_apply = lambda page: Stealth().apply(page)
        except ImportError:
            try:
                from playwright_stealth import stealth_sync
                _stealth_apply = stealth_sync
            except ImportError:
                try:
                    from playwright_stealth import stealth
                    _stealth_apply = stealth
                except ImportError:
                    logger.warning("playwright-stealth не установлен, продолжаем без него "
                                   "(pip install playwright-stealth)")
        _has_stealth = _stealth_apply is not None

        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=_get_random_desktop_ua(),
                )
                try:
                    page = context.new_page()
                    if _stealth_apply:
                        try:
                            _stealth_apply(page)
                        except Exception:
                            # stealth не применился — пропускаем
                            logger.warning("playwright_stealth: не удалось применить stealth, продолжаем без него")
                    yield browser, page
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
        finally:
            pw.stop()
else:
    @contextmanager
    def playwright_session(headless: bool = True):
        """Заглушка — Playwright не установлен."""
        logger.error("Playwright не установлен. playwright_session недоступен.")
        yield None, None
