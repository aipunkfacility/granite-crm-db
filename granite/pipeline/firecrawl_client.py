# pipeline/firecrawl_client.py
"""Клиент для firecrawl CLI: поиск и скрапинг сайтов через subprocess.

Вынесен из PipelineManager для изоляции subprocess-вызовов,
устранения дублирования JSON-парсинга и возможности мокирования в тестах.
"""

import subprocess
import json
import re
import time
from loguru import logger
from granite.utils import extract_emails


class FirecrawlClient:
    """Обёртка над firecrawl CLI (search + scrape)."""

    def __init__(
        self, timeout: int = 60, search_limit: int = 3, request_delay: float = 2.0
    ):
        self.timeout = timeout
        self.search_limit = search_limit
        self.request_delay = request_delay

    # ── JSON-парсинг stdout (устраняет дублирование между search и scrape) ──

    def _parse_json_output(self, stdout: str) -> dict | None:
        """Парсит stdout firecrawl CLI как JSON.

        Пробует:
        1. Распарсить весь stdout как JSON
        2. Найти первый {...} блок через regex с балансом скобок
        """
        stdout = stdout.strip()
        if not stdout:
            return None

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # Нежадный поиск с балансом скобок
            m = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", stdout)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            return None

    # ── Поиск ──

    def search(self, query: str) -> dict | None:
        """Поиск через firecrawl search CLI.

        Returns:
            dict с ключом "data.web" — список результатов, или None.
        """
        try:
            result = subprocess.run(
                ["firecrawl", "search", query, "--limit", str(self.search_limit)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
            if result.returncode != 0 and result.stderr:
                logger.warning(
                    f"Firecrawl search stderr (rc={result.returncode}): "
                    f"{result.stderr.strip()[:300]}"
                )
                return None

            stdout = result.stdout.strip()
            if not stdout:
                return None

            parsed = self._parse_json_output(stdout)
            if parsed is None:
                logger.debug(
                    f"Firecrawl search: не удалось распарсить stdout ({len(stdout)} символов)"
                )
            return parsed

        except subprocess.TimeoutExpired:
            logger.warning(f"Firecrawl search таймаут: {query[:60]}")
            return None
        except FileNotFoundError:
            logger.error("firecrawl CLI не найден — установите firecrawl-cli")
            return None
        except Exception as e:
            logger.debug(f"Firecrawl search ошибка: {e}")
            return None

    # ── Скрапинг ──

    def scrape(self, url: str) -> dict | None:
        """Скрапинг сайта через firecrawl scrape CLI.

        Returns:
            {"phones": [...], "emails": [...]} или None.
        """
        try:
            result = subprocess.run(
                ["firecrawl", "scrape", url, "--format", "markdown"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
            if result.returncode != 0 and result.stderr:
                logger.warning(
                    f"Firecrawl scrape stderr (rc={result.returncode}): "
                    f"{result.stderr.strip()[:300]}"
                )
                return None

            stdout = result.stdout.strip()
            if not stdout:
                return None

            # Пробуем распарсить как JSON
            data = self._parse_json_output(stdout)

            if not data:
                # Если не JSON — это может быть чистый markdown
                if len(stdout) > 50:
                    markdown = stdout
                else:
                    return None
            else:
                markdown = ""
                d = data.get("data", {})
                if isinstance(d, dict):
                    markdown = d.get("markdown", "") or d.get("html", "")
                elif isinstance(d, str):
                    markdown = d
                if not markdown:
                    return None

            phones = re.findall(
                r"(\+?7[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2})",
                markdown,
            )
            return {"phones": phones, "emails": extract_emails(markdown)}

        except subprocess.TimeoutExpired:
            logger.warning(f"Firecrawl scrape таймаут: {url[:80]}")
            return None
        except FileNotFoundError:
            logger.error("firecrawl CLI не найден — установите firecrawl-cli")
            return None
        except Exception as e:
            logger.debug(f"Firecrawl scrape ошибка: {e}")
            return None
