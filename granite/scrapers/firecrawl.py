# scrapers/firecrawl.py — поиск и сбор через firecrawl CLI
import subprocess
import json
import re
import tempfile
import shutil
import atexit
import os
from granite.scrapers.base import BaseScraper
from granite.models import RawCompany, Source
from granite.utils import normalize_phones, extract_emails, extract_domain
from loguru import logger


class FirecrawlScraper(BaseScraper):
    """Скрепер через firecrawl CLI (работает без API-ключа в демо-режиме)."""

    def __init__(self, config: dict, city: str, db=None):
        super().__init__(config, city)
        self.source_config = config.get("sources", {}).get("firecrawl", {})
        self.queries = self.source_config.get("queries", [])
        self._tmpdir = tempfile.mkdtemp(prefix="firecrawl_")
        atexit.register(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def _run(self, args: list) -> dict | None:
        """Запуск firecrawl CLI с JSON-выводом во временный файл."""
        outfile = os.path.join(self._tmpdir, f"fc_{os.getpid()}_{id(args)}.json")
        full_args = ["firecrawl"] + args + ["--json", "-o", outfile]

        try:
            result = subprocess.run(
                full_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            if os.path.exists(outfile):
                with open(outfile, "r", encoding="utf-8") as f:
                    return json.load(f)
            # Если файл не создался — попробуем распарсить stdout
            stdout = result.stdout.strip()
            if stdout:
                try:
                    return json.loads(stdout)
                except json.JSONDecodeError:
                    pass
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"  Firecrawl CLI ошибка: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"  Firecrawl JSON parse error: {e}")
            return None

    def scrape(self) -> list[RawCompany]:
        companies = []
        region_name = self.city_config.get("region", self.city)

        for query in self.queries:
            search_query = f"{query} {region_name}"
            logger.info(f"  Firecrawl поиск: {search_query}")

            result = self._run(["search", search_query, "--limit", "10"])
            if not result:
                logger.warning("  Firecrawl: пустой ответ от search")
                continue

            # Парсим JSON: result["data"]["web"][]
            web_results = result.get("data", {}).get("web", [])
            if not web_results:
                logger.debug(f"  Firecrawl: 0 web-результатов для '{search_query}'")
                continue

            for item in web_results:
                url = item.get("url", "")
                title = item.get("title", "")
                description = item.get("description", "")
                if not url or not title:
                    continue

                companies.append(
                    RawCompany(
                        source=Source.FIRECRAWL,
                        source_url=url,
                        name=title,
                        phones=[],
                        address_raw="",
                        website=url,
                        emails=[],
                        city=self.city,
                    )
                )

        logger.info(f"  Firecrawl: найдено {len(companies)} компаний (поиск)")

        # Детальный сбор со всех уникальных сайтов
        seen_domains = set()
        enriched = 0
        for company in companies:
            if not company.website:
                continue
            domain = extract_domain(company.website)
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)

            logger.info(f"  Scrape: {company.website}")
            details = self._scrape_details(company.website)
            if details:
                company.phones = normalize_phones(
                    company.phones + details.get("phones", [])
                )
                company.emails = list(set(company.emails + details.get("emails", [])))
                if not company.address_raw and details.get("addresses"):
                    company.address_raw = details["addresses"][0]
                enriched += 1

        logger.info(f"  Firecrawl: обогащено {enriched}/{len(seen_domains)} сайтов")
        return companies

    def _scrape_details(self, url: str) -> dict | None:
        """Детальный скрапинг сайта через firecrawl scrape."""
        result = self._run(["scrape", url, "--format", "markdown"])
        if not result:
            return None

        # Извлекаем markdown из ответа
        markdown = ""
        data = result.get("data", {})
        if isinstance(data, dict):
            markdown = data.get("markdown", "") or data.get("html", "")
        elif isinstance(data, str):
            markdown = data

        if not markdown:
            return None

        data_out: dict = {"phones": [], "emails": [], "addresses": []}

        data_out["phones"] = re.findall(
            r"(\+?7[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2})",
            markdown,
        )
        data_out["emails"] = extract_emails(markdown)

        address_patterns = [
            r"г\.?\s+[А-Яа-яё]+\s*,?\s*ул\.?\s+[А-Яа-яё]+",
            r"г\.?\s+[А-Яа-яё]+\s*,?\s*[А-Яа-яё]+\s+\d+",
        ]
        for pattern in address_patterns:
            data_out["addresses"].extend(re.findall(pattern, markdown))

        return data_out
