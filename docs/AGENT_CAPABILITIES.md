# 🤖 Возможности агента — Granite CRM

Конфигурация [Google Antigravity](https://antigravity.google) для работы над проектом
**Granite Workshops DB** — пайплайн сбора базы ритуальных мастерских + FastAPI бэкенд.

> **Antigravity** — agent-first IDE от Google (ноябрь 2025). Агенты работают параллельно
> в Manager View, имеют собственные терминал и браузер, и загружают Skills только по
> необходимости (Progressive Disclosure — не всё сразу в контекст).

---

## 📁 Структура конфигурации

```
granite-crm-db/
└── .agents/
    ├── rules.md                   # Правила агента (загружаются всегда)
    └── skills/                    # Workspace Skills — коммитятся в git
        ├── data-auditor/
        │   ├── SKILL.md           # ← уже есть
        │   └── scripts/
        ├── pipeline-monitor/
        │   └── SKILL.md           # создать
        ├── scraper-debugger/
        │   └── SKILL.md           # создать
        └── granite-coder/
            └── SKILL.md           # создать
```

> **Workspace vs Global scope:**
> `.agents/skills/` — только для этого проекта, коммитится в git, видят все в команде.
> `~/.gemini/antigravity/skills/` — личные утилиты через все проекты.

---

## 🛠️ MCP-серверы

MCP поддержка добавлена в Antigravity в декабре 2025. Настраиваются через
**Agent Manager → Manage MCP Servers** или вручную в `mcp_config.json`.

> ⚠️ **Лимит:** Antigravity рекомендует ≤ 50 активных инструментов суммарно.
> При > 100 — ошибка. Устанавливайте только нужное, отключайте лишнее.

### Активные

| Сервер | Установка | Для чего используется |
| :--- | :--- | :--- |
| **SQLite MCP** | [`mcp-server-sqlite`](https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite) · `uvx mcp-server-sqlite --db-path data/granite.db` | Прямые SQL-запросы к `granite.db` без написания скриптов. Data Auditor skill использует его как основной инструмент: поиск дублей, аномалии скоринга, статистика по городам. |
| **Playwright MCP** | [`@playwright/mcp`](https://github.com/microsoft/playwright-mcp) · `npx @playwright/mcp@latest` | Отладка парсинга jsprav/2GIS в реальном браузере. Проверить что Playwright fallback видит те же карточки что руками. Дополняет встроенный браузер Antigravity. |
| **GitHub MCP** | [`@modelcontextprotocol/server-github`](https://github.com/modelcontextprotocol/servers/tree/main/src/github) · требует `GITHUB_TOKEN` | Создавать issues по результатам аудита БД. Смотреть историю коммитов при отладке регрессий. |
| **Context7** | [`@upstash/context7-mcp`](https://github.com/upstash/context7) · `npx @upstash/context7-mcp` | Актуальная документация библиотек прямо в контексте: SQLAlchemy 2.x, FastAPI, nuqs@2.x, TanStack Query v5, shadcn/ui. Предотвращает галлюцинации устаревшего API. |
| **Sequential Thinking** | [`@modelcontextprotocol/server-sequential-thinking`](https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking) | Пошаговое планирование перед сложными задачами: SQL-оптимизация followup, SSE-архитектура, рефакторинг enrichment_phase. |

### Рекомендованные (добавить)

| Сервер | Установка | Зачем |
| :--- | :--- | :--- |
| **Tavily Search** | [`tavily-mcp`](https://github.com/tavily-ai/tavily-mcp) · требует `TAVILY_API_KEY` | Точечный поиск компаний которых не нашёл DuckDuckGo. Использовать только в reverse lookup, не вместо основного скрапинга. |

**Конфигурация `mcp_config.json`:**
```json
{
  "mcpServers": {
    "github-mcp-server": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "..." }
    },
    "sqlite": {
      "command": "npx",
      "args": ["-y", "mcp-server-sqlite-npx", "data/granite.db"]
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp"]
    },
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp@latest"]
    },
    "sequentialthinking": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]
    }
  }
}
```

---

## 🧠 Skills (Workspace Scope)

Antigravity читает только `name` + `description` из всех скиллов при старте —
полные инструкции подгружаются только при активации. Держите `description` узким
и конкретным чтобы избежать ложных срабатываний ("Tool Bloat").

---

### ✅ Data Auditor
**Путь:** `.agents/skills/Data Auditor/SKILL.md` · **Статус:** готов

Аудит качества `granite.db` (~6000 компаний, 29 городов).

Чек-лист покрывает: общую статистику, аномалии скоринга (богатые данные + низкий скор),
качество по городам с порогом 30%, дубли по имени и телефону, мёртвые записи без контактов,
Telegram Trust, CMS-распределение.

**Активируется:** "проверь базу", "аудит данных", "качество по городу X", "найди дубли".

```bash
uv run scripts/audit_database.py --output data/audit_report.md
uv run scripts/audit_database.py --city "Краснодар"
```

**Интерпретация результатов:**

| Проблема | Действие |
|----------|----------|
| `pct_zero > 30%` в городе | `python cli.py run "Город" --re-enrich` |
| Дубли по имени | `--no-scrape` + пересчёт дедупликации |
| Скоринговые аномалии | Проверить `granite/enrichers/classifier.py` |
| Много мёртвых записей | Включить `reverse_lookup` в `config.yaml` |

---

### ✅ Pipeline Monitor
**Путь:** `.agents/skills/Pipeline Monitor/SKILL.md` · **Статус:** готов

```markdown
---
name: pipeline-monitor
description: |
  Анализирует логи пайплайна Granite (data/logs/granite.log). Используй когда
  пользователь говорит что пайплайн завис, упал, или дал 0 компаний для города.
  НЕ использовать для вопросов о коде или структуре проекта.
---

# Pipeline Monitor

## Алгоритм диагностики

1. Прочитай последние 200 строк `data/logs/granite.log`
2. Определи фазу остановки: scraping / enrichment / dedup / scoring
3. Найди паттерны проблем (таблица ниже)
4. Предложи конкретное действие

## Паттерны и решения

| Паттерн в логах | Причина | Действие |
|----------------|---------|----------|
| `enrichment N detail-страниц` (N > 50) без продолжения | HTTP-зависание на detail-страницах jsprav | Уменьшить таймаут до 8с; для больших городов пропускать detail-enrichment |
| `Scrape: https://...` без продолжения | Таймаут конкретного сайта (15с) | Добавить домен в `_FAILED_DOMAINS` или снизить таймаут до 8с |
| `DDGS — 0 результатов` несколько раз подряд | Rate limit DuckDuckGo | Подождать 10-15 минут |
| `получено 75 из 193` для большого города | Лимит 5 стр. статической пагинации jsprav | Штатно — Playwright fallback подберёт остальное; проверить что PW запустился |
| `City 'X' not found in config` | Город из области не в config.yaml | Нормально для малых городов области, данные всё равно сохранятся под главным городом |
| `поддомен X, категория не найдена` | Неверный поддомен или нет категории | Добавить `subdomain_map` в config.yaml вручную |
```

---

### ✅ Scraper Debugger
**Путь:** `.agents/skills/Scraper Debugger/SKILL.md` · **Статус:** готов

Изолированный запуск одного скрепера без полного пайплайна.

```markdown
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
```

---

### ✅ Granite Coder
**Путь:** `.agents/skills/Granite Coder/SKILL.md` · **Статус:** готов

Правила написания кода специфичные для проекта. Активируется при задачах изменения кода.

```markdown
---
name: granite-coder
description: |
  Правила и паттерны разработки для проекта Granite CRM. Используй при написании
  или изменении кода Python в директориях granite/ (scrapers, enrichers, pipeline, api,
  database). НЕ использовать для вопросов о данных или логах.
---

# Granite Coder

## Ключевые правила

**БД и сессии:**
- Всегда использовать `session_scope()` контекстный менеджер, не `get_session()`
- Никогда не вызывать `session.commit()` внутри `session_scope()` — делает автоматически
- Не передавать SQLAlchemy session между потоками ThreadPoolExecutor

**HTTP и безопасность:**
- Все URL через `is_safe_url(url)` перед запросом (SSRF protection)
- Логировать URL через `_sanitize_url_for_log(url)` — не сырой URL (PII)
- Таймаут 15с для детального scraping, 8с для batch-запросов

**Async vs Sync:**
- Если `enrichment.async_enabled: true` → httpx.AsyncClient через `http_client.py`
- Иначе → ThreadPoolExecutor в `_enrich_companies_parallel()`
- Не смешивать sync и async в одном контексте без `run_async()`

## Изменение схемы БД

```bash
# 1. Изменить ORM-модель в granite/database.py
# 2. Проверить что Alembic видит изменения:
python cli.py db check
# 3. Создать миграцию:
python cli.py db migrate "описание изменения"
# 4. Проверить файл в alembic/versions/
# 5. Применить:
python cli.py db upgrade head
```

## Тесты

```bash
python -m pytest tests/ -v                      # все
python -m pytest tests/test_enrichers.py -v     # enrichers
python -m pytest tests/test_pipeline.py -v      # pipeline
python -m pytest -k "async" -v                  # только async
```

## Использовать Context7 MCP для:
- SQLAlchemy 2.x синтаксис (особенно `select()` vs `query()`)
- FastAPI Depends и response models
- Alembic `op.*` методы для миграций
```

---

## 📦 Готовые коллекции Skills

Вместо написания с нуля — установить из community-репозиториев:

| Репозиторий | Что внутри | Установка |
| :--- | :--- | :--- |
| [**antigravity-awesome-skills**](https://github.com/sickn33/antigravity-awesome-skills) | 1400+ skills, есть Antigravity-таргет. Категории: `development`, `backend`, `database`, `security` | `npx antigravity-awesome-skills --path .agents/skills --category development,backend --risk safe` |
| [**rmyndharis/antigravity-skills**](https://github.com/rmyndharis/antigravity-skills) | 300+ skills портированных из Claude Code. Хорошие: `python-development`, `database-expert`, `api-design-principles`, `security-auditor` | Клонировать → скопировать нужные папки в `.agents/skills/` |

---

## 📐 Как создать скилл

Официальная документация: [Authoring Antigravity Skills (Codelabs)](https://codelabs.developers.google.com/getting-started-with-antigravity-skills)
Практический гайд: [5 примеров скиллов (Medium)](https://medium.com/google-cloud/tutorial-getting-started-with-antigravity-skills-864041811e0d)

```
.agents/skills/my-skill/
├── SKILL.md          # обязателен — metadata + инструкции
├── scripts/          # Python/Bash для выполнения агентом
│   └── run.py
└── references/       # Документация, шаблоны (читаются по необходимости)
    └── schema.md
```

Шаблон `SKILL.md`:
```markdown
---
name: skill-name
description: |
  Одно предложение: когда агент ДОЛЖЕН использовать этот скилл.
  Узкое = меньше ложных активаций. Добавь "НЕ использовать когда..."
---

# Skill Title

## Use this skill when
- конкретная ситуация 1
- конкретная ситуация 2

## Do not use this skill when
- ...

## Instructions
1. Шаг 1
2. Шаг 2

## Safety
(если скилл выполняет shell-команды — описать что запрещено)
```

> **Windows Native (PowerShell):**
> 1. Устанавливайте глобальные скиллы в `%USERPROFILE%\.gemini\antigravity\skills`.
> 2. Для применения изменений в `mcp_config.json` или новых скиллов используйте **Restart Agent** в меню Agent Manager.
> 3. Убедитесь, что `node`, `npm` и `uv` добавлены в системный `PATH`.

---

## 🗺️ Маршрутизация задач

```
Запрос пользователя
        │
        ├─ "пайплайн завис / 0 компаний / долго работает"
        │   └─ Pipeline Monitor → читает granite.log → конкретный диагноз
        │
        ├─ "почему скрепер не работает для города X"
        │   └─ Scraper Debugger → изолированный запуск → Playwright MCP для проверки HTML
        │
        ├─ "проверь базу / качество данных / аудит"
        │   └─ Data Auditor → SQLite MCP → SQL-запросы → Markdown отчёт
        │
        ├─ "изменить / написать / отрефакторить код"
        │   └─ Granite Coder → Context7 MCP для актуального API → тесты
        │
        └─ "найти компанию / обогатить запись вручную"
            └─ Tavily MCP → точечный поиск → SQLite MCP для записи результата
```

---

*Версия: 3.0 · Дата: 2026-04-13*
*IDE: [Google Antigravity](https://antigravity.google) · Skills docs: [Codelabs](https://codelabs.developers.google.com/getting-started-with-antigravity-skills)*
