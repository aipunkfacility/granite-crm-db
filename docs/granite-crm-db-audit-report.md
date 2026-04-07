# 🔍 Отчет по аудиту кодовой базы: granite-crm-db

**Репозиторий:** [https://github.com/aipunkfacility/granite-crm-db](https://github.com/aipunkfacility/granite-crm-db)  
**Дата аудита:** 07.04.2026  
**Язык:** Python 3.10+, SQLAlchemy 2.0, Pydantic 2.0, Typer, Playwright  
**Назначение:** CRM-конвейер сбора данных о компаниях (гранитные мастерские) из российских бизнес-каталогов (2ГИС, Yell, Firmsru, JSprav, Firecrawl) с дедупликацией, обогащением, скорингом и экспортом в CSV/Markdown.

---

## Содержание

1. [Общая оценка](#1-общая-оценка)
2. [Критические проблемы (CRITICAL)](#2-критические-проблемы-critical)
3. [Высокий приоритет (HIGH)](#3-высокий-приоритет-high)
4. [Средний приоритет (MEDIUM)](#4-средний-приоритет-medium)
5. [Низкий приоритет (LOW)](#5-низкий-приоритет-low)
6. [Анализ архитектуры](#6-анализ-архитектуры)
7. [Анализ безопасности](#7-анализ-безопасности)
8. [Обработка ошибок](#8-обработка-ошибок)
9. [Проблемы производительности](#9-проблемы-производительности)
10. [Дублирование кода (DRY)](#10-дублирование-кода-dry)
11. [Тестовое покрытие](#11-тестовое-покрытие)
12. [Зависимости](#12-зависимости)
13. [Конфигурация](#13-конфигурация)
14. [Анализ модулей по файлам](#14-анализ-модулей-по-файлам)
15. [Рекомендации](#15-рекомендации)

---

## 1. Общая оценка

Проект представляет собой хорошо структурированный Python-конвейер с четким разделением ответственности между модулями. Архитектура пайплайна (scraping → enrichment → dedup → scoring → export) построена корректно. Используются современные библиотеки (SQLAlchemy 2.0, Pydantic 2.0, Typer, Alembic). Код основного модуля `granite/` написан на хорошем уровне с соблюдением конвенций.

Однако обнаружено **5 критических проблем**, **12 проблем высокого приоритета**, **30+ проблем среднего приоритета** и множество мелких недочетов. Основные области риска: безопасность (отсутствие `.gitignore`, управление секретами), стабильные баги (сломанные импорты, `NameError`), системные проблемы с обработкой ошибок (множество «тихих» перехватов исключений) и значительное дублирование кода.

| Метрика | Значение |
|---------|----------|
| Файлов Python | ~45 |
| Строк кода (основной модуль `granite/`) | ~3500 |
| Строк кода (скрипты `scripts/`) | ~2200 |
| Тестовых файлов | 8 (~107 тестов) |
| Критических проблем | 5 |
| Высокий приоритет | 12 |
| Средний приоритет | 30+ |
| Низкий приоритет | 20+ |

---

## 2. Критические проблемы (CRITICAL)

### 2.1. Отсутствие `.gitignore` — риск утечки данных

**Файл:** корень репозитория  
**Риск:** `data/` каталог содержит SQLite-базу `granite.db` с персональными данными (телефоны, email, адреса реальных людей и компаний). Без `.gitignore` эти данные могут быть случайно закоммичены в публичный репозиторий.

**Рекомендуемое содержимое `.gitignore`:**
```gitignore
data/
*.db
*.db-wal
*.db-shm
__pycache__/
*.pyc
.env
.venv/
dist/
build/
*.egg-info/
```

### 2.2. Сломанный импорт в `checkpoint.py`

**Файл:** `granite/pipeline/checkpoint.py:18, 43`  
**Проблема:** `from database import RawCompanyRow, CompanyRow` — неверный путь импорта. Модуль `database.py` находится в пакете `granite/`, а не в корне. Этот импорт вызовет `ModuleNotFoundError` при нормальном запуске.

```python
# БОК:
from database import RawCompanyRow, CompanyRow

# ИСПРАВЛЕНИЕ:
from granite.database import RawCompanyRow, CompanyRow
```

### 2.3. `NameError` в `messenger_scanner.py`

**Файл:** `granite/enrichers/messenger_scanner.py:29`  
**Проблема:** На строке 29 используется `requests.RequestException`, но модуль `requests` не импортирован. Импортирован только `from requests.exceptions import RequestException` (строка 6). При срабатывании исключения будет `NameError`.

```python
# БОК (строка 29):
except (requests.RequestException, Exception) as e:

# ИСПРАВЛЕНИЕ:
except (RequestException, Exception) as e:
```

### 2.4. `UnboundLocalError` в `messenger_scanner.py`

**Файл:** `granite/enrichers/messenger_scanner.py:38`  
**Проблема:** Переменная `html` используется за пределами блока `try`, в котором она определяется. Если первый блок `try` (строка 26) вызывает исключение и оно перехватывается на строке 29, переменная `html` остаётся неопределённой, что приводит к `UnboundLocalError` на строке 38.

**Исправление:** Инициализировать `html = ""` перед блоком `try` или переструктурировать обработку ошибок.

### 2.5. Сломанные относительные импорты в скраперах

**Файлы:** `granite/scrapers/dgis.py:24`, `granite/scrapers/jsprav_playwright.py:31`  
**Проблема:** `from utils import slugify` — неверный относительный импорт. Будет работать только если запущен из корня проекта с `PYTHONPATH`, но не при нормальном импорте модуля.

```python
# БОК:
from utils import slugify

# ИСПРАВЛЕНИЕ:
from granite.utils import slugify
```

---

## 3. Высокий приоритет (HIGH)

### 3.1. Тихое проглатывание исключения в `database.py`

**Файл:** `granite/database.py:183-185`  
**Проблема:** Блок `except Exception:` в `__init__` перехватывает исключение из `run_alembic_upgrade()` и выполняет fallback на `create_all()` **без какого-либо логирования**. Оператор не узнает, что миграции не применились.

**Рекомендация:** Добавить `logger.warning()` перед fallback.

### 3.2. Риск `AttributeError` в `utils.py`

**Файл:** `granite/utils.py:221`  
**Проблема:** `e.response.status_code` — доступ к атрибуту `response` объекта `HTTPError` без проверки на `None`. В редких случаях `response` может быть `None`.

```python
# ИСПРАВЛЕНИЕ:
status = e.response.status_code if e.response is not None else "?"
logger.warning(f"HTTP {status}: {url}")
```

### 3.3. Жадный regex в `firecrawl_client.py`

**Файл:** `granite/pipeline/firecrawl_client.py:39`  
**Проблема:** `re.search(r'\{.*\}', stdout, re.DOTALL)` — жадный квантификатор `.*` захватит от первого `{` до последнего `}` во всем выводе. Если Firecrawl выводит несколько JSON-объектов или диагностические сообщения с `{}`, будет захвачено слишком много данных.

```python
# ИСПРАВЛЕНИЕ (нежадный вариант):
m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', stdout, re.DOTALL)
# ИЛИ лучше использовать json.JSONDecoder().raw_decode()
```

### 3.4. Телефон не нормализуется перед конструированием URL в `tg_finder.py`

**Файл:** `granite/enrichers/tg_finder.py:48-56`  
**Проблема:** Функция `find_tg_by_phone` не нормализует номер перед интерполяцией в URL. Если caller передает `+79031234567` или `8 903 123-45-67`, URL станет `https://t.me/+7+7903...` или `https://t.me/+8 903...`, что невалидно.

**Рекомендация:** Нормализовать номер (удалить нецифровые символы, проверить длину) перед построением URL.

### 3.5. Нарушение инкапсуляции: импорт приватной функции

**Файлы:** `granite/enrichers/tg_trust.py:5` → `granite/enrichers/tg_finder.py:11`  
**Проблема:** `tg_trust.py` импортирует приватную функцию `_tg_request` из `tg_finder.py`. Функции с префиксом `_` по конвенции являются внутренними и не должны импортироваться другими модулями.

**Рекомендация:** Убрать префикс `_` и сделать функцию частью публичного API, либо вынести в общий модуль `_tg_common.py`.

### 3.6. Загрузка ВСЕХ компаний в память в `enrichment_phase.py`

**Файл:** `granite/pipeline/enrichment_phase.py:50-54`  
**Проблема:** Метод `_run_shallow_enrich` загружает ВСЕ записи `CompanyRow` в память, затем фильтрует их в Python с помощью списка ID. Для крупных городов (тысячи компаний) это может вызвать OOM.

```python
# ТЕКУЩИЙ КОД:
all_companies = session.query(CompanyRow).all()
filtered = [c for c in all_companies if c.id not in enriched_ids]

# РЕКОМЕНДАЦИЯ (SQL):
from sqlalchemy import select
stmt = select(CompanyRow).where(CompanyRow.id.notin_(enriched_ids))
filtered = session.execute(stmt).scalars().all()
```

### 3.7. Silent failure скраперов в `base.py`

**Файл:** `granite/scrapers/base.py:35-38`  
**Проблема:** Метод `run()` перехватывает ВСЕ исключения и возвращает пустой список `[]`. Caller (ScrapingPhase) не может отличить «0 результатов» от «скрапер упал с ошибкой». Трассировка логируется на уровне `debug`, который обычно отключён в продакшене.

**Рекомендация:** Добавить индикатор ошибки — бросать кастомное исключение при фатальных ошибках, либо возвращать специальный sentinel-объект.

### 3.8. Отсутствие обработки ошибок в `scoring_phase.py`

**Файл:** `granite/pipeline/scoring_phase.py`  
**Проблема:** Нет `try/except` вокруг обработки компаний. Если `classifier.calculate_score(d)` или `c.to_dict()` бросает исключение на одной записи, весь цикл прерывается и ни одна компания в городе не получит скор.

**Рекомендация:** Добавить per-company `try/except` по аналогии с `enrichment_phase.py`.

### 3.9. Неперехваченный `json.JSONDecodeError` в `firecrawl.py`

**Файл:** `granite/scrapers/firecrawl.py:41`  
**Проблема:** Если Firecrawl CLI выводит невалидный JSON, `json.load(f)` выбросит `json.JSONDecodeError`, который НЕ перехватывается (перехватываются только `TimeoutExpired` и `FileNotFoundError`). Весь скрапер упадёт.

### 3.10. Preset-фильтры в `config.yaml` ссылаются на несуществующие колонки

**Файл:** `config.yaml:421-450`  
**Проблема:** Экспорт-пресеты содержат SQL-фильтры, использующие колонки, отсутствующие в схеме БД:

| Пресет | Несуществующие колонки |
|--------|----------------------|
| `hot_leads` | `has_production`, `priority_score` |
| `producers_only` | `has_production`, `has_portrait_service`, `status` |
| `cold_email` | `website_status` |
| `manual_search` | `has_production` |

В таблице `enriched_companies` есть `crm_score`, но не `priority_score`. Эти пресеты упадут в runtime.

### 3.11. Загрузка всех компаний в память в `network_detector.py`

**Файл:** `granite/enrichers/network_detector.py:27-30`  
**Проблема:** `scan_for_networks` загружает **все** компании в память (`query.all()`), затем трижды итерирует по ним. Для больших городов это неэффективно. Логику доменной и телефонной кластеризации можно реализовать SQL-запросами с `GROUP BY` / `HAVING`.

### 3.12. Отсутствие управления секретами

**Файл:** проект целиком  
**Проблема:** Нет файла `.env`, нет `python-dotenv` в зависимостях. В `scripts/tg_phone_finder.py` содержатся паттерны-заглушки для API-ключей (`api_id = 12345`, `api_hash = 'your_api_hash'`, `API_KEY = 'your_telescan_api_key'`). Хотя это placeholder-значения, паттерн встраивания секретов в код уже установлен. Разработчик, заполняющий реальные значения, рискует закоммитить их.

---

## 4. Средний приоритет (MEDIUM)

### 4.1. Инconsistency управления сессиями БД

Проект предоставляет удобный контекстный менеджер `db.session_scope()` с автоматическим commit/rollback/close. Однако **6 locations** используют ручной `db.get_session()` с ручным try/finally/close:

| Файл | Строки |
|------|--------|
| `granite/pipeline/checkpoint.py` | 16, 41 |
| `granite/exporters/markdown.py` | 55, 79 |
| `granite/exporters/csv.py` | 115, 142 |
| `granite/enrichers/network_detector.py` | 21 |

**Рекомендация:** Заменить все ручные `get_session()` на `db.session_scope()` для единообразия и безопасности транзакций.

### 4.2. Системная проблема тихого проглатывания исключений

По всему коду обнаружен паттерн широкого `except Exception` с возвратом `None` или `"start"` без логирования:

| Файл | Строки | Эффект |
|------|--------|--------|
| `database.py` | 183 | Alembic fallback без логирования |
| `database.py` | 211 | `session_scope()` — rollback без лога |
| `utils.py` | 125 | `extract_domain()` → `None` без лога |
| `utils.py` | 233 | `check_site_alive()` → `None` без лога |
| `category_finder.py` | 73, 88 | API и HTTP ошибки скрыты |
| `firecrawl_client.py` | 85, 145 | Ошибки логируются на `debug` |
| `checkpoint.py` | 33 | Возвращает `"start"` при любой ошибке |

**Риск:** `checkpoint.py` возвращает `"start"` при ошибке БД, что приведёт к повторному скрапингу всего города и дублированию данных при транзиентных сбоях.

### 4.3. Неправильная аннотация типа `fallback: list = None`

**Файл:** `granite/category_finder.py:165`  
**Проблема:** `fallback: list = None` — type hint говорит `list`, но значение по умолчанию `None`. Технически это работает (Python не проверяет типы в runtime), но вводит в заблуждение. Аналогичные проблемы в `tg_finder.py:68`, `network_detector.py:19`.

```python
# ИСПРАВЛЕНИЕ:
fallback: list | None = None
```

### 4.4. Сравнение JSON-колонки со строкой в `csv.py`

**Файл:** `granite/exporters/csv.py:64`  
**Проблема:** `EnrichedCompanyRow.emails != "[]"` сравнивает JSON-колонку со строковым литералом. В SQLite это может работать (JSON хранится как текст), но в PostgreSQL/MySQL поведение будет другим.

### 4.5. Опасность SSRF в `validator.py`

**Файл:** `granite/dedup/validator.py:50`  
**Проблема:** `validate_website` делает HEAD-запрос к произвольным URL без ограничений. Если URL указывает на внутренний адрес (`http://169.254.169.254/` — metadata AWS), это может быть использовано для SSRF.

### 4.6. Path traversal через `city` в экспортерах

**Файлы:** `granite/exporters/markdown.py:62,90`, `granite/dedup/merger.py:101`  
**Проблема:** `city.lower()` используется напрямую в именах файлов без санитизации. Если `city` содержит `../../etc`, файл будет записан за пределами целевой директории.

### 4.7. Markdown-инъекция в `markdown.py`

**Файл:** `granite/exporters/markdown.py:8,41-42`  
**Проблема:** Функция `_escape_md` экранирует только символ `|`. Если название компании содержит `[link](javascript:alert(1))`, это создаёт валидную Markdown-ссылку. Экранирование применяется только к `name`, но не к `phones`, `site`, `tg_link`.

### 4.8. Фрагильный повторный запрос в `network_detector.py`

**Файл:** `granite/enrichers/network_detector.py:27-30`  
**Проблема:** После `query.update(...)` (bulk update), тот же объект `query` используется для `.all()`. Хотя SQLAlchemy обычно обрабатывает это корректно, семантика хрупкая и может вернуть устаревшие результаты при кэшировании.

### 4.9. Email regex компилируется на каждый вызов

**Файл:** `granite/dedup/validator.py:60`  
**Проблема:** `re.compile(r"...")` вызывается внутри `validate_email()` на каждой итерации. Регулярное выражение нужно вынести на уровень модуля. Также паттерн `[a-zA-Z0-9.\-]+` допускает точки в конце доменной части (`user@domain.`), что невалидно.

### 4.10. Слабое согласование URL в `dgis.py`

**Файл:** `granite/scrapers/dgis.py:68`  
**Проблема:** `source_url = f"https://2gis.ru{href}"` — конкатенация строк вместо `urllib.parse.urljoin`. Если `href` начинается с `//evil.com`, URL станет `https://2gis.ru//evil.com`.

### 4.11. HTML-парсинг через строковые сравнения в `tg_trust.py`

**Файл:** `granite/enrichers/tg_trust.py:32-48`  
**Проблема:** `"bot" in html.lower()` и аналогичные проверки могут давать ложные срабатывания на CSS-классах, JS-переменных или текстовом содержимом страницы. Следует использовать BeautifulSoup для выборки конкретных элементов.

### 4.12. Гиперссылка с F-string в CSS-селекторе в `jsprav_playwright.py`

**Файл:** `granite/scrapers/jsprav_playwright.py:56`  
**Проблема:** `f"a[href*='/{category}/']"` — если `category` содержит спецсимволы (`]`, `"`), CSS-селектор может быть некорректным или создать вектор инъекции.

### 4.13. Раздутый метод `_apply_preset_filter` в `csv.py`

**Файл:** `granite/exporters/csv.py:31-103`  
**Проблема:** Метод вырос до 70+ строк с 8+ regex-сопоставлениями. Эффективно превратился в «mini SQL-парсер». Логику стоит вынести в стратегию (lookup table из `(pattern, handler)` пар) или в отдельный модуль.

### 4.14. O(N×M) поиск в `dedup_phase.py`

**Файл:** `granite/pipeline/dedup_phase.py:72-73`  
**Проблема:** `[d for d in dicts if d["id"] in cl]` выполняется для каждого кластера, давая O(N×M) сложность. При больших датасетах стоит использовать dict-based lookup для O(1) доступа.

### 4.15. Неиспользуемый параметр `db` в `FirecrawlScraper`

**Файл:** `granite/scrapers/firecrawl.py:18`  
**Проблема:** Конструктор принимает `db=None`, но никуда его не сохраняет и не использует. Мёртвый параметр, вводящий в заблуждение.

### 4.16. Избыточное условие в `enrichment_phase.py`

**Файл:** `granite/pipeline/enrichment_phase.py:92`  
**Проблема:** `not e.emails or len(e.emails) == 0` — условие `len(e.emails) == 0` избыточно, так как `not e.emails` уже покрывает `None`, `[]` и пустой список.

### 4.17. 8 bare `except:` в legacy-скриптах

**Файлы:** `scripts/tg_phone_finder.py`, `scripts/scrape_city.py`, `scripts/scrape_fast.py`, `scripts/scrape_fast_utf8.py`  
**Проблема:** 8 экземпляров `except:` без указания типа исключения. Такие блоки перехватывают ВСЁ, включая `KeyboardInterrupt`, `SystemExit`, `MemoryError`.

### 4.18. Ложное согласование городов в `category_finder.py`

**Файл:** `granite/category_finder.py:69`  
**Проблема:** Префиксное согласование `city.lower()[:4]` слишком широкое. «Волг» совпадает и с «Волгоградом», и с «Волгодонском». Может привести к выбору неправильного города.

### 4.19. Depreciated код всё ещё в кодовой базе

| Файл | Что |
|------|-----|
| `models.py:66` | `EnrichedCompany` — помечен как deprecated |
| `models.py:89` | `PipelineRun` — может быть deprecated |
| `database.py:29` | Колонка `merged_into` — deprecated |
| `database.py:96` | `PipelineRunRow` — deprecated |
| `alembic/versions/...` | Таблица `pipeline_runs` создаётся, но не используется |

---

## 5. Низкий приоритет (LOW)

### 5.1. Тип `db_path` в `database.py`

**Файл:** `granite/database.py:153`  
`db_path: str = None` — аннотация `str`, но значение `None`. Следует `str | None = None`.

### 5.2. `PipelineRun.status` как plain `str`

**Файл:** `granite/models.py:99`  
`status: str = "running"` — может принимать любое строковое значение. Следует использовать Enum для предотвращения невалидных состояний.

### 5.3. Дублированный `import os` в `database.py`

**Файл:** `granite/database.py:129`  
`import os` повторяется внутри `run_alembic_upgrade()`, хотя уже импортирован на уровне модуля (строка 7).

### 5.4. Глобальное мутабельное состояние в `regions.py`

**Файл:** `granite/regions.py:8, 13-14`  
`_REGIONS_CACHE` — глобальный мутабельный кэш без блокировок. Теоретически возможна race condition при одновременном доступе из нескольких потоков (хотя на практике безвредна).

### 5.5. Одноэлементный tuple-цикл в `jsprav.py`

**Файл:** `granite/scrapers/jsprav.py:52-56`  
`for variant in (loc_lower,):` — цикл по одноэлементному кортежу бессмысленен. Следует использовать `loc_lower` напрямую.

### 5.6. Хардкоженный User-Agent в `jsprav.py`

**Файл:** `granite/scrapers/jsprav.py:157`  
Одиночный хардкоженный UA-строка, в отличие от Playwright-скраперов, которые рандомизируют UA. Облегчает обнаружение и блокировку.

### 5.7. Устаревший `fake-useragent`

**Файл:** `requirements.txt:10`  
`fake-useragent>=1.4.0` — пакет больше не поддерживается. Рекомендуется `fake-useragent2`.

### 5.8. Статический список UA в `_playwright.py`

**Файл:** `granite/scrapers/_playwright.py:21-33`  
Пул из 6 User-Agent с Chrome 122/121. Со временем эти версии будут выглядеть подозрительно.

### 5.9. Непоследовательные задержки

Некоторые скраперы используют `adaptive_delay()`, другие — `time.sleep(1.0)` (`jsprav.py:240`). Следует унифицировать подход.

### 5.10. `scraper_session` → `self.region` в `scraping_phase.py`

**Файл:** `granite/pipeline/scraping_phase.py:37`  
Параметр называется `region_resolver`, но сохраняется как `self.region`. Несогласованность именования.

### 5.11. Нет `__all__` в `__init__.py`

Модули `granite/__init__.py`, `granite/exporters/__init__.py`, `granite/dedup/__init__.py` не определяют `__all__`, что затрудняет понимание публичного API.

### 5.12. Использование `_sa_instance_state` в `enrichment_phase.py`

**Файл:** `granite/pipeline/enrichment_phase.py:225`  
`hasattr(records[0], '_sa_instance_state')` — проверка приватного атрибута SQLAlchemy. Хрупкое решение, которое может сломаться при обновлении ORM.

### 5.13. Вводящая в заблуждение константа `UA` в `category_finder.py`

**Файл:** `granite/category_finder.py:13`  
`UA` — слишком общее имя для константы заголовков. Рекомендуется `DEFAULT_HEADERS` или `_DEFAULT_UA`.

### 5.14. Magic number в `firecrawl_client.py`

**Файл:** `granite/pipeline/firecrawl_client.py:119`  
`len(stdout) > 50` — магическое число без документации. Если Firecrawl вернёт корректный, но короткий markdown-ответ (< 50 символов), он будет молча отброшен.

### 5.15. Ошибочный комментарий в `test_classifier.py`

**Файл:** `tests/test_classifier.py:54-55`  
Комментарий `# 10+15+5+15+15+10+5+5+15 = 90` содержит арифметическую ошибку. Правильная сумма — 95. Тест проходит, но комментарий вводит в заблуждение.

---

## 6. Анализ архитектуры

### 6.1. Сильные стороны

| Аспект | Оценка | Комментарий |
|--------|--------|-------------|
| Разделение ответственности | ⭐⭐⭐⭐⭐ | Четкое разделение pipeline на фазы: scraping, enrichment, dedup, scoring, export |
| Dependency injection | ⭐⭐⭐⭐ | Конструкторы получают зависимости, не создают их сами |
| Паттерн Template Method | ⭐⭐⭐⭐ | `BaseScraper` с абстрактным `scrape()` — чистая реализация |
| Контекстные менеджеры | ⭐⭐⭐⭐ | `session_scope()`, `playwright_session()` — корректное управление ресурсами |
| Миграции БД | ⭐⭐⭐⭐⭐ | Alembic с batch mode для SQLite, WAL mode, busy_timeout |
| Конфигурация | ⭐⭐⭐⭐ | `config.yaml` + env var fallback (`GRANITE_CONFIG`) |

### 6.2. Слабые стороны

| Аспект | Оценка | Комментарий |
|--------|--------|-------------|
| Обработка ошибок в pipeline | ⭐⭐ | Нет изоляции между фазами — падение одной фазы останавливает весь конвейер |
| Управление секретами | ⭐ | Нет `.env` поддержки, нет secrets management |
| Code reuse (DRY) | ⭐⭐ | Значительное дублирование между скраперами и экспортерами |
| Legacy код | ⭐ | Каталог `scripts/` — свалка устаревшего кода с дублированием |
| Type safety | ⭐⭐⭐ | Типы есть, но покрытие непоследовательно |

### 6.3. Структура модулей

```
granite/
├── __init__.py          # Пустой
├── models.py            # Pydantic модели (RawCompany, Company, EnrichedCompany, PipelineRun)
├── database.py          # SQLAlchemy ORM + Database класс с session_scope()
├── utils.py             # Утилиты (normalize_phone, extract_domain, fetch_page, adaptive_delay)
├── regions.py           # Загрузка регионов из YAML
├── category_finder.py   # Автообнаружение категорий на JSprav
├── scrapers/
│   ├── base.py          # ABC BaseScraper
│   ├── dgis.py          # 2ГИС (Playwright)
│   ├── yell.py          # Yell.ru (Playwright)
│   ├── firmsru.py       # Firms.ru (Playwright)
│   ├── jsprav.py        # JSprav.ru (requests + BeautifulSoup)
│   ├── jsprav_playwright.py  # JSprav (Playwright вариант)
│   ├── firecrawl.py     # Firecrawl CLI обёртка
│   └── _playwright.py   # Контекстный менеджер Playwright
├── pipeline/
│   ├── manager.py       # PipelineManager — оркестратор
│   ├── status.py        # Rich-консоль для статуса
│   ├── checkpoint.py    # Контрольные точки (сохранение прогресса)
│   ├── region_resolver.py  # Определение активных источников/городов
│   ├── firecrawl_client.py # HTTP-клиент для Firecrawl API
│   ├── scraping_phase.py    # Фаза скрапинга
│   ├── enrichment_phase.py  # Фаза обогащения
│   ├── dedup_phase.py       # Фаза дедупликации
│   ├── scoring_phase.py     # Фаза скоринга
│   └── export_phase.py      # Фаза экспорта
├── enrichers/
│   ├── _tg_common.py    # Общие константы Telegram
│   ├── tg_finder.py     # Поиск Telegram по телефону/имени
│   ├── tg_trust.py      # Оценка доверия Telegram-канала
│   ├── classifier.py    # Скоринг и сегментация компаний
│   ├── network_detector.py  # Обнаружение сетей компаний
│   ├── tech_extractor.py    # Определение CMS/технологий
│   └── messenger_scanner.py # Сканирование мессенджеров на сайте
├── dedup/
│   ├── name_matcher.py  # Нечёткое согласование имён
│   ├── phone_cluster.py # Кластеризация по телефонам (Union-Find)
│   ├── site_matcher.py  # Кластеризация по сайтам
│   ├── validator.py     # Валидация email/website
│   └── merger.py        # Слияние дубликатов
└── exporters/
    ├── csv.py           # Экспорт в CSV с пресетами
    └── markdown.py      # Экспорт в Markdown-таблицы
```

---

## 7. Анализ безопасности

### 7.1. Матрица рисков

| Уязвимость | Файл | Серьёзность | exploitability |
|------------|------|-------------|----------------|
| Нет `.gitignore` → утечка PII | Корень | CRITICAL | Высокая — достаточно `git add .` |
| Нет `.env` → секреты в коде | Проект | HIGH | Средняя — требует заполнения placeholder |
| SSRF через `validate_website` | `validator.py:50` | MEDIUM | Низкая — данные из скрапинга, не от пользователя |
| Path traversal в экспорте | `markdown.py`, `merger.py` | MEDIUM | Низкая — города из config.yaml |
| Markdown injection | `markdown.py:41` | MEDIUM | Низкая — данные из скрапинга |
| URL injection в 2ГИС | `dgis.py:68` | MEDIUM | Низкая — href с целевого сайта |
| Несанитированный CSS-селектор | `jsprav_playwright.py:56` | MEDIUM | Низкая — категории из config |

### 7.2. Положительные аспекты безопасности

- `subprocess.run()` с list-аргументами (не `shell=True`) — защита от shell-injection
- `yaml.safe_load()` (cli.py, env.py) — защита от произвольного выполнения кода через YAML
- `check_same_thread=False` только для SQLite — правильно для многопоточного доступа
- WAL mode + busy_timeout — корректная настройка для конкурентного чтения

---

## 8. Обработка ошибок

### 8.1. Системные проблемы

**Паттерн 1: Тихое проглатывание (`except Exception: return None`)**
Встречается в 10+ locations. Ошибки не логируются, отладка в продакшене невозможна. Особенно опасно в `checkpoint.py`, где возврат `"start"` при ошибке БД приводит к повторному скрапингу и дублированию данных.

**Паттерн 2: Отсутствие изоляции фаз в pipeline**
`PipelineManager.run_city()` не оборачивает вызовы фаз в `try/except`. Падение одной фазы (например, `NetworkDetector`) прерывает весь pipeline для города без обновления checkpoint.

**Паттерн 3: Commit per-record вместо транзакционной группы**
`enrichment_phase.py` делает `session.commit()` для каждой компании внутри цикла. Если процесс упадёт в середине, БД окажется в частичном состоянии без возможности определить, какие компании были обработаны.

### 8.2. Хорошие паттерны

- `session_scope()` с auto-commit/rollback/close — правильная реализация
- `BaseScraper.run()` с логированием traceback — хороший diagnostic
- Playwright stealth fallback — корректная деградация

---

## 9. Проблемы производительности

| Проблема | Файл | Влияние | Решение |
|----------|------|---------|---------|
| Загрузка всех компаний в память | `enrichment_phase.py:50` | OOM при больших городах | SQL `NOT IN` |
| Загрузка всех компаний для network detection | `network_detector.py:30` | O(3n) итераций | SQL `GROUP BY` |
| O(N×M) поиск кластеров | `dedup_phase.py:72` | Медленно при N>1000 | Dict-based lookup |
| N+1 page loads в jsprav_playwright | `jsprav_playwright.py:121` | N+1 HTTP запросов | `page.go_back()` или табы |
| 50+ subprocess calls в Firecrawl | `firecrawl.py:96-117` | >1 час при 50 доменах | Пакетная обработка |
| `inner_html()` для каждой карточки | `yell.py:80` | Дорого при больших картах | CSS-селекторы напрямую |
| Email regex на каждый вызов | `validator.py:60` | Микро-оптимизация | Pre-compile |
| Хардкоженный `time.sleep` | `jsprav.py:240` | Медленнее чем нужно | `adaptive_delay()` |
| O(n²) fuzzy matching в name_matcher | `name_matcher.py:38-42` | 500K сравнений для блока | Early termination |

---

## 10. Дублирование кода (DRY)

### 10.1. Критическое дублирование

| Дубликат | Объём | Рекомендация |
|----------|-------|--------------|
| `yell.py` + `firmsru.py` | ~80% идентичного кода | Базовый класс `PlaywrightCardScraper` |
| Messenger extraction | 4 копии в скраперах | Общая функция `_extract_messengers(elem)` |
| `scrape_fast.py` + `scrape_fast_utf8.py` | Полный дубликат (разные кодировки) | Удалить UTF-16 версию |
| `export_city` vs `export_city_with_preset` | ~80% идентичного кода в CSV и Markdown | Общий метод `_export_base()` |

### 10.2. Конкретные примеры

**Messenger extraction** — копипаста в 4 файлах:
```python
# Одинаковый код в dgis.py, yell.py, firmsru.py, jsprav_playwright.py
messengers = {}
for a in card.query_selector_all("a[href*='t.me'], a[href*='telegram'], a[href*='wa.me'], a[href*='whatsapp']"):
    href = a.get_attribute("href") or ""
    if "t.me" in href or "telegram" in href:
        messengers["telegram"] = href
    elif "wa.me" in href or "whatsapp" in href:
        messengers["whatsapp"] = href
```

**Рекомендация:** Вынести в `granite/scrapers/base.py`:
```python
@staticmethod
def extract_messengers(element) -> dict[str, str]:
    """Извлечение ссылок на мессенджеры из HTML-элемента."""
    ...
```

---

## 11. Тестовое покрытие

### 11.1. Текущее состояние

| Тестовый файл | Тесты | Качество |
|---------------|-------|----------|
| `test_classifier.py` | 3 | ⭐⭐⭐ |
| `test_scrapers.py` | 16 | ⭐⭐⭐⭐ |
| `test_migrations.py` | 7 | ⭐⭐⭐⭐ |
| `test_pipeline.py` | 12 | ⭐⭐⭐⭐ |
| `test_enrichers.py` | 25 | ⭐⭐⭐⭐⭐ |
| `test_utils.py` | 12 | ⭐⭐⭐⭐ |
| `test_dedup.py` | 12 | ⭐⭐⭐⭐ |
| `test_refactored_pipeline.py` | 22 | ⭐⭐⭐⭐⭐ |
| **Итого** | **~107** | |

### 11.2. Группы покрытия

- **Хорошо покрыто:** Utils, Dedup, Enrichers, Pipeline components, Scrapers (mock-based)
- **Не покрыто:** `cli.py` (0 тестов), `category_finder.py`, `network_detector.py`, все `scripts/`

### 11.3. Недостающие тесты

1. **CLI (`cli.py`)** — ни один subcommand не протестирован
2. **CategoryFinder** — нет тестов для автообнаружения категорий
3. **NetworkDetector** — нет тестов для обнаружения сетей
4. **PipelineManager.run_city()** — нет интеграционного теста
5. **Конфигурация** — нет валидации схемы `config.yaml`
6. **SSRF-защита** — нет тестов для валидации URL

### 11.4. Рекомендации

- Добавить `pytest-cov` в зависимости для измерения покрытия
- Создать `conftest.py` для общих фикстур (моки БД, тестовые данные)
- Добавить интеграционные тесты для полного pipeline
- Добавить `--cov-fail-under=60` в CI

---

## 12. Зависимости

### 12.1. Обзор

| Пакет | Версия | Риск | Статус |
|-------|--------|------|--------|
| `pydantic` | >=2.0 | Низкий | Актуальный, стабильный |
| `sqlalchemy` | >=2.0 | Низкий | Актуальный, стабильный |
| `alembic` | >=1.13.0 | Низкий | Стандартный инструмент |
| `pyyaml` | >=6.0 | Низкий | `safe_load` используется корректно |
| `requests` | >=2.31.0 | Низкий | Стандартный HTTP-клиент |
| `tenacity` | >=8.2.0 | Низкий | Библиотека ретраев |
| `fake-useragent` | >=1.4.0 | **Средний** | **Deprecated/unmaintained** |
| `beautifulsoup4` | >=4.12.0 | Низкий | Стандартный парсер |
| `lxml` | >=5.0.0 | Низкий | Стандартный парсер |
| `rapidfuzz` | >=3.0 | Низкий | Быстрое нечёткое сравнение |
| `playwright` | >=1.40.0 | Низкий | Актуальный |
| `playwright-stealth` | >=1.0.6 | Низкий | Антидетект |
| `pytest` | >=7.0 | Низкий | Стандартный тест-фреймворк |
| `loguru` | >=0.7.0 | Низкий | Удобный логгер |
| `typer` | >=0.9.0 | Низкий | Современный CLI |
| `rich` | >=13.0.0 | Низкий | Терминальный вывод |

### 12.2. Отсутствующие зависимости

- `python-dotenv` — для `.env` поддержки
- `pytest-cov` — для отчётов о покрытии
- `responses` или `httpx` — для более качественного мокирования HTTP в тестах

### 12.3. Версионирование

Все зависимости указаны с `>=` (минимальная версия), без верхних границ. Рекомендуется использовать lock-файл (`poetry.lock` или `uv.lock` — файл `uv.lock` уже существует в `scripts/`).

---

## 13. Конфигурация

### 13.1. Проблемы `config.yaml`

| Проблема | Детали |
|----------|--------|
| Несуществующие колонки в preset-фильтрах | `has_production`, `priority_score`, `website_status`, `has_portrait_service` |
| Нет schema validation | Нет проверки структуры конфига при загрузке |
| Нет документа версионирования | Нет `config_version` для миграции конфига |

### 13.2. Рекомендации

1. Добавить Pydantic-модель для валидации `config.yaml`
2. Исправить названия колонок в preset-фильтрах на актуальные (`crm_score` вместо `priority_score`)
3. Добавить `config_version: 1` для future-proofing

---

## 14. Анализ модулей по файлам

### `granite/models.py` — Хорошо ⭐⭐⭐⭐

Чистые Pydantic-модели с правильными аннотациями типов. `Field(default_factory=...)` везде. Есть deprecated-модель `EnrichedCompany` и `PipelineRun` — следует удалить.

### `granite/database.py` — Приемлемо ⭐⭐⭐

Хороший паттерн `session_scope()`. Правильная настройка SQLite (WAL, foreign keys, busy_timeout). Проблемы: тихое проглатывание исключений, фрагильный type hint `db_path: str = None`, deprecated сущности.

### `granite/utils.py` — Приемлемо ⭐⭐⭐

Полезные утилиты с хорошими docstrings. Проблемы: риск `AttributeError` при доступе к `e.response`, тихие возвраты `None`, regex в `slugify()` не схлопывает последовательные дефисы.

### `granite/pipeline/manager.py` — Хорошо ⭐⭐⭐⭐

Чистый оркестратор с DI. 807 строк старого кода рефакторены до ~60. Не хватает error isolation между фазами.

### `granite/scrapers/dgis.py` — Средне ⭐⭐⭐

Хрупкие CSS-селекторы. Сломанный импорт `from utils import slugify`. Нет `urljoin` для URL.

### `granite/scrapers/yell.py` — Средне ⭐⭐⭐

Чистый код, но ~80% дублирования с `firmsru.py`. Дорогой `inner_html()` на каждой карточке.

### `granite/scrapers/firmsru.py` — Средне ⭐⭐⭐

Практически копия `yell.py`. Нарушение DRY.

### `granite/scrapers/jsprav.py` — Хорошо ⭐⭐⭐⭐

Самый продвинутый скрапер. Хорошая пагинация с фоллбэками. Хардкоженный UA, одноэлементный tuple-цикл.

### `granite/scrapers/firecrawl.py` — Средне ⭐⭐

Неиспользуемый параметр `db`. Неперехваченный `JSONDecodeError`. 50+ subprocess calls без пакетизации.

### `granite/enrichers/tg_finder.py` — Средне ⭐⭐⭐

Логический баг с ненормализованным телефоном. Импорт приватной функции. Бизнес-специфичный хак `'ritualnyeuslugi'`.

### `granite/enrichers/messenger_scanner.py` — Плохо ⭐⭐

`NameError` (строка 29), `UnboundLocalError` (строка 38), regex-based HTML parsing вместо BeautifulSoup.

### `granite/enrichers/network_detector.py` — Средне ⭐⭐⭐

Загрузка всех записей в память. Фрагильный повторный запрос после bulk update.

### `granite/dedup/` — Хорошо ⭐⭐⭐⭐

Чистый Union-Find, корректное нечёткое согласование. O(N×M) lookup в pipeline — оптимизировать.

### `granite/exporters/csv.py` — Средне ⭐⭐⭐

Раздутый `_apply_preset_filter`. Сравнение JSON со строкой. Дублирование кода с markdown.py.

### `granite/exporters/markdown.py` — Средне ⭐⭐⭐

Markdown injection. Только pipe-экранирование. Дублирование кода с csv.py.

### `cli.py` — Хорошо ⭐⭐⭐⭐

Чистый Typer CLI. Глобальное мутабельное состояние (`_config_path`) — незначительный минус. Нет тестов.

### `scripts/` — Плохо ⭐⭐

Legacy-код с bare except, UTF-16 LE файлами, хардкоженными секретами. Требует очистки или удаления.

---

## 15. Рекомендации

### Приоритет 1: Исправить критические баги (немедленно)

1. Создать `.gitignore` с исключениями для `data/`, `.env`, `__pycache__/`
2. Исправить импорт в `checkpoint.py:18,43` → `from granite.database import ...`
3. Исправить `messenger_scanner.py:29` → `except (RequestException, Exception)`
4. Исправить `messenger_scanner.py` — инициализировать `html` до `try`
5. Исправить импорты в `dgis.py:24`, `jsprav_playwright.py:31` → `from granite.utils import ...`

### Приоритет 2: Улучшить обработку ошибок (среднесрочно)

6. Добавить `logger.warning()` перед всеми silent fallback-возвратами
7. Добавить error isolation в `PipelineManager.run_city()` — обернуть каждую фазу в try/except
8. Добавить per-company error handling в `scoring_phase.py`
9. Добавить `json.JSONDecodeError` в `firecrawl.py:41`
10. Заменить все `get_session()` на `session_scope()` (6 locations)

### Приоритет 3: Рефакторинг (долгосрочно)

11. Вынести общий код из `yell.py`/`firmsru.py` в `PlaywrightCardScraper`
12. Создать общую функцию `extract_messengers()` в `base.py`
13. Объединить `export_city` и `export_city_with_preset` в экспортерах
14. Заменить `enrichment_phase.py:50-54` на SQL `NOT IN`
15. Оптимизировать `dedup_phase.py:72` — dict-based lookup
16. Удалить deprecated-код (`EnrichedCompany`, `PipelineRunRow`, `pipeline_runs`)
17. Добавить Pydantic-схему валидации для `config.yaml`

### Приоритет 4: Улучшение качества (фоново)

18. Добавить `python-dotenv` в зависимости
19. Заменить `fake-useragent` на `fake-useragent2`
20. Исправить названия колонок в preset-фильтрах `config.yaml`
21. Добавить тесты для `cli.py` и `category_finder.py`
22. Добавить `pytest-cov` и threshold в CI
23. Создать `conftest.py` с общими фикстурами
24. Унифицировать задержки (все скраперы → `adaptive_delay()`)
25. Очистить или удалить `scripts/`

---

*Отчёт сгенерирован автоматически на основе полного аудита ~45 файлов Python, 8 тестовых файлов, конфигурационных файлов и документации.*
