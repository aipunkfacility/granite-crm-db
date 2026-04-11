# Granite CRM — Выстраивание фундамента БД и подготовка к CRM

> Пошаговое руководство по доработке БД, добавлению CRM-таблиц, Pydantic ConfigSchema и подготовке к фазе разработки CRM на примере granite-crm-db

> **Обновлено:** 2026-04-11. Актуализировано под текущее состояние ветки `feat/web-search-scraper`.

---

## 1. Текущее состояние

### Версия и назначение

**Granite CRM DB v0.1.0** — пайплайн лидогенерации для гранитных мастерских (памятники, надгробия). Скрапит справочники по городам России, дедуплицирует, обогащает контактами (TG/WA/VK), скорит и экспортирует CSV/Markdown. CLI на Typer, БД SQLite (WAL), ORM SQLAlchemy 2.x + Alembic.

### Пайплайн (8 фаз)

```
RegionResolver → ScrapingPhase → DedupPhase → EnrichmentPhase
    → ReverseLookupEnricher → NetworkDetector → ScoringPhase → ExportPhase
```

Оркестратор: `PipelineManager` (161 строка, lazy-loading фаз через `@property`). Фазы 6–8 реализованы (ReverseLookup, Crawlee-скраперы, async enrichment).

### Существующие таблицы (3 шт.)

| Таблица | ORM-класс | Назначение | Ключевые поля |
|---|---|---|---|
| raw_companies | RawCompanyRow | Сырые записи от скраперов | source, source_url, name, phones(JSON), address_raw, website, emails(JSON), geo(String), messengers(JSON), city, merged_into(FK) |
| companies | CompanyRow | Уникальные компании после дедуп | merged_from(JSON), name_best, phones(JSON), address, website, emails(JSON), city, messengers(JSON), status, segment, needs_review, review_reason, created_at, updated_at |
| enriched_companies | EnrichedCompanyRow | Обогащённые данные | messengers(JSON), tg_trust(JSON), cms, has_marquiz, is_network, crm_score, segment, updated_at |

### Связи (FK)

```
raw_companies.merged_into → companies.id (ON DELETE — нет CASCADE/SET NULL, дефолт SQLite)
enriched_companies.id → companies.id (ON DELETE CASCADE, PK=company_id)
```

**Внимание:** `merged_into` в ORM не имеет `ondelete="SET NULL"`. Alembic-миграция тоже не добавляет `SET NULL` — FK создан без ondelete-указания. При удалении компании SQLite откатит транзакцию, если есть ссылки.

### SQLite-настройки (database.py)

- `journal_mode=WAL` — параллельные чтение/запись без "database is locked"
- `foreign_keys=ON` — FK constraints активны
- `busy_timeout=5000` — 5 сек ожидания блокировки
- `pool_pre_ping=True` — **лишний для SQLite, генерирует лишний SELECT 1** (остался)
- `engine.dispose()` — вызывается в `cli.py` в трёх местах (`run`, `export`, `export_preset`)
- `session_scope()` — context manager с auto-commit/rollback/close (реализован)

### Стек HTTP-клиентов

Проект использует три HTTP-клиента:

1. **`requests`** — sync HTTP для скрапинга (jsprav, web_search) и обратной совместимости. Используется в `MessengerScanner.scan_website()`, `tg_finder.find_tg_by_phone()` и других sync-методах.

2. **`httpx`** — async HTTP для enrichment. Единый модуль `granite/http_client.py` (219 строк, singleton `httpx.AsyncClient`). Функции: `async_fetch_page()`, `async_head()`, `async_get()` (с exponential backoff), `async_adaptive_delay()`, `run_async()` (sync→async bridge). SSRF protection через `is_safe_url()` и `_sanitize_url_for_log()`. Подключается при `enrichment.async_enabled: true` в config.yaml.

3. **`crawlee`** — управление браузером и парсингом для 2GIS и Yell скраперов. `BeautifulSoupCrawler` (2GIS fallback) и `PlaywrightCrawler` (Yell и reverse lookup). Crawlee управляет собственным жизненным циклом браузера, независимым от Playwright-сессий проекта.

### Активные источники данных (config.yaml)

| Источник | Статус | Метод | Примечание |
|---|---|---|---|
| jsprav | Включён | JSON-LD парсинг + Playwright fallback | Основной источник |
| web_search | Включён | DuckDuckGo (`ddgs.DDGS`) + скрапинг сайтов | ~190 доменов в блоклисте |
| jsprav_playwright | Включён (авто-fallback) | Playwright + stealth | Запускается если JspravScraper не добрал |
| dgis | **Отключён** | Crawlee + 2GIS API | Нужен `DGIS_API_KEY` |
| yell | **Отключён** | Crawlee PlaywrightCrawler | Блокируются анти-ботом |
| google_maps | **Отключён** | Заглушка | Не реализован |
| avito | **Отключён** | Заглушка | Не реализован |

### Ключевые модули (размер)

| Файл | Строк | Назначение |
|---|---|---|
| `pipeline/enrichment_phase.py` | **799** | Обогащение (sync + async), самый тяжёлый файл |
| `scrapers/web_search.py` | **750** | DuckDuckGo + скрапинг сайтов |
| `enrichers/reverse_lookup.py` | **557** | Reverse lookup через 2GIS/Yell |
| `utils.py` | **437** | Утилиты: телефоны, URL, SSRF, HTTP |
| `database.py` | **318** | ORM-модели + Database + Alembic |
| `http_client.py` | **219** | Async HTTP-клиент (singleton) |
| `pipeline/manager.py` | **161** | Оркестратор пайплайна (лёгкий) |

### Проблемы текущей схемы

1. **`merged_into` FK без ondelete** — ORM определяет FK, но не указывает `ON DELETE SET NULL`. Alembic-миграция тоже не добавляет. При удалении компании SQLite откатит транзакцию, если на неё ссылаются сырые записи.
2. **Hardcoded `HEAD_REVISION`** — `'a3f1b2c4d5e6'` дублирован в двух местах `database.py` (строки 165 и 279). При создании новой миграции — нужно обновить вручную в обоих местах.
3. **`pool_pre_ping=True`** — лишний для SQLite, генерирует лишний `SELECT 1` при каждом получении соединения.
4. **Config validation — ad-hoc** — `config_validator.py` (75 строк) проверяет только критичные поля (`cities`, `scoring.weights`, `database.path`). Нет Pydantic-схемы, malformed config может упасть runtime.
5. **`enrichment_phase.py` — 799 строк** — монолит, не рефакторен. Гайд рекомендует <150 строк.
6. **`name_matcher.py` не используется** — модуль существует в `dedup/`, но `dedup_phase.py` кластеризирует только по телефону и сайту (TODO на строке 65).
7. **`_run_async()` дублирован** — одинаковая функция в `http_client.py` и `reverse_lookup.py`.
8. **geo как String** — `"lat,lon"` вместо двух Float или JSON.
9. **messengers как JSON-dict** — нет схемы ключей, нет валидации.
10. **status как String** — нет enum, можно записать любой мусор.

---

## 2. Phase 0: Стабилизация скрапера

CRM без стабильных данных = пустая CRM. Сначала делаем скрапер надёжным.

### Step 0.1: Pydantic ConfigSchema

**Файл:** `granite/config.py` (NEW)

Зачем: сейчас `config_validator.py` делает ad-hoc проверки (75 строк, `isinstance` + `print_status`). Malformed config падает через runtime KeyError. С ConfigSchema — при старте, с понятным Pydantic ValidationError.

```python
from pydantic import BaseModel, Field
from typing import Literal

class CityConfig(BaseModel):
    name: str
    population: int
    region: str
    status: Literal["pending", "completed", "error"] = "pending"
    geo_center: list[float] = Field(default_factory=list)

class ScrapingConfig(BaseModel):
    request_delay: float = 1.5
    timeout: int = 15
    max_retries: int = 3
    max_threads: int = 3

class SourceConfig(BaseModel):
    enabled: bool = True
    max_retries: int = 3
    model_config = {"extra": "allow"}  # loose — поля источников разные

class ScoringWeights(BaseModel):
    has_website: int = 5
    cms_bitrix: int = 10
    has_telegram: int = 15
    has_whatsapp: int = 10
    # ... остальные веса

class ConfigSchema(BaseModel):
    cities: list[CityConfig] = []
    scraping: ScrapingConfig = ScrapingConfig()
    scoring: dict = {}  # loose для весов
    database: dict = {"path": "data/granite.db"}
    model_config = {"extra": "allow"}  # остальные секции не валидируем жёстко

def load_config(path: str = "config.yaml") -> dict:
    """Загрузка и валидация config.yaml через Pydantic."""
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    validated = ConfigSchema(**raw)
    return validated.model_dump()
```

**Что ловит:**
- `population: "123"` → ValidationError (str вместо int)
- `status: "active"` → ValidationError (нет в Literal)
- `geo_center: "abc"` → ValidationError (не list[float])

**Интеграция:** после создания `config.py`:
- `cli.py:load_config()` (строка 44) — заменить `yaml.safe_load()` + `_validate_config()` на `load_config()` из `config.py`
- `database.py:__init__()` (строка 211) — убрать дублирующий `yaml.safe_load()`, принимать готовый dict
- `config_validator.py` — удалить (заменён ConfigSchema)

### Step 0.2: Rate Limiting

**Файл:** `granite/utils.py`

Сейчас rate limiting — только через `adaptive_delay()` (случайная задержка 1.0–3.5с) и per-enricher счётчики в `reverse_lookup.py`. Нет token-bucket per domain.

```python
import time
import threading

class RateLimiter:
    """Token-bucket rate limiter per domain."""
    def __init__(self, max_requests: int = 30, per_seconds: int = 60):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def wait_if_needed(self, domain: str):
        with self._lock:
            now = time.monotonic()
            bucket = self._buckets.setdefault(domain, [])
            cutoff = now - self.per_seconds
            self._buckets[domain] = [t for t in bucket if t > cutoff]
            if len(self._buckets[domain]) >= self.max_requests:
                sleep_time = self.per_seconds - (now - self._buckets[domain][0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self._buckets[domain].append(time.monotonic())

_rate_limiter = RateLimiter()

def rate_limited_fetch(url: str, timeout: int = 15) -> str:
    """fetch_page через rate limiter."""
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ""
    _rate_limiter.wait_if_needed(domain)
    return fetch_page(url, timeout=timeout)
```

**Интеграция:**
- `web_search.py:_scrape_details()` — заменить `fetch_page()` на `rate_limited_fetch()`
- `messenger_scanner.py:scan_website()` — добавить rate limiting на домен сайта
- `config.yaml` — добавить `scraping.max_requests_per_minute: 30`

### Step 0.3: Refactor enrichment_phase.py

Текущий размер: **799 строк**. Цель: < 200 строк (оркестрация только).

**Новые файлы:**

| Файл | Содержимое | Строк |
|---|---|---|
| `granite/enrichers/phone_handler.py` | `normalize_phones()`, валидация, дедуп | ~50 |
| `granite/enrichers/site_enricher.py` | `SiteEnricher.enrich(url)` — tech + messengers | ~100 |
| `granite/enrichers/tg_enricher.py` | `TgEnricher.find(phones, name, config)` — tg_finder + tg_trust | ~80 |

**enrichment_phase.py** после рефакторинга:
```python
class EnrichmentPhase:
    def _enrich_companies(self, session, companies, phone_handler, site_enricher, tg_enricher):
        for c in companies:
            phones = phone_handler.normalize(c.phones)
            if c.website:
                site_data = site_enricher.enrich(c.website)
            tg = tg_enricher.find(phones, c.name_best, self.config)
            # ... собрать erow, session.merge(erow)
```

**Примечание:** файл содержит и sync, и async enrichment — при рефакторинге оба режима должны сохраниться.

### Step 0.4: Убрать дублирование `_run_async()`

`_run_async()` идентична в `http_client.py` (строка 28) и `reverse_lookup.py` (строка 28).

**Действие:** оставить одну реализацию в `http_client.py`, в `reverse_lookup.py` заменить на `from granite.http_client import run_async`.

### Step 0.5: Включить name_matcher в дедупликацию

**Файл:** `granite/pipeline/dedup_phase.py` (строка 64–66)

Сейчас есть TODO:
```python
# Алгоритмы кластеризации (только телефон и сайт — без name_matcher)
# TODO: подключить find_name_matches из granite.dedup.name_matcher
```

**Действие:** добавить третий кластеризатор по имени после `cluster_by_phones` и `cluster_by_site`.

### Step 0.6: Исправить HEAD_REVISION дублирование

**Файл:** `granite/database.py` (строки 165 и 279)

`HEAD_REVISION = 'a3f1b2c4d5e6'` дублирован в двух местах. При новой миграции — нужно обновлять в обоих.

**Действие:** вынести в константу модульного уровня (одна строка).

### Step 0.7: Проверка

```bash
pytest tests/ -q          # все тесты проходят
python cli.py run --city Астрахань  # completed без ошибок
```

---

## 3. Подготовка существующей БД

### 3.1. Исправить FK merged_into (добавить ON DELETE SET NULL)

**Проблема:** `raw_companies.merged_into → companies.id` создан без `ondelete`. ORM (строка 39):
```python
merged_into = Column(Integer, ForeignKey("companies.id"), nullable=True)
```
Нужно:
```python
merged_into = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
```

Alembic-миграция (строка 82–83 initial_schema) тоже не имеет ondelete:
```python
sa.Column('merged_into', sa.Integer(), nullable=True),
sa.ForeignKeyConstraint(['merged_into'], ['companies.id']),
```

**Действие:**
1. Исправить ORM в `database.py`
2. Создать корректирующую миграцию: `alembic revision --autogenerate -m "fix_merged_into_ondelete"`
3. Проверить, что autogenerate добавил `ondelete="SET NULL"` — если нет, добавить вручную
4. Применить: `alembic upgrade head`
5. Проверить: `pytest tests/test_migrations.py -q`

### 3.2. FK CASCADE: проверить поведение enriched_companies

`enriched_companies.id → companies.id` корректно имеет `ON DELETE CASCADE` (строка 73 ORM, строка 61 миграции).

Проверить в тесте:
```python
def test_cascade_delete():
    # Создать company, enriched, raw
    session.delete(company)
    session.commit()
    assert session.get(EnrichedCompanyRow, company.id) is None  # CASCADE
    assert raw.merged_into is None  # SET NULL (после Step 3.1)
```

### 3.3. Убрать pool_pre_ping для SQLite

**Файл:** `granite/database.py` (строка 238)

```python
# database.py — убрать pool_pre_ping=True
self.engine = create_engine(
    f"sqlite:///{db_path}",
    echo=False,
    # pool_pre_ping=True,  # ← убрать, лишний SELECT 1 для SQLite
    connect_args={"check_same_thread": False},
)
```

### 3.4. EnrichedCompanyRow.to_dict() — проверить полноту

Метод существует (строка 99), возвращает: `id`, `name`, `phones`, `address_raw`, `website`, `emails`, `city`, `messengers`, `tg_trust`, `cms`, `has_marquiz`, `is_network`, `crm_score`, `segment`.

**Вывод:** все нужные поля для CRM-API присутствуют. При добавлении CRM-таблиц — может понадобиться поле `city` для фильтрации (уже есть).

---

## 4. Phase 1: CRM-таблицы

### 4.1. Новые ORM-модели

**Файл:** `granite/database.py` — добавить 8 классов

#### crm_contacts (главная CRM-таблица, 1:1 с companies)

```python
class CrmContactRow(Base):
    __tablename__ = "crm_contacts"

    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)

    # Воронка
    funnel_stage = Column(String, default="new", index=True)
    # new → email_sent → email_opened → follow_up_sent →
    # second_follow_up → contacted → portfolio_sent →
    # interested → test_order → regular_client
    # negative: not_interested, unreachable

    # Email-метрики
    email_sent_count = Column(Integer, default=0)
    email_opened_count = Column(Integer, default=0)
    email_replied_count = Column(Integer, default=0)
    last_email_opened_at = Column(DateTime, nullable=True)

    # Мессенджер-метрики
    last_contact_at = Column(DateTime, nullable=True)
    last_contact_channel = Column(String, default="")
    contact_count = Column(Integer, default=0)
    first_contact_at = Column(DateTime, nullable=True)
    last_tg_at = Column(DateTime, nullable=True)
    last_wa_at = Column(DateTime, nullable=True)
    tg_sent_count = Column(Integer, default=0)
    wa_sent_count = Column(Integer, default=0)

    # Ручное
    notes = Column(Text, default="")
    tags = Column(Text, default="[]")  # JSON: ["vip", "test"]
    color_label = Column(String, default="")
    archived = Column(Integer, default=0)

    # Заказы
    order_count = Column(Integer, default=0)
    total_revenue = Column(Integer, default=0)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
```

**Ключевые решения:**
- `company_id = PRIMARY KEY` — связь 1:1, upsert через `INSERT ... ON CONFLICT`
- `funnel_stage = TEXT` — без CHECK constraint, валидация на уровне Pydantic/API
- `tags = TEXT` — JSON-строка, конвертация в Pydantic validator
- Все FK → companies.id с CASCADE

#### crm_email_logs

```python
class CrmEmailLogRow(Base):
    __tablename__ = "crm_email_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("crm_email_campaigns.id"), nullable=True)

    email_to = Column(String, nullable=False)
    email_subject = Column(String, default="")
    email_template = Column(String, default="")

    status = Column(String, default="pending")
    # pending / sent / bounced / opened / replied / failed

    sent_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    bounced_at = Column(DateTime, nullable=True)
    bounce_reason = Column(Text, default="")
    error_message = Column(Text, default="")

    tracking_id = Column(String, unique=True, nullable=True)  # UUID

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

#### crm_touches

```python
class CrmTouchRow(Base):
    __tablename__ = "crm_touches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    channel = Column(String, nullable=False)   # email / tg / wa / manual
    direction = Column(String, nullable=False)  # outgoing / incoming
    status = Column(String, default="sent")     # sent / delivered / read / replied / failed

    subject = Column(String, default="")
    body = Column(Text, default="")
    note = Column(Text, default="")
    response_text = Column(Text, default="")
    response_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

#### crm_tasks

```python
class CrmTaskRow(Base):
    __tablename__ = "crm_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)

    title = Column(String, nullable=False)
    description = Column(Text, default="")
    due_date = Column(String, nullable=True)  # DATE as string YYYY-MM-DD
    priority = Column(String, default="normal")  # low / normal / high / urgent
    status = Column(String, default="pending")   # pending / in_progress / completed / cancelled
    task_type = Column(String, default="follow_up")  # follow_up / send_portfolio / check_response / remind / custom

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
```

#### crm_email_campaigns, crm_templates, crm_auto_rules, crm_orders

Полные определения для оставшихся 4 таблиц (схемы и бизнес-логика) — в `docs/granite-crm-dev-plan.md`, раздел 2.

### 4.2. Alembic-миграция

```bash
alembic revision --autogenerate -m "add_crm_tables"
```

**Проверить в сгенерированной миграции:**
1. Все FK имеют `ON DELETE CASCADE` (или SET NULL для crm_tasks.company_id)
2. `crm_contacts.company_id` — PRIMARY KEY
3. `crm_email_logs.tracking_id` — UNIQUE
4. Индексы: `crm_contacts.funnel_stage`, `crm_contacts.company_id`

**Критично для SQLite:** autogenerate не добавляет CASCADE. Открыть миграцию и вручную добавить `ondelete` к каждому FK:
```python
# В сгенерированной миграции:
op.create_table('crm_contacts',
    sa.Column('company_id', sa.Integer(),
              sa.ForeignKey('companies.id', ondelete='CASCADE'),
              primary_key=True),
    # ...
)
```

```bash
alembic upgrade head
pytest tests/test_migrations.py -q
```

### 4.3. SEED: начальные данные

Добавить в миграцию (или отдельный seed-скрипт):

```python
# crm_templates — 8 записей
templates = [
    {"name": "cold_email_1", "channel": "email", "subject": "...", "body": "...", "is_default": 1},
    {"name": "follow_up_email", "channel": "email", "subject": "Re: ...", "body": "...", "is_default": 0},
    {"name": "final_email", "channel": "email", "subject": "...", "body": "...", "is_default": 0},
    {"name": "tg_intro", "channel": "tg", "body": "...", "is_default": 1},
    {"name": "tg_follow_up", "channel": "tg", "body": "...", "is_default": 0},
    {"name": "wa_intro", "channel": "wa", "body": "...", "is_default": 1},
    {"name": "wa_follow_up", "channel": "wa", "body": "...", "is_default": 0},
    {"name": "wa_final", "channel": "wa", "body": "...", "is_default": 0},
]

# crm_auto_rules — 5 записей
rules = [
    {"name": "follow_up_tg", "trigger_type": "no_response", "trigger_channel": "email",
     "trigger_days": 5, "funnel_stages": "email_sent",
     "action_type": "create_task", "action_params": '{"task_type":"follow_up","channel":"tg"}'},
    {"name": "follow_up_wa", "trigger_type": "no_response", "trigger_channel": "tg",
     "trigger_days": 4, "funnel_stages": "follow_up_sent",
     "action_type": "create_task", "action_params": '{"task_type":"follow_up","channel":"wa"}'},
    {"name": "second_email", "trigger_type": "no_response", "trigger_channel": "wa",
     "trigger_days": 5, "funnel_stages": "second_follow_up",
     "action_type": "create_task", "action_params": '{"task_type":"send_email","template":"final_email"}'},
    {"name": "unreachable", "trigger_type": "schedule", "trigger_days": 21,
     "funnel_stages": "second_follow_up",
     "action_type": "change_stage", "action_params": '{"funnel_stage":"unreachable"}'},
    {"name": "reopen_warm", "trigger_type": "no_response", "trigger_channel": "email",
     "trigger_days": 7, "funnel_stages": "email_opened",
     "action_type": "create_task", "action_params": '{"task_type":"follow_up","channel":"email"}'},
]
```

---

## 4.5. Реализованные расширения (Фазы 6–8)

> Подробный план и критерии — в `docs/EXPANSION_PLAN.md`.

### 4.5.1. Фаза 6: ReverseLookupEnricher — РЕАЛИЗОВАНА

**Файл:** `granite/enrichers/reverse_lookup.py` (557 строк)

Обогащение компаний с малым количеством данных. Ищет компанию в 2GIS и Yell по имени и телефону, сливает найденные контакты с существующими (union, без перезаписи).

**Критерии отбора:** нет мессенджеров, нет email, CRM-score < `min_crm_score` (дефолт: 30).

**Источники:**
- **2GIS API** (приоритет) — httpx sync, пагинация через `page`, escalating backoff при 403/429. Нужен `DGIS_API_KEY`.
- **2GIS Crawlee fallback** — `BeautifulSoupCrawler`, `asyncio.run()`.
- **Yell Crawlee** — `PlaywrightCrawler`, `asyncio.run()`.

**Интеграция:** `PipelineManager` (lazy `@property`) вызывает `ReverseLookupEnricher.run(city)` после EnrichmentPhase и перед NetworkDetector.

**Конфигурация:** `enrichment.reverse_lookup` в config.yaml.

**Известные ограничения:**
- Не полностью async — sync httpx + `asyncio.run()` для Crawlee.
- Нет Crawlee session pool / proxy rotation (только adaptive delay + jitter).
- `_run_async()` дублирован (см. Step 0.4).

### 4.5.2. Фаза 7: Переписанные скраперы 2GIS и Yell — РЕАЛИЗОВАНА

**DgisScraper** (`granite/scrapers/dgis.py`, 397 строк): Crawlee + 2GIS API. Два режима:
1. **API mode** (`DGIS_API_KEY`) — пагинация, escalating backoff при 403/429.
2. **Crawlee fallback** (BeautifulSoupCrawler) — одна страница без пагинации.

Управляет собственным браузером через Crawlee. `DGIS_REGION_IDS` вынесен в `dgis_constants.py` (53 города).

**YellScraper** (`granite/scrapers/yell.py`, 318 строк): Crawlee `PlaywrightCrawler`. Пагинация через «Показать ещё». Категории из `category_finder` или `base_path` из config.

**Оба отключены по дефолту** в config.yaml. Запускаются вне `playwright_session()` scraping_phase.py.

### 4.5.3. Фаза 8: Частичная async-миграция — РЕАЛИЗОВАНА

**Файл:** `granite/http_client.py` (219 строк) — singleton `httpx.AsyncClient`. Функции: `async_fetch_page()`, `async_head()`, `async_get()` (exponential backoff при 429), `async_adaptive_delay()`, `run_async()` (sync→async bridge). SSRF protection.

**EnrichmentPhase** — два режима:
- **sync** (дефолт, `enrichment.async_enabled: false`) — `ThreadPoolExecutor`.
- **async** (`enrichment.async_enabled: true`) — `asyncio.Semaphore` + `httpx.AsyncClient`. БД остаётся sync, запись батчами через `session.merge()`.

Все enrichers имеют sync и async варианты: `scan_website` / `scan_website_async`, `find_tg_by_phone` / `find_tg_by_phone_async`, `check_tg_trust` / `check_tg_trust_async`, `extract` / `extract_async`.

**PipelineManager** — авто-детекция async-фаз через `asyncio.iscoroutinefunction()`.

---

## 5. Phase 1.5: CRM-API (FastAPI)

> Не реализовано. План и код эндпоинтов ниже.

### 5.1. Структура API

```
granite/api/
├── app.py          # FastAPI, CORS, lifespan, include routers
├── deps.py         # get_db(), get_config()
├── schemas.py      # Pydantic-схемы (CompanyResponse, TouchCreate, …)
├── companies.py    # GET /companies, GET /companies/{id}, PATCH /companies/{id}
├── crm.py          # POST /companies/{id}/touches
├── tasks.py        # POST /companies/{id}/tasks, GET /tasks, PATCH /tasks/{id}
├── funnel.py       # GET /funnel
├── campaigns.py    # POST /campaigns, POST /campaigns/{id}/run, GET /campaigns/{id}/stats
├── export.py       # GET /export/{preset}
└── tracking.py     # GET /track/open/{id}.png
```

### 5.2. Ключевые эндпоинты

#### GET /api/v1/companies — список с фильтрами

```python
@router.get("/companies")
def list_companies(
    city: str | None = None,
    segment: str | None = None,
    funnel_stage: str | None = None,
    has_telegram: bool | None = None,
    has_website: bool | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 50,
    order_by: str = "crm_score",
    order_dir: str = "desc",
    db: Session = Depends(get_db),
):
    # JOIN companies + enriched_companies + crm_contacts
    q = (
        db.query(CompanyRow, EnrichedCompanyRow, CrmContactRow)
        .outerjoin(EnrichedCompanyRow, CompanyRow.id == EnrichedCompanyRow.id)
        .outerjoin(CrmContactRow, CompanyRow.id == CrmContactRow.company_id)
    )
    # Фильтры...
    # Пагинация...
    return {"items": [...], "total": N, "page": page, "per_page": per_page}
```

**Риски:**
- `order_by` — column injection → валидировать через Literal
- `has_telegram` — JSON LIKE в SQLite → `enriched_companies.messengers LIKE '%telegram%'`
- `search` — LIKE injection → Pydantic + SQLAlchemy параметризуют

#### PATCH /api/v1/companies/{id} — обновить CRM-данные

```python
@router.patch("/companies/{company_id}")
def update_company(company_id: int, data: ContactUpdate, db: Session = Depends(get_db)):
    contact = db.get(CrmContactRow, company_id)
    if contact is None:
        # Upsert: создаём новый crm_contact
        contact = CrmContactRow(company_id=company_id, **data.model_dump(exclude_unset=True))
        db.add(contact)
    else:
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(contact, k, v)
        contact.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}
```

#### POST /api/v1/companies/{id}/touches — логировать касание

```python
@router.post("/companies/{company_id}/touches")
def create_touch(company_id: int, data: TouchCreate, db: Session = Depends(get_db)):
    # 1. INSERT crm_touches
    touch = CrmTouchRow(company_id=company_id, **data.model_dump())
    db.add(touch)

    # 2. UPDATE crm_contacts
    contact = db.get(CrmContactRow, company_id)
    if contact is None:
        contact = CrmContactRow(company_id=company_id)
        db.add(contact)
    contact.contact_count = (contact.contact_count or 0) + 1
    contact.last_contact_at = datetime.now(timezone.utc)
    contact.last_contact_channel = data.channel
    if data.channel == "tg":
        contact.tg_sent_count = (contact.tg_sent_count or 0) + 1
        contact.last_tg_at = datetime.now(timezone.utc)
    elif data.channel == "wa":
        contact.wa_sent_count = (contact.wa_sent_count or 0) + 1
        contact.last_wa_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "touch_id": touch.id}
```

#### GET /api/v1/funnel — воронка

```python
@router.get("/funnel")
def get_funnel(db: Session = Depends(get_db)):
    from sqlalchemy import func
    rows = db.query(CrmContactRow.funnel_stage, func.count()).group_by(CrmContactRow.funnel_stage).all()
    return {stage: count for stage, count in rows}
```

### 5.3. Pydantic-схемы

```python
# schemas.py
from pydantic import BaseModel
from typing import Literal

class ContactUpdate(BaseModel):
    funnel_stage: str | None = None
    notes: str | None = None
    tags: str | None = None
    color_label: str | None = None
    archived: bool | None = None

class TouchCreate(BaseModel):
    channel: Literal["email", "tg", "wa", "manual"]
    direction: Literal["outgoing", "incoming"] = "outgoing"
    subject: str = ""
    body: str = ""
    note: str = ""

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    due_date: str | None = None  # YYYY-MM-DD
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    task_type: Literal["follow_up", "send_portfolio", "check_response", "remind", "custom"] = "follow_up"
```

---

## 6. Риски и решения

### 6.1. ORM ↔ Alembic drift после добавления CRM-таблиц

**Риск:** autogenerate не добавляет CASCADE для SQLite.

**Решение:** после `alembic revision --autogenerate` — открыть миграцию и вручную добавить `ondelete="CASCADE"` к каждому FK. Проверить через `test_migrations.py`.

**Текущая ситуация:** enriched_companies CASCADE работает корректно (проверено в миграции). Но `merged_into` — без ondelete (см. §3.1).

### 6.2. JSON в TEXT-полях

**Риск:** `tags`, `action_params` хранятся как TEXT. Конвертация str ↔ list.

**Решение:** Pydantic validator:
```python
from pydantic import field_validator
import json

class ContactResponse(BaseModel):
    tags: list[str] = []
    @field_validator("tags", mode="before")
    def parse_tags(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v.startswith("[") else [t.strip() for t in v.split(",") if t.strip()]
        return v or []
```

### 6.3. funnel_stages — TEXT без constraint

**Риск:** можно записать любой мусор в `funnel_stage`.

**Решение:** валидация на уровне API через Pydantic Literal. БД не constraint — это нормально для SQLite, проверка на уровне приложения достаточна.

### 6.4. auto_rules — JSON action_params

**Риск:** `action_params` = JSON-строка с параметрами действия. Парсинг может упасть.

**Решение:** `json.loads()` с try/except. Валидация структуры в бизнес-логике.

### 6.5. Заполнение crm_contacts

**Риск:** после создания таблицы — она пустая. API вернёт 0 записей.

**Решение:** SEED-запрос:
```sql
INSERT OR IGNORE INTO crm_contacts (company_id, funnel_stage, created_at, updated_at)
SELECT id, 'new', datetime('now'), datetime('now')
FROM companies
WHERE id NOT IN (SELECT company_id FROM crm_contacts);
```

### 6.6. Hardcoded HEAD_REVISION

**Риск:** `'a3f1b2c4d5e6'` дублирован в двух местах `database.py` (строки 165 и 279). При новой миграции — забудешь обновить, stamp будет указывать на старую ревизию.

**Решение:** вынести в константу модульного уровня (см. Step 0.6).

---

## 7. Контрольный чеклист

### Phase 0: Стабилизация

- [ ] `load_config()` валидирует config.yaml через Pydantic ConfigSchema (Step 0.1)
- [ ] `config_validator.py` удалён (заменён ConfigSchema)
- [ ] Rate limiter per domain работает в web_search и enrichers (Step 0.2)
- [ ] `enrichment_phase.py` < 200 строк — логика вынесена в enrichers (Step 0.3)
- [ ] `_run_async()` не дублирован — единая реализация в http_client.py (Step 0.4)
- [ ] `name_matcher.py` подключён в dedup_phase.py (Step 0.5)
- [ ] `HEAD_REVISION` — одна константа, не дублируется (Step 0.6)
- [ ] `pytest tests/ -q` — все тесты проходят

### Существующая БД

- [ ] `merged_into` FK имеет `ON DELETE SET NULL` (ORM + миграция) (§3.1)
- [ ] FK CASCADE на enriched_companies — работает (тест) (§3.2)
- [ ] `pool_pre_ping=True` убран из database.py (§3.3)
- [ ] `EnrichedCompanyRow.to_dict()` содержит все нужные поля (§3.4)

### Phase 1: CRM-таблицы

- [ ] 8 ORM-моделей созданы в database.py
- [ ] `alembic upgrade head` — 8 новых таблиц созданы
- [ ] CASCADE добавлен вручную в миграцию (autogenerate не делает это для SQLite)
- [ ] `test_migrations.py` — ORM и Alembic синхронизированы
- [ ] SEED: шаблоны (8), auto_rules (5) заполнены
- [ ] SEED: crm_contacts заполнена для всех companies

### Phase 1.5: CRM-API

- [ ] `uvicorn granite.api.app:app` — сервер запускается на :8000
- [ ] `/docs` — Swagger UI доступен
- [ ] `GET /api/v1/companies?per_page=5` — возвращает JSON с пагинацией
- [ ] `PATCH /api/v1/companies/1` — обновляет funnel_stage и notes
- [ ] `POST /api/v1/companies/1/touches` — логирует касание
- [ ] `POST /api/v1/companies/1/tasks` — создаёт задачу
- [ ] `GET /api/v1/tasks?status=pending` — список задач
- [ ] `GET /api/v1/funnel` — возвращает воронку
