# Granite CRM — Гайд по проведению аудита проекта

> На реальном опыте аудита `granite-crm-db` (55 Python-файлов, 124 находки, 6 групп последовательных исправлений, 169/169 тестов)

---

## 1. Общие принципы

Аудит — это не беглое чтение файлов. Это систематическая проверка данных, которые текут через проект: от входа (config, CLI, скраперы) до выхода (экспорт). Каждая находка привязана к конкретному потоку данных, а не к абстрактной категории. Ошибки находятся на стыках: там, где данные переходят из одного формата в другой (Pydantic → ORM, JSON → Python dict, URL → HTTP-запрос).

**Ключевое правило:** если ты не можешь объяснить, какие данные повреждены и как это проявляется у пользователя — это не баг, это стилистическое замечание.

---

## 2. Карта потоков данных (что проверять)

Перед аудитом нарисуй карту потоков. Для granite-crm-db она выглядит так:

| Поток | Фаза | Класс | Таблица |
|:---:|:---:|:---:|:---:|
| A | Scraping | ScrapingPhase | raw_companies |
| B | Dedup | DedupPhase | companies |
| C | Enrichment | EnrichmentPhase | enriched_companies |
| D | Scoring | ScoringPhase | enriched_companies (crm_score, segment) |
| E | Export | ExportPhase | CSV / Markdown файлы |

Оркестратор: `PipelineManager` (manager.py) координирует фазы через `CheckpointManager`. Контрольно-точки работают через подсчёт записей в таблицах. Поток идёт по цепочке: start → scraped → deduped → enriched.

**Зачем карта:** каждая таблица — это узел, где данные могут исказиться. Проверяй каждый переход: ScrapingPhase сохраняет RawCompany → Database, DedupPhase читает RawCompanyRow → пишет CompanyRow, и т.д.

---

## 3. Стратегия 1: Аудит по входным точкам

Начинай с того, откуда данные входят в систему. Иди от входа к выходу.

### Вход 1: config.yaml → Database → CLI

- `config.yaml` — YAML без валидации. Проверь: все ли поля используются? Какие значения по умолчанию в коде, если поле отсутствует?
- `Database.__init__()` (database.py) читает `yaml.safe_load()` → `config.get("database", {}).get("path", ...)`. Если config.yaml malformed — упадёт через 30 минут работы, а не при старте.
- `cli.py` вызывает `load_config()` многократно. Проверь: есть ли кэширование? Нет ли гонки?
- **Что искать:** отсутствие Pydantic ConfigSchema, hardcoded defaults, дублирование парсинга, race conditions в multi-threaded контексте.

### Вход 2: CLI → PipelineManager → Phases

- `cli.py` создаёт `PipelineManager(config, db)`, тот создаёт все фазы eagerly (даже для export-only режима).
- `PipelineManager.__init__()` инстанцирует `Classifier` и `NetworkDetector` — не нужны для скрапинга, тратят ресурсы.
- `run_city(city)` опирается на `CheckpointManager.get_stage(city)`. Проверь: что если checkpoint повреждён? Что если `--force` очистил данные, но checkpoint не обновился?
- **Что искать:** лишние инстанциации, отсутствие валидации checkpoint, хрупкие переходы между стадиями.

### Вход 3: Pipeline → Database → ORM models

- `ScrapingPhase._save_raw()` преобразует Pydantic `RawCompany` → ORM `RawCompanyRow`. Проверь: все ли поля маппятся? Есть ли потеря данных?
- `DedupPhase.run()` читает `RawCompanyRow` → пишет `CompanyRow`. Проверь: `merged_from` и `messengers` из `merge_cluster()` действительно сохраняются.
- `EnrichmentPhase.run()` читает `CompanyRow` → пишет `EnrichedCompanyRow`. Проверь: `messengers` и `tg_trust` корректно сохраняются.
- **Что искать:** расхождение Pydantic ↔ ORM (geo: `list[float]` vs `String("lat,lon")`), потеря полей при маппинге, отсутствие NULL-проверок.

---

## 4. Стратегия 2: Аудит по категориям уязвимостей

После потоков — проверь каждую категорию насквозь.

### 4.1. Безопасность (SSRF)

SSRF — самый частый вектор в скраперах. Проверь **каждое место**, где код делает HTTP-запрос с user-controlled URL:

```
grep -rn "fetch_page\|requests.get\|requests.head\|subprocess.run" --include="*.py"
```

Для каждого вызова проверь:
1. Откуда берётся URL? (config, scraped HTML, user input)
2. Есть ли валидация URL перед запросом?
3. Есть ли проверка IP (localhost, 169.254.169.254, 10.x, 192.168.x)?
4. Есть ли DNS-резолв (защита от DNS rebinding)?
5. Есть ли whitelist scheme (только http/https)?

**Типичные проблемы:**
- `validator.py` проверяет `_is_internal_url` до очистки URL (порядок операций)
- `messenger_scanner.py` и `tech_extractor.py` не вызывают `is_safe_url()` вообще
- удалённые модули (firecrawl.py, firecrawl_client.py) — проверка URL была не полной
- `jsprav.py` интерполирует subdomain без regex-проверки

### 4.2. Потеря данных (data loss)

Проверь **каждое место**, где данные преобразуются из одного формата в другой:

```
grep -rn "merge_cluster\|CompanyRow(\|EnrichedCompanyRow(\|session.add" --include="*.py"
```

- `merge_cluster()` возвращает dict с `merged_from` и `messengers`, но вызов `CompanyRow()` может их не принять.
- `_deep_enrich_company()` накапливает `updated = ["email", "email", "email"]` — дублирование.
- JSON-парсер в удалённом `firecrawl_client.py` не обрабатывал `{` внутри строк (модуль удалён).

### 4.3. Логика (bugs)

Проверь **каждое место** с `.get()`, `[]`, `.lower()`, арифметикой:

```
grep -rn "\.get(\|\.lower()\|\.upper()\|len(" --include="*.py" | grep -v test
```

- `r.get("messengers", {}).items()` — если `messengers` это string/list, будет `AttributeError`
- `.get("name", "")` возвращает `None` если ключ есть с value `None` → `.lower()` крашится
- `if not cid` отбрасывает ID=0 (falsy) — использовать `if cid is None`
- `float()` вызывается ДО проверки `is not None` (guard misplaced)

### 4.4. ORM ↔ Alembic drift

Запусти `alembic upgrade head` и сравни ORM-модели с фактической схемой через SQLAlchemy Inspector:

```python
from sqlalchemy import inspect
inspector = inspect(engine)
orm_tables = Base.metadata.tables
for table_name, table in orm_tables.items():
    db_columns = {c['name'] for c in inspector.get_columns(table_name)}
    orm_columns = {c.name for c in table.columns}
    print(f"Missing in DB: {orm_columns - db_columns}")
    print(f"Extra in DB: {db_columns - orm_columns}")
```

Что искать: отсутствующие FK (merged_into), отсутствующие индексы.

---

## 5. Стратегия 3: Аудит по модулям

Когда потоки и категории проверены — пройдись по каждому модулю целиком.

### pipeline/

| Файл | На что смотреть |
|---|---|
| manager.py | Лишние eager-инстанциации, хрупкие checkpoint-переходы |
| enrichment_phase.py | Размер (>150 строк = плохо), дублирование логики, ненужные `.commit()` |
| web_client.py | WebClient (requests+BeautifulSoup), URL-валидация, поиск через DuckDuckGo |
| dedup_phase.py | Передаются ли все поля из `merge_cluster()` в `CompanyRow()`? |
| scoring_phase.py | `except Exception` — слишком широкий? |
| checkpoint.py | Явный `session.commit()` внутри `session_scope()`? |
| scraping_phase.py | Playwright браузер на поток (100-200MB)?

### dedup/

| Файл | На что смотреть |
|---|---|
| merger.py | `.get("messengers", {}).items()` — тип messengers? `conflict['cluster_id']` — `.get()`? |
| name_matcher.py | `.get("name", "")` — может вернуть None? O(n²) внутри блоков? |
| phone_cluster.py | `if not cid` — falsy check vs `is None`? |
| validator.py | `_is_internal_url` — mutable list? Порядок операций (cleanup до или после check)? |
| site_matcher.py | `company["id"]` — прямой доступ без `.get()`? `extract_domain` для `file://`? |

### enrichers/

| Файл | На что смотреть |
|---|---|
| classifier.py | `tg_trust.get(...)` — null check? `trust_score * multiplier` — тип? |
| tg_finder.py | Хардкод "ritual"? `len(phone)` — phone может быть int? |
| tg_trust.py | HTTP status code проверяется? Отрицательный trust_score? |
| messenger_scanner.py | `contact_patterns` — dead code? Fragile regex HTML-парсинг? |
| tech_extractor.py | `config` parameter — dead parameter? CMS substring match false positives? |

### scrapers/

| Файл | На что смотреть |
|---|---|
| Все | DRY-нарушение: вынести общие паттерны в BaseScraper |
| web_search.py | DuckDuckGo поиск + скрапинг сайтов, заменил firecrawl |
| jsprav.py | `rstrip("аеоуияью")` снимает символы, не подстроки. `requests.get` без retry |
| dgis.py | Только 3 итерации скролла —.lazy-loaded карточки ниже не подгрузятся |
| jsprav_playwright.py | `a[href*='http']` мачает CDN/tracking-пиксели |

### exporters/

| Файл | На что смотреть |
|---|---|
| markdown.py | `_capitalize_city` не капитализирует после дефиса. `<br>` в Markdown. XSS в URL |
| csv.py | `.contains(f'"{key}"')` — хрупко для user-controlled ключей |

---

## 6. Стратегия исправлений: группы по приоритету

**Никогда не правь всё параллельно.** В granite-crm-db 5 агентов одновременно редактировали `enrichment_phase.py` — результат: 3 конфликта, мёртвый код, лишние HEAD-запросы. Правильно — последовательно, с тестами после каждой группы.

### Группа 1: HIGH + CRITICAL + падающие тесты

- SSRF: `is_safe_url()` во все модули с `fetch_page()`
- XSS: URL scheme whitelist в Markdown-экспортере
- ORM drift: корректирующая Alembic-миграция
- Data loss: `merged_from` и `messengers` в `CompanyRow()`
- NameError: `from loguru import logger` в `alembic/env.py`
- JSON-парсер: переписать на `json.JSONDecoder().raw_decode()`
- 3 падающих теста: обновить assertions

→ `pytest tests/ -q` → должно быть 168/168+

### Группа 2: ORM + pipeline bugs

- Double commit в `checkpoint.py`
- Дублированный `_is_enabled()` → `RegionResolver`
- `enriched_companies.status` не обновлялся

→ `pytest tests/ -q` → 169/169

### Группа 3: SSRF deep fixes

- DNS-rebinding: `socket.getaddrinfo()` в `is_safe_url()`
- URL cleanup order: sanitize до SSRF-проверки
- Credentials в логах: не логировать URL с `user:pass@`

→ `pytest tests/ -q` → 169/169

### Группа 4: Defensive checks

- Null-checks: `.get()` вместо `[]`, `if x is not None` вместо `if x`
- Type-checks: `isinstance(phone, str)` перед `len()`
- Response status: проверять HTTP status в `tg_trust.py`

→ `pytest tests/ -q` → 169/169

### Группа 5: Architecture + LOW

- `__all__` во все 14 модулей
- Dead code cleanup: `contact_patterns`, unused imports, unused config params
- Config wiring: `scraping.timeout` → скраперы
- Extract hardcoded industry logic из `tg_finder.py` → config

→ `pytest tests/ -q` → 169/169

### Группа 6: Config + CLI + housekeeping

- Pin requirements: `sqlalchemy>=2.0,<3.0`
- Обновить тесты: точные assertions вместо `>=`
- Cleanup: удалить debug prints, обновить docstrings

→ `pytest tests/ -q` → 169/169

---

## 7. Правила работы с AI-агентами при аудите

### Правило 1: Один агент = одна задача = один коммит

```
✅ ПРАВИЛЬНО:
  Агент 1: "Создай granite/config.py с Pydantic ConfigSchema" → коммит → тест
  Агент 2: "Подключи ConfigSchema в cli.py" → коммит → тест

❌ НЕПРАВИЛЬНО:
  Агент: "Переделай весь проект" → 50 файлов → 200 багов
```

### Правило 2: Ограничивай скоуп

Промпт агента должен содержать:
- **Файлы-границы:** "Трогай ТОЛЬКО файлы X, Y, Z"
- **Файлы-запреты:** "НЕ трогай файлы A, B, C"
- **Что сделать:** конкретное действие, а не "улучши"
- **Как проверить:** "После изменений запусти `pytest tests/ -q`"

### Правило 3: Последовательно, никогда параллельно

```
Step 0.1 → test → commit → Step 0.2 → test → commit → …
```

Параллельные агенты, редактирующие один файл = гарантированный конфликт.

### Правило 4: Обязательный промпт-шаблон

```markdown
## Правила:
1. Читай файл ПОЛНОСТЬЮ перед редактированием
2. НЕ трогай файлы, не указанные в задании
3. После изменения — запускай: pytest tests/ -q
4. Если тесты упали — фиксируй СРАЗУ, не продолжай
5. Используй Edit, не Write (не перезаписывай файлы)
6. Один логический шаг → один коммит
7. Не рефактори то, что не просили
8. Добавляй `__all__` в новые модули
9. Добавляй docstring к новым функциям/классам
10. Не добавляй зависимости без необходимости
```

### Правило 5: Чекпоинты (worklog)

Каждый агент пишет в `worklog.md`:
```markdown
---
Task ID: 1
Agent: security-fix
Task: Добавить is_safe_url() во все модули с fetch_page()

Work Log:
- Создал is_safe_url() в granite/utils.py
- Добавил вызовы в messenger_scanner.py, tech_extractor.py, tg_finder.py, jsprav.py
- Запустил pytest: 169/169 passed

Stage Summary:
- 6 файлов изменено, +45/-12 lines
- Все тесты проходят
```

Следующий агент читает worklog перед началом работы.

---

## 8. Практические примеры поиска багов

### Пример 1: Конфиг не используется

**Симптом:** `config.yaml` содержит `scraping.timeout: 15`, но `jsprav.py` делает `requests.get(url, timeout=10)`.

**Как найти:** `grep -rn "timeout" --include="*.py"` → сравнить с `config.yaml`.

**Как исправить:** `jsprav.py` должен читать `self.config.get("scraping", {}).get("timeout", 15)` вместо хардкода.

### Пример 2: ORM ↔ Pydantic mismatch

**Симптом:** тест ожидает `tuple (46.35, 48.03)`, Pydantic возвращает `list [46.35, 48.03]`.

**Как найти:** сравнить `models.py` (Pydantic) с `database.py` (ORM). Поле `geo`: Pydantic `list[float]` vs ORM `String("lat,lon")`.

**Как исправить:** тест должен проверять `list`, а не `tuple`. Долгосрочное — переделать `geo` на два Float-столбца или JSON.

### Пример 3: Потеря данных при дедупликации

**Симптом:** `merge_cluster()` возвращает dict с `merged_from` и `messengers`, но после дедупликации в БД этих полей нет.

**Как найти:** `grep -n "CompanyRow(" granite/pipeline/dedup_phase.py` → посмотреть, передаются ли `merged_from` и `messengers`.

**Как исправить:** добавить `merged_from=merged.get("merged_from", [])` и `messengers=merged.get("messengers", {})` в вызов `CompanyRow()`.

### Пример 4: Мёртвый код от параллельных агентов

**Симптом:** `is_safe_url()` добавлена в `utils.py`, но нигде не вызывается.

**Как найти:** `grep -rn "is_safe_url" --include="*.py" | grep -v "def is_safe_url" | grep -v "test_"`.

**Как исправить:** добавить вызовы во все модули с `fetch_page()`. Или: не запускать агентов параллельно.

---

## 9. Аудит тестов

После исправления багов — проверь сами тесты.

### Что искать:

| Проблема | Как найти |
|---|---|
| ORM-Alembic drift | `test_migrations.py` — сравнивает ORM-схему с Alembic |
| Broken assertions | Тест проверяет поведение, которое изменилось |
| Wrong type assertions | tuple vs list, str vs None |
| Weak assertions | `>=`, `is not None` вместо точных значений |
| No negative tests | Нет тестов на error paths, null values, edge cases |
| Duplicated fixtures | Один и тот же fixture в разных файлах |

### Приоритет тестов:

1. `test_migrations.py` — обнаруживает реальную production-проблему (ORM drift)
2. `test_enrichers.py` — покрывает обогащение (самая сложная фаза)
3. `test_pipeline.py` — интеграционный тест полного пайплайна
4. `test_utils.py` — утилиты (normalize_phone, extract_emails, is_safe_url)
5. `test_dedup.py` — дедупликация (кластеризация + слияние)
6. `test_scrapers.py` — парсинг моделей (Pydantic validation)
7. `test_classifier.py` — скоринг (формула + сегментация)
