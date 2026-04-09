from granite.scrapers.base import BaseScraper
from granite.scrapers.jsprav import JspravScraper
from granite.scrapers.jsprav_playwright import JspravPlaywrightScraper
from granite.scrapers.dgis import DgisScraper
from granite.scrapers.yell import YellScraper
from granite.scrapers.firmsru import FirmsruScraper
from granite.scrapers.web_search import WebSearchScraper
from granite.scrapers.firecrawl import FirecrawlScraper  # LEGACY

__all__ = [
    "BaseScraper",
    "JspravScraper",
    "JspravPlaywrightScraper",
    "DgisScraper",
    "YellScraper",
    "FirmsruScraper",
    "WebSearchScraper",
    "FirecrawlScraper",
]
