---
name: scraper-debugger
description: |
  Запускает и отлаживает один скрепер Granite (jsprav, web_search, dgis) для конкретного
  города в изоляции. Используй когда скрепер даёт 0 результатов или зависает на конкретном
  городе и не нужно запускать весь пайплайн.
---

# Scraper Debugger

## Быстрый запуск

```python
# В терминале Antigravity:

# Jsprav
from granite.scrapers.jsprav import JspravScraper
config = {"sources": {"jsprav": {}}, "cities": [{"name": "Ярославль"}]}
s = JspravScraper(config, "Ярославль")
print(f"Поддомен: {s._get_subdomain()}")  # → yaroslavl
results = s.run()
print(f"Найдено: {len(results)}, с мессенджерами: {sum(1 for r in results if r.messengers)}")

# WebSearch
from granite.scrapers.web_search import WebSearchScraper
s = WebSearchScraper(config, "Ярославль")
results = s.run()
```

## Диагностика нулевого результата

1. `s._get_subdomain()` — проверить что поддомен правильный
2. Через Playwright MCP открыть `https://{subdomain}.jsprav.ru/izgotovlenie-i-ustanovka-pamyatnikov-i-nadgrobij/` — есть ли JSON-LD в исходнике?
3. Проверить кэш: `cat data/category_cache.yaml | grep -A2 "ИмяГорода"`
4. Если категория не найдена — добавить вручную в `config.yaml → sources.jsprav.subdomain_map`

## Проверка после исправления

```bash
# Запустить только scraping без dedup/enrichment
python cli.py run "Ярославль" --no-scrape  # пропустить scraping если уже есть данные
# или полный прогон с очисткой:
python cli.py run "Ярославль" --force
```
