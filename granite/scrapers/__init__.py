from granite.scrapers.base import BaseScraper
from granite.scrapers.jsprav import JspravScraper
from granite.scrapers.jsprav_playwright import JspravPlaywrightScraper
from granite.scrapers.dgis import DgisScraper
from granite.scrapers.yell import YellScraper
from granite.scrapers.web_search import WebSearchScraper

__all__ = [
    "BaseScraper",
    "JspravScraper",
    "JspravPlaywrightScraper",
    "DgisScraper",
    "YellScraper",
    "WebSearchScraper",
]
