# Granite Workshops DB

Сбор базы гранитных мастерских и производителей памятников по областям России. Поиск контактов (телефон, email, Telegram, WhatsApp, VK) для дальнейшей связи.

## Как работает

Запускаешь город из конфига — программа сама:

1. Определяет область (из `config.yaml`)
2. Подтягивает все населённые пункты этой области (из `data/regions.yaml`)
3. Автоматически ищет поддомены и категории на jsprav.ru через API (`/api/cities/`)
4. Парсит каждый город из источников: jsprav, web_search (DuckDuckGo), 2GIS, yell
5. Дедуплицирует — сливает дубли по телефону и сайту (Union-Find)
6. **Обогащение, проход 1** — сканирует сайты на мессенджеры, ищет Telegram по телефону и названию, определяет CMS
7. **Обогащение, проход 2** — для компаний без сайта/email: поиск через web_search (DuckDuckGo) с заполнением недостающих полей
8. Детекция филиальных сетей (один домен или телефон у 2+ компаний в пределах области)
9. Определяет сегмент (A/B/C/D) по скорингу
10. Экспортирует в CSV или Markdown

Всё локально. Никаких GitHub Actions, никаких облачных сервисов.

## Установка

**Требования:** Python 3.10+

```bash
pip install -r requirements.txt
playwright install chromium
```

### Настройка секретов (опционально)

Для использования API ключей создайте `.env` файл на основе `.env.example`:

```bash
cp .env.example .env
# Отредактируйте .env и добавьте ваши API ключи
```

Поддерживаемые переменные:
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` — Telegram API (опционально)
- `TELESCAN_API_KEY` — Telescan API (опционально)

## Запуск

```bash
# Использовать альтернативный конфиг (по умолчанию: config.yaml)
python cli.py -c config.prod.yaml run "Астрахань"

# Одна область (все города парсятся автоматически)
python cli.py run "Ростов-на-Дону"

# С очисткой старых данных
python cli.py run "Ростов-на-Дону" --force

# Пропустить парсинг, только дедупликация и обогащение
python cli.py run "Ростов-на-Дону" --no-scrape

# Перезапустить только точечное обогащение (сохранить scrape+dedup, заполнить пустые website/email)
python cli.py run "Ростов-на-Дону" --re-enrich

# Все города из конфига
python cli.py run all

# Экспорт
python cli.py export "Ростов-на-Дону" --format csv
python cli.py export "Ростов-на-Дону" --format md

# Экспорт по пресету
python cli.py export-preset "Ростов-на-Дону" hot_leads
```

### Управление базой данных (Alembic миграции)

```bash
# Проверить, нужна ли миграция
python cli.py db check

# Создать миграцию (после изменения моделей в database.py)
python cli.py db migrate "add last_contacted_at to companies"

# Применить миграцию
python cli.py db upgrade head

# Откатить на одну версию назад
python cli.py db downgrade -1

# История миграций
python cli.py db history -v

# Текущая версия схемы
python cli.py db current

# Пометить существующую БД как актуальную (для миграции на Alembic)
python cli.py db stamp head
```

Подробнее: [docs/DATABASE_GUIDE.md](docs/DATABASE_GUIDE.md)

### run.bat (Windows)

Файл `run.bat` в корне проекта. Настройки:

```bat
set CITY=Астрахань          :: Город из config.yaml
set RE_ENRICH=--re-enrich   :: Раскомментировать для перезапуска обогащения
:: set FORCE=--force         :: Раскомментировать для очистки и запуска с нуля
```

## Структура

```
├── cli.py                      # Точка входа (typer CLI)
├── config.yaml                 # Настройки: города, источники, скоринг, пресеты
├── alembic.ini                 # Конфигурация Alembic
├── alembic/
│   ├── env.py                  # Среда миграций (импорт granite.database)
│   ├── script.py.mako          # Шаблон для генерации миграций
│   └── versions/               # Файлы миграций
│       └── ..._initial_schema.py
├── docs/
│   └── DATABASE_GUIDE.md       # Подробный гайд по БД
├── granite/                    # Основной пакет проекта
│   ├── __init__.py
│   ├── database.py             # ORM-модели БД + класс Database (SQLite, WAL, Alembic)
│   ├── models.py               # Pydantic-модели данных
│   ├── utils.py                # Транслитерация, нормализация телефонов, HTTP-запросы
│   ├── regions.py              # Справочник: область → список городов
│   ├── category_finder.py      # Автопоиск поддоменов jsprav.ru через API
│   ├── pipeline/               # Конвейер обработки
│   │   ├── __init__.py
│   │   ├── manager.py          # Оркестратор (все фазы)
│   │   ├── region_resolver.py  # Определение области и городов
│   │   ├── scraping_phase.py   # Фаза 1: Скрапинг
│   │   ├── dedup_phase.py      # Фаза 2: Дедупликация (Union-Find)
│   │   ├── enrichment_phase.py # Фаза 3: Обогащение (мессенджеры, TG, CMS)
│   │   ├── scoring_phase.py    # Фаза 5: Скоринг и сегментация
│   │   ├── export_phase.py     # Фаза 6: Автоэкспорт
│   │   ├── checkpoint.py       # Возобновление с прерванного этапа
│   │   └── status.py           # Вывод статуса
│   ├── scrapers/               # Парсеры источников
│   │   ├── __init__.py
│   │   ├── base.py             # Общий интерфейс скреперов
│   │   ├── jsprav.py           # Jsprav.ru (JSON-LD, быстрый)
│   │   ├── jsprav_playwright.py# Jsprav.ru (Playwright, глубокий, выключен)
│   │   ├── dgis.py             # 2GIS (выключен)
│   │   ├── yell.py             # Yell.ru (выключен)
│   │   ├── web_search.py       # DuckDuckGo web search (поиск + скрапинг сайтов)
│   │   └── _playwright.py      # Общая логика Playwright
│   ├── dedup/                  # Дедупликация
│   │   ├── __init__.py
│   │   ├── phone_cluster.py    # Кластеризация по общим телефонам
│   │   ├── name_matcher.py     # Поиск дубликатов по названиям (fuzzy)
│   │   ├── site_matcher.py     # Кластеризация по домену сайта
│   │   ├── merger.py           # Слияние записей + генерация conflicts.md
│   │   └── validator.py        # Валидация телефонов, email, сайтов
│   ├── enrichers/              # Обогащение данных
│   │   ├── __init__.py
│   │   ├── _tg_common.py       # Общие константы TG (TG_MAX_RETRIES, TG_INITIAL_BACKOFF)
│   │   ├── messenger_scanner.py# Поиск TG/WA/VK парсингом ссылок из HTML
│   │   ├── tg_finder.py        # Поиск Telegram по телефону и названию
│   │   ├── tg_trust.py         # Анализ профиля TG (аватар, описание, бот/канал)
│   │   ├── tech_extractor.py   # Определение CMS сайта
│   │   ├── classifier.py       # Скоринг и сегментация A/B/C/D
│   │   └── network_detector.py # Поиск филиальных сетей
│   └── exporters/              # Экспорт данных
│       ├── __init__.py
│       ├── csv.py              # Экспорт в CSV (utf-8-sig, пресеты)
│       └── markdown.py         # Экспорт в Markdown (пресеты)
├── tests/                      # Тесты (240 шт.)
│   ├── __init__.py
│   ├── test_classifier.py
│   ├── test_dedup.py
│   ├── test_enrichers.py
│   ├── test_migrations.py
│   ├── test_pipeline.py
│   ├── test_refactored_pipeline.py  # Тесты рефакторинга pipeline/
│   ├── test_scrapers.py
│   └── test_utils.py
├── scripts/                    # Отдельные утилиты (legacy)
│   └── ...
├── run.bat                     # Быстрый запуск на Windows
├── requirements.txt
└── data/
    ├── regions.yaml            # Справочник: 40 областей, 566 городов
    ├── category_cache.yaml     # Кэш найденных поддоменов и категорий
    ├── granite.db              # SQLite база (WAL-режим)
    ├── logs/
    │   └── granite.log         # Логи (rotating, 10 MB)
    └── export/                 # CSV/MD экспорт
```

## Конфигурация

### config.yaml — основные настройки

- **`cities`** — список городов с полями:
  - `name` — название города
  - `population` — население (для приоритизации)
  - `region` — область/край/республика
  - `status` — `pending` / `completed` (статус обработки)
  - `geo_center` — `[lat, lon]` центр для карт
- **`scraping`** — общие настройки: задержки, таймауты, user-agent rotation, потоки
- **`sources`** — каждый источник: `enabled: true/false`, категории, поддомены
  - `web_search` — поиск через DuckDuckGo (queries)
  - `jsprav` — категория, `subdomain_map` для нестандартных поддоменов
  - `dgis`, `yell`, `jsprav_playwright` — по умолчанию выключены
  - `google_maps`, `avito` — заглушки (выключены)
- **`dedup`** — настройки дедупликации (порог, слияние по телефону/сайту)
- **`enrichment`** — настройки обогащения:
  - `messenger_pages` — страницы сайта для сканирования мессенджеров
  - `tg_finder` — задержки, поиск через Google
  - `tg_trust` — штраф за пустой профиль
  - `tech_keywords` — ключевые слова для определения: оборудование, производство, портрет, конструктор сайта
- **`scoring`** — **вложенная** структура:
  ```yaml
  scoring:
    weights:       # Баллы за каждый признак
      has_website: 5
      has_telegram: 15
      has_whatsapp: 10
      ...
    levels:        # Пороги для сегментов
      segment_A: 50
      segment_B: 30
      segment_C: 15
  ```

  Полная таблица весов скоринга:

  | Параметр | Баллы | Описание |
  |----------|-------|----------|
  | `has_telegram` | +15 | Найден Telegram |
  | `has_whatsapp` | +10 | Найден WhatsApp |
  | `has_website` | +5 | Есть сайт |
  | `has_email` | +5 | Есть email |
  | `cms_bitrix` | +10 | Сайт на Bitrix |
  | `cms_modern` | +3 | Сайт на WordPress/Tilda (современные CMS) |
  | `has_marquiz` | +8 | На сайте есть виджет Marquiz |
  | `multiple_phones` | +5 | Более одного телефона |
  | `is_network` | +5 | Является частью филиальной сети |
  | `tg_trust_multiplier` | ×2 | Множитель для компаний с живым TG-профилем (trust_score ≥ 2) |

  Пороги сегментов (настраиваются в `scoring.levels`):
  - **A** — ≥ 50 баллов (высокий приоритет)
  - **B** — ≥ 30 баллов
  - **C** — ≥ 15 баллов
  - **D** — < 15 баллов (мало данных)
- **`export_presets`** — готовые фильтры для экспорта (hot_leads, producers_only, with_telegram, cold_email, manual_search, full_dump)
- **`logging`** — уровень логов, ротация, формат
- **`database`** — путь к SQLite (`data/granite.db`)

### data/regions.yaml — города по областям

Статичный справочник (40 областей, 566 городов). Каждая область содержит полный список населённых пунктов. При запуске города скреперы проходят по всем пунктам его области.

Нужно добавить город — просто допиши в файл. Для jsprav.ru поддомены определяются автоматически через API (`/api/cities/`), кэшируются в `data/category_cache.yaml`. Ручные замены — через `subdomain_map` в config.yaml.

## База данных

SQLite с WAL-режимом (параллельные записи без "database is locked"). `busy_timeout=5000ms`. Схема управляется через **Alembic** — миграции применяются автоматически при запуске `Database()`.

Подробная документация: [docs/DATABASE_GUIDE.md](docs/DATABASE_GUIDE.md)

### Таблицы

| Таблица | Назначение | Записей |
|---------|-----------|---------|
| **`raw_companies`** | Сырые данные из скреперов (source, name, phones, website, emails, city) | Много (дубли) |
| **`companies`** | После дедупликации (merged_from, name_best, phones, website, emails, messengers) | Уникальные |
| **`enriched_companies`** | Обогащённые данные (messengers, tg_trust, cms, crm_score, segment, is_network). Связь 1:1 с `companies` по `id` (FK с `ON DELETE CASCADE`) | = companies |

### Связи

```
raw_companies.merged_from ──→ companies.id       (many-to-one, merged_from = JSON list)
enriched_companies.id ──────→ companies.id       (1:1, PK = FK, CASCADE)
```

### Миграции

Схема БД версионирована через Alembic. При изменении ORM-моделей в `granite/database.py` создайте миграцию:

```bash
python cli.py db check          # проверить, есть ли изменения
python cli.py db migrate "... " # создать миграцию
python cli.py db upgrade head   # применить
```

Автоматически: `Database()` вызывает `alembic upgrade head` при инициализации, поэтому при обычном запуске (`python cli.py run ...`) миграции применяются сами.

### Работа с БД в коде

Для безопасной работы с сессией используйте контекстный менеджер `session_scope()` — он автоматически делает commit при успехе и rollback при ошибке:

```python
from granite.database import Database, EnrichedCompanyRow

db = Database()

# Чтение
with db.session_scope() as session:
    companies = session.query(EnrichedCompanyRow).filter_by(city="Волгograd").all()
    for c in companies:
        print(c.name, c.crm_score)
# commit() вызывается автоматически при выходе из with

# Запись
with db.session_scope() as session:
    # Создание или обновление (merge = insert or update)
    enriched = EnrichedCompanyRow(
        id=company_id,  # если нужно обновить существующую
        name="ГранитМастер",
        crm_score=45,
    )
    session.merge(enriched)
# commit() автоматически
```

Подробнее: [docs/DATABASE_GUIDE.md](docs/DATABASE_GUIDE.md) → раздел 9.

## Конвейер

```
run "Астрахань"
  │
  ├─ Поиск категорий (внутри ScrapingPhase)
  │   Автопоиск поддоменов jsprav.ru через API
  │   Проверка категорий HEAD-запросом
  │   Кэширование → data/category_cache.yaml
  │
  ├─ Фаза 1: Скрапинг
  │   Для каждого города Астраханской области:
  │   jsprav (JSON-LD) → web_search (DuckDuckGo) → [dgis, yell — выключены]
  │   Всё сохраняется в raw_companies (БД)
  │
  ├─ Фаза 2: Дедупликация
  │   Кластеризация по телефонам → сайтам (Union-Find)
  │   name_matcher существует но сейчас НЕ используется
  │   Слияние дубликатов → companies (БД)
  │
  ├─ Фаза 3: Обогащение (проход 1)
  │   Для каждой компании:
  │   → сканирование сайта на мессенджеры (парсинг ссылок из HTML)
  │   → поиск TG по телефону (t.me/+7XXX)
  │   → поиск TG по названию (генерация юзернеймов)
  │   → анализ профиля TG: +1 аватар, +1 описание, -1 канал, -1 бот
  │   → определение CMS (Bitrix, WordPress, Tilda и др.)
  │
  ├─ Фаза 3b: Точечный поиск (проход 2, web_search)
  │   Для компаний без сайта или email:
  │   → web_search "Название Город" → берём лучший URL
  │   → scrape URL → извлекаем email, телефоны
  │   → сканируем найденный сайт на мессенджеры и CMS
  │   Пауза 2 сек между запросами
  │
  ├─ Детекция сетей (после обогащения, перед скорингом)
  │   Поиск компаний с филиалами (один домен/телефон у 2+ компаний)
  │   Поиск только в пределах одного города (не между городами)
  │   Нормализация телефонов: 8xxx → 7xxx
  │
  ├─ Фаза 4: Скоринг
  │   Расчёт CRM-score по весам из config.yaml
  │   Пересчёт после детекции сетей (is_network влияет на скор)
  │   Сегментация: A (≥50), B (≥30), C (≥15), D
  │
  └─ Фаза 5: Экспорт
      Автоматический CSV + пресеты при завершении
      Сортировка по crm_score (убывание)
      data/export/{город}_enriched.csv
```

**Примечание:** Детекция сетей не является отдельной фазой с чекпоинтом — она вызывается после обогащения, но до скоринга.

## Чекпоинты

Конвейер запоминает прогресс в БД. При перезапуске — продолжает с прерванного этапа. Логика работает через подсчёт записей в таблицах:

| Этап | Что проверяется | Следующая фаза |
|------|-----------------|----------------|
| `start` | raw_companies = 0 | Скрапинг |
| `scraped` | raw_companies > 0 | Дедупликация |
| `deduped` | companies > 0 | Обогащение |
| `enriched` | enriched_companies > 0 | Скоринг + экспорт |

Флаги:
- `--force` — полная очистка данных по городу, старт с нуля
- `--no-scrape` — пропустить скрапинг, начать с дедупликации
- `--re-enrich` — пропустить скрапинг и дедупликацию, запустить только точечный поиск (заполнение недостающих website/email через web_search)

## Сегменты

| Сегмент | Порог | Описание |
|---------|-------|----------|
| A | ≥ 50 | Есть TG + WA + сайт, высокий скор |
| B | ≥ 30 | Есть мессенджеры + сайт/производство |
| C | ≥ 15 | Есть контакты или сайт |
| D | < 15 | Мало данных, нужна ручная проверка |

Пороги настраиваются в `config.yaml` → `scoring.levels`.

## TG Trust

Анализ Telegram-профиля при скрапинге:

| Признак | Изменение score |
|---------|-----------------|
| Есть аватарка | +1 |
| Есть описание | +1 |
| Это канал/группа | -1 |
| Это бот | -1 |

`trust_score ≥ 2` — живой бизнес-контакт. `trust_score = 0` — мёртвый/фейк.

## Messenger Scanner

Ищет ссылки на мессенджеры парсингом HTML (не из шаблонов конфига):
1. Загружает главную страницу → ищет ссылки t.me, wa.me, vk.com
2. Если TG не найден — ищет страницу контактов по тексту ссылок и URL
3. На странице контактов ищет доп. страницы (о нас, производство, каталог) — до 3 штук
4. Фильтрует: пропускает кнопки "поделиться" (share, joinchat)

## Экспорт

### CSV

Файл: `data/export/{город}_enriched.csv`, кодировка UTF-8 BOM.

Поля: id, name, phones, address, website, emails, segment, crm_score, is_network, cms, has_marquiz, telegram, vk, whatsapp.

Сортировка по crm_score (убывание) — лучшие контакты первыми.

### Пресеты

Готовые фильтры из `config.yaml` → `export_presets`:

| Пресет | Описание |
|--------|----------|
| `hot_leads` | Есть Telegram + высокий CRM-скор (≥50) |
| `high_score` | Сегмент A (высокий приоритет) |
| `with_telegram` | Все компании с Telegram |
| `cold_email` | Нет мессенджеров, но есть email |
| `manual_search` | Нет мессенджеров — нужен прозвон |
| `full_dump` | Все обогащённые компании |

```bash
python cli.py export-preset "Волгоград" hot_leads
python cli.py export-preset all with_telegram
```

## Troubleshooting

### «database is locked»
SQLite в WAL-режиме, но иногда возникает конфликт. Решения:
- Увеличьте `busy_timeout` в `database.py` (сейчас 5000ms)
- Уменьшите `max_threads` в `config.yaml` → `scraping`
- Проверьте, что нет параллельных процессов, пишущих в БД

### Ошибка «no such module: json1»
Старые версии SQLite не поддерживают JSON. Обновите SQLite до 3.38+ или используйте Python с встроенным SQLite новой версии.

### Скрапер возвращает пустые данные
- Проверьте интернет-соединение
- Проверьте, что источник не заблокировал ваш IP
- Попробуйте сменить `user_agent` в `config.yaml`
- Проверьте логи в `data/logs/granite.log`

### Как посмотреть логи
```bash
# Последние 50 строк
tail -n 50 data/logs/granite.log

# Фильтр по уровню
grep "ERROR" data/logs/granite.log
```

### Конвейер упал на середине — как продолжить?
Чекпоинты работают автоматически. Просто запустите снова:
```bash
python cli.py run "Астрахань"
```
Он продолжит с того места, где остановился.

Принудительно начать с нуля:
```bash
python cli.py run "Астрахань" --force
```

### Как проверить статус города
```bash
# Через экспорт (пустой = не обработан)
python cli.py export "Астрахань" --format csv

# Или через БД напрямую
sqlite3 data/granite.db "SELECT COUNT(*) FROM companies WHERE city='Астрахань';"
```

## Разработка

### Требования
- **Python** 3.10+ (используется syntax `str | None`)
- **Playwright** (для скреперов с JS-рендерингом)

### Установка для разработки
```bash
# Клонирование и зависимости
git clone ...
pip install -r requirements.txt
playwright install chromium

# Запуск тестов
python -m pytest tests/ -v
```

### Структура модулей
- `granite/scrapers/` — парсеры источников (jsprav, web_search, dgis...)
- `granite/enrichers/` — обогащение (TG finder, messenger scanner, CMS detector...)
- `granite/dedup/` — дедупликация (phone clustering, name matching, merging...)
- `granite/pipeline/` — фазы обработки (scraping, dedup, enrichment, scoring...)
- `granite/exporters/` — экспорт (CSV, Markdown)

### Как добавить новый скрепер
1. Создать класс в `granite/scrapers/`, унаследовать от `BaseScraper`
2. Реализовать метод `scrape(city)` → `list[RawCompany]`
3. Добавить в `config.yaml` → `sources` с `enabled: true`
4. Добавить тесты в `tests/test_scrapers.py`

### Как добавить новый enricher
1. Создать класс в `granite/enrichers/`
2. Использовать в `enrichment_phase.py` или создать новый метод
3. Добавить тесты в `tests/test_enrichers.py`

### Линтеры (опционально)
```bash
# flake8
pip install flake8
flake8 granite/ --max-line-length=100

# mypy (проверка типов)
pip install mypy
mypy granite/ --ignore-missing-imports
```

## Тесты

240 тестов покрывают: дедупликацию, классификатор, обогащение (TG finder, TG trust, tech extractor, messenger scanner), экспорт (CSV, Markdown, пресеты), скреперы, утилиты, миграции БД.

```bash
# Все тесты
python -m pytest tests/ -v

# Только миграции
python -m pytest tests/test_migrations.py -v
```
