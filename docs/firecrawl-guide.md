# Firecrawl — Полное руководство

**Версия:** 2.0  
**Дата:** 30.03.2026  
**Источники:** Официальная документация + транскрипт видео

---

## Содержание

1. [Введение](#введение)
2. [Быстрый старт](#быстрый-старт)
3. [Установка и настройка](#установка-и-настройка)
4. [Основные возможности](#основные-возможности)
5. [Дополнительные возможности](#дополнительные-возможности)
6. [SDK и интеграции](#sdk-и-интеграции)
7. [Примеры использования](#примеры-использования)
8. [Бизнес-идеи](#бизнес-идеи)
9. [Применение для granite-crm-db](#применение-для-granite-crm-db)

---

## Введение

Firecrawl — это веб-API для сбора данных с веб-сайтов, разработанное специально для AI-систем. Превращает любой URL в чистые данные, готовые для использования языковыми моделями (LLM).

### Ключевые преимущества

| Возможность | Описание |
|-------------|----------|
| **Search** | Поиск в Google с полным контентом результатов |
| **Scrape** | Извлечение контента в Markdown, HTML или JSON |
| **Interact** | Взаимодействие со страницей после скрапинга |
| **Надёжность** | Работает на 98-99% сайтов |
| **Скорость** | Результаты за секунды |
| **MCP Server** | Интеграция с Claude, Cursor, Windsurf и другими |

---

## Быстрый старт

### Получение API-ключа

1. Зарегистрируйтесь на [firecrawl.dev/app/api-keys](https://www.firecrawl.dev/app/api-keys)
2. Получите API-ключ
3. Начните использовать

### Первый запрос

```bash
curl -X POST 'https://api.firecrawl.dev/v2/scrape' \
  -H 'Authorization: Bearer fc-YOUR-API-KEY' \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com"}'
```

### Ответ

```json
{
  "success": true,
  "data": {
    "markdown": "# Example Domain\n\nThis domain is for use in illustrative examples...",
    "metadata": {
      "title": "Example Domain",
      "sourceURL": "https://example.com"
    }
  }
}
```

---

## Установка и настройка

### Python SDK

```bash
pip install firecrawl-py
```

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key="fc-YOUR-API-KEY")
result = app.scrape("https://example.com")
print(result)
```

### Node.js SDK

```bash
npm install @mendable/firecrawl-js
```

```javascript
import Firecrawl from '@mendable/firecrawl-js';

const app = new Firecrawl({ apiKey: "fc-YOUR-API-KEY" });
const result = await app.scrape("https://example.com");
console.log(result);
```

### CLI

```bash
# Установка
npx -y firecrawl-cli@latest init --all --browser

# Использование
firecrawl https://example.com
firecrawl search "запрос" --limit 5
```

### MCP Server (рекомендуется)

Интеграция с AI-агентами через Model Context Protocol:

```bash
npx -y firecrawl-cli@latest init --all --browser
```

После установки Firecrawl становится доступен в Claude Code, Cursor, Windsurf и других AI-инструментах.

---

## Основные возможности

### 1. Search — Поиск

Поиск в Google с получением полного контента из результатов.

```python
from firecrawl import Firecrawl

firecrawl = Firecrawl(api_key="fc-YOUR-API-KEY")

results = firecrawl.search(
    query="ритуальные услуги Москва",
    limit=5,
)
print(results)
```

**Ответ:**

```json
{
  "success": true,
  "data": {
    "web": [
      {
        "url": "https://example.com/",
        "title": "Example",
        "description": "Description",
        "position": 1
      }
    ],
    "images": [...],
    "news": [...]
  }
}
```

**Применение:**

- Поиск компаний по ключевым словам
- Сбор новостей
- Мониторинг конкурентов

---

### 2. Scrape — Скрапинг

Извлечение контента с любого URL.

```python
from firecrawl import Firecrawl

firecrawl = Firecrawl(api_key="fc-YOUR-API-KEY")

# Базовый scrape
doc = firecrawl.scrape("https://firecrawl.dev", formats=["markdown", "html"])
print(doc)

# С параметрами
doc = firecrawl.scrape(
    url="https://yell.ru/moscow/cat/ritualnyie_uslugi/",
    formats=["markdown", "json"],
    only_main_content=True
)
```

**Форматы вывода:**

- `markdown` — чистый текст без HTML
- `html` — исходный HTML
- `json` — структурированный JSON
- `links` — список ссылок
- `screenshot` — скриншот страницы

**Ответ:**

```json
{
  "success": true,
  "data": {
    "markdown": "...",
    "html": "...",
    "metadata": {
      "title": "Title",
      "description": "Description",
      "language": "ru",
      "sourceURL": "https://...",
      "statusCode": 200
    }
  }
}
```

---

### 3. Interact — Взаимодействие

Скрап страницы + продолжение работы: клики, заполнение форм, извлечение динамического контента.

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key="fc-YOUR-API-KEY")

# 1. Скрап страницы
result = app.scrape("https://www.amazon.com", formats=["markdown"])
scrape_id = result.metadata.scrape_id

# 2. Взаимодействие
app.interact(scrape_id, prompt="Search for iPhone 16 Pro Max")
response = app.interact(scrape_id, prompt="Click on the first result and tell me the price")
print(response.output)

# 3. Остановить сессию
app.stop_interaction(scrape_id)
```

**Ответ:**

```json
{
  "success": true,
  "liveViewUrl": "https://liveview.firecrawl.dev/...",
  "output": "The iPhone 16 Pro Max (256GB) is priced at $1,199.00.",
  "exitCode": 0
}
```

**Применение:**

- Авторизация на сайтах
- Заполнение форм поиска
- Пагинация
- Клики по динамическим элементам

---

## Дополнительные возможности

### 4. Agent

Автономный сбор данных, управляемый AI. Опишите, что нужно найти, и агент сделает остальное.

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key="fc-YOUR-API-KEY")

# Запуск агента
agent = app.agent(
    query="Найди все ритуальные агентства Екатеринбурга с телефонами и адресами",
    website=None,  # Агент сам найдёт источники
    webhook_url=None
)

print(agent)
```

**Применение:**

- Комплексные исследовательские задачи
- Сбор данных из множества источников
- Естественноязыковые запросы к данным

---

### 5. Map

Быстрое построение карты всех URL сайта.

```python
from firecrawl import Firecrawl

firecrawl = Firecrawl(api_key="fc-YOUR-API-KEY")

result = firecrawl.map("https://yell.ru")
print(result)
```

**Ответ:**

```json
{
  "success": true,
  "data": {
    "links": [
      "https://yell.ru/moscow/",
      "https://yell.ru/spb/",
      ...
    ]
  }
}
```

---

### 6. Crawl

Рекурсивный обход всего сайта.

```python
from firecrawl import Firecrawl

firecrawl = Firecrawl(api_key="fc-YOUR-API-KEY")

# Запуск краулинга
crawl_result = firecrawl.crawl(
    url="https://yell.ru/moscow/cat/ritualnyie_uslugi/",
    limit=100,  # Максимум страниц
    scrape_options={
        "formats": ["markdown", "json"]
    }
)

print(crawl_result)
```

---

### 7. Batch Scrape

Массовый скрапинг множества URL в одном запросе.

```python
from firecrawl import Firecrawl

firecrawl = Firecrawl(api_key="fc-YOUR-API-KEY")

urls = [
    "https://yell.ru/company1/",
    "https://yell.ru/company2/",
    "https://yell.ru/company3/",
]

result = firecrawl.batch_scrape(urls=urls)
print(result)
```

---

### 8. Extract (JSON Mode)

Извлечение структурированных данных через LLM.

```python
from firecrawl import Firecrawl

firecrawl = Firecrawl(api_key="fc-YOUR-API-KEY")

result = firecrawl.extract(
    urls=["https://yell.ru/company/ritualnaya-sluzhba-moskva/"],
    prompt="Извлеки название компании, телефон, адрес, email и сайт"
)

print(result)
```

---

### 9. Change Tracking

Отслеживание изменений на веб-сайтах.

```python
from firecrawl import Firecrawl

firecrawl = Firecrawl(api_key="fc-YOUR-API-KEY")

# Мониторинг изменений
result = firecrawl.crawl(
    url="https://example.com/prices/",
    enable_change_tracking=True
)
```

---

## SDK и интеграции

### Поддерживаемые языки

| SDK | Команда установки |
|-----|-------------------|
| **Python** | `pip install firecrawl-py` |
| **Node.js** | `npm install @mendable/firecrawl-js` |
| **Go** | `go get github.com/firecrawl/firecrawl-go` |
| **Rust** | Добавить в Cargo.toml |
| **Java** | Добавить зависимость |

### Интеграции с AI-фреймворками

- **LangChain** — [документация](https://docs.firecrawl.dev/developer-guides/llm-sdks-and-frameworks/langchain)
- **LangGraph** — [документация](https://docs.firecrawl.dev/developer-guides/llm-sdks-and-frameworks/langgraph)
- **LlamaIndex** — [документация](https://docs.firecrawl.dev/developer-guides/llm-sdks-and-frameworks/llamaindex)
- **OpenAI** — [документация](https://docs.firecrawl.dev/developer-guides/llm-sdks-and-frameworks/openai)
- **Anthropic (Claude)** — [документация](https://docs.firecrawl.dev/developer-guides/llm-sdks-and-frameworks/anthropic)
- **Google Gemini** — [документация](https://docs.firecrawl.dev/developer-guides/llm-sdks-and-frameworks/gemini)
- **Vercel AI SDK** — [документация](https://docs.firecrawl.dev/developer-guides/llm-sdks-and-frameworks/vercel-ai-sdk)

### MCP Server

Подключение Firecrawl к AI-инструментам:

- **Claude Code** — [гайд](https://docs.firecrawl.dev/developer-guides/mcp-setup-guides/claude-code)
- **Cursor** — [гайд](https://docs.firecrawl.dev/developer-guides/mcp-setup-guides/cursor)
- **Windsurf** — [гайд](https://docs.firecrawl.dev/developer-guides/mcp-setup-guides/windsurf)
- **ChatGPT** — [гайд](https://docs.firecrawl.dev/developer-guides/mcp-setup-guides/chatgpt)

### Автоматизация

- **n8n** — [гайд](https://docs.firecrawl.dev/developer-guides/workflow-automation/n8n)
- **Zapier** — [гайд](https://docs.firecrawl.dev/developer-guides/workflow-automation/zapier)
- **Make** — [гайд](https://docs.firecrawl.dev/developer-guides/workflow-automation/make)
- **Dify** — [гайд](https://docs.firecrawl.dev/developer-guides/workflow-automation/dify)

---

## Примеры использования

### Пример 1: Сбор ритуальных компаний

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key="fc-YOUR-API-KEY")

# Поиск компаний
results = app.search(
    query="ритуальные услуги Екатеринбург каталог",
    limit=10
)

# Извлечение данных с каждой
for item in results.data['web']:
    company_data = app.scrape(
        url=item['url'],
        formats=['markdown', 'json'],
        only_main_content=True
    )
    print(company_data.markdown)
```

### Пример 2: Обход каталога

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key="fc-YOUR-API-KEY")

# Краулинг всех страниц каталога
crawl_result = app.crawl(
    url="https://yell.ru/ekaterinburg/cat/ritualnyie_uslugi/",
    limit=500,
    scrape_options={
        "formats": ["markdown", "json"],
        "only_main_content": True
    }
)

for page in crawl_result.data:
    print(page['markdown'][:500])
```

### Пример 3: Интерактивный сбор (Avito)

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key="fc-YOUR-API-KEY")

# Скрап главной страницы Avito
result = app.scrape("https://www.avito.ru/ekaterinburg/ritualnye_uslugi", formats=["markdown"])
scrape_id = result.metadata.scrape_id

# Поиск
app.interact(scrape_id, prompt="Введи в поиск 'памятники гранитные'")

# Сбор результатов
for i in range(5):
    response = app.interact(scrape_id, prompt=f"Кликни на объект номер {i+1} и извлеки: название, цена, телефон, адрес")
    print(response.output)

# Следующая страница
app.interact(scrape_id, prompt="Кликни на кнопку 'Следующая страница'")

app.stop_interaction(scrape_id)
```

---

## Бизнес-идеи

### 1. Lead Enrichment (Обогащение лидов)

[Документация](https://docs.firecrawl.dev/use-cases/lead-enrichment)

Извлечение и фильтрация лидов с веб-сайтов для продажи CRM-отделам.

**Пример:** База ритуальных агентств с контактами

### 2. Competitive Intelligence (Конкурентная разведка)

[Документация](https://docs.firecrawl.dev/use-cases/competitive-intelligence)

Мониторинг конкурентов в реальном времени.

**Пример:** Отслеживание цен конкурентов

### 3. Product & E-commerce

[Документация](https://docs.firecrawl.dev/use-cases/product-ecommerce)

Мониторинг цен и остатков на маркетплейсах.

**Пример:** Мониторинг цен на памятники

### 4. SEO Platforms

[Документация](https://docs.firecrawl.dev/use-cases/seo-platforms)

SEO-аудиты для ниш.

### 5. Deep Research

[Документация](https://docs.firecrawl.dev/use-cases/deep-research)

AI-исследовательские инструменты.

### 6. Investment & Finance

[Документация](https://docs.firecrawl.dev/use-cases/investment-finance)

Финансовая аналитика из веб-данных.

---

## Применение для granite-crm-db

### Текущая задача

Сбор базы **гранитных мастерских** и **производителей памятников** по городам России для предложения услуг ретушера.

### Целевая аудитория

- Гранитные мастерские
- Производители памятников
- Фабрики по обработке камня
- Мастерские по изготовлению надгробий

### Что им нужно (B2B)

- Портреты на памятники (обработка фото усопших)
- Цветная печать на граните
- Гравировка макетов
- Каталоги продукции (обработка фото изделий)

### Рекомендуемый подход

#### CLI (рекомендуется)

```bash
# Поиск гранитных мастерских
npx -y firecrawl-cli@latest search "гранитная мастерская Москва" --limit 10

# Детальный сбор
npx -y firecrawl-cli@latest scrape "https://igranit.ru/" --format markdown
```

#### Python SDK

```python
from firecrawl import Firecrawl
import json

app = Firecrawl(api_key="fc-YOUR-API-KEY")

def collect_granite_companies(city: str, limit: int = 100):
    """Сбор гранитных мастерских для города"""
    
    # Поисковые запросы
    queries = [
        "гранитная мастерская памятники",
        "производство памятников гранит",
        "мастерская по изготовлению памятников",
    ]
    
    companies = []
    
    for query in queries:
        search_results = app.search(
            query=f"{query} {city}",
            limit=10
        )
        
        for item in search_results.data['web']:
            try:
                result = app.scrape(
                    url=item['url'],
                    formats=['markdown', 'json'],
                    only_main_content=True
                )
                
                # Извлечение контактов
                phones = extract_phones(result.markdown)
                emails = extract_emails(result.markdown)
                
                companies.append({
                    'name': item['title'],
                    'url': item['url'],
                    'description': item['description'],
                    'phones': phones,
                    'emails': emails,
                    'city': city,
                    'query': query
                })
                
            except Exception as e:
                print(f"Ошибка {item['url']}: {e}")
    
    return companies

# Использование
moscow_companies = collect_granite_companies("Москва")
ekb_companies = collect_granite_companies("Екатеринбург")

# Сохранение
with open('granite_companies.json', 'w', encoding='utf-8') as f:
    json.dump({
        'moscow': moscow_companies,
        'ekaterinburg': ekb_companies
    }, f, ensure_ascii=False, indent=2)
```

### Готовый скрипт

Используй готовый скрипт: `scripts/firecrawl_granite.py`

```bash
python firecrawl_granite.py Москва
```

### Тестированные примеры

| Запрос | Результат |
|--------|-----------|
| `гранитная мастерская Москва` | iGranit, Гранит-Арт, Nisp, Каменная Слеза |
| `производство памятников Екатеринбург` | GS-Ритуал, Ритуал-Камелия |
| `изготовление памятников Санкт-Петербург` | Granit78,彼得堡石材 |

---

## Ограничения и тарифы

### Rate Limits

| Тариф | Лимиты |
|-------|--------|
| Free | Ограниченное количество запросов |
| Pro | ~5000 запросов/минуту |
| Enterprise | Кастомные лимиты |

### Стоимость

- Pay-per-use или абонемент
- Точные цены: [billing docs](https://docs.firecrawl.dev/billing)

---

## Ссылки

- [Официальный сайт](https://firecrawl.dev)
- [Документация](https://docs.firecrawl.dev)
- [Playground](https://www.firecrawl.dev/playground)
- [API Reference](https://docs.firecrawl.dev/api-reference/v2-introduction)
- [Discord Community](https://discord.gg/firecrawl)
- [GitHub](https://github.com/firecrawl/firecrawl)
