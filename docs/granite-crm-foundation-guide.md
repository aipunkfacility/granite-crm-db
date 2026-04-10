# Granite CRM — Выстраивание фундамента БД и подготовка к CRM

> Пошаговое руководство по доработке БД, добавлению CRM-таблиц, Pydantic ConfigSchema и подготовке к фазе разработки CRM на примере granite-crm-db

---

## 1. Текущее состояние

### Существующие таблицы (скрапер, 4 шт.)

| Таблица | ORM-класс | Назначение | Ключевые поля |
|---|---|---|---|
| raw_companies | RawCompanyRow | Сырые записи от скраперов | source, source_url, name, phones, address_raw, website, emails, geo, messengers, city, merged_into |
| companies | CompanyRow | Уникальные компании после дедуп | merged_from, name_best, phones, address, website, emails, city, messengers, status, segment |
| enriched_companies | EnrichedCompanyRow | Обогащённые данные | messengers, tg_trust, cms, has_marquiz, is_network, crm_score, segment |

### Связи (FK)

```
raw_companies.merged_into → companies.id (ON DELETE SET NULL)
enriched_companies.id → companies.id (ON DELETE CASCADE, PK=company_id)
```

### SQLite-настройки (database.py)

- `journal_mode=WAL` — параллельные чтение/запись без "database is locked"
- `foreign_keys=ON` — FK constraints активны
- `busy_timeout=5000` — 5 сек ожидания блокировки
- `pool_pre_ping=True` — лишний для SQLite (можно убрать)
- `engine.dispose()` — не вызывается в cli.py (утечка соединений)

### Проблемы текущей схемы

1. **ORM ↔ Alembic drift** — merged_into в ORM, но не в Alembic-миграции
2. **geo как String** — `"lat,lon"` вместо двух Float или JSON
3. **messengers как JSON-dict** — нет схемы, нет валидации ключей
4. **status как String** — нет enum, можно записать любой мусор
5. **Нет __all__** — public API неявный

---

## 2. Phase 0: Стабилизация скрапера

CRM без стабильных данных = пустая CRM. Сначала делаем скрапер надёжным.

### Step 0.1: Pydantic ConfigSchema

**Файл:** `granite/config.py` (NEW)

Зачем: malformed config.yaml сейчас падает через 30 минут работы (runtime KeyError). С ConfigSchema — при старте, с понятным сообщением.

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

### Step 0.2: Подключить ConfigSchema в CLI и Database

**Файлы:** `cli.py`, `database.py`

- `cli.py`: заменить `yaml.safe_load()` на `load_config()`
- `database.py`: `Database.__init__()` принимает config-dict, не парсит YAML
- `.env.example`: добавить пример `DATABASE_URL`

### Step 0.3: Rate Limiting

**Файл:** `granite/utils.py`

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
            # Удалить старые записи
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

**config.yaml:** `scraping.max_requests_per_minute: 30`

### Step 0.4: Refactor enrichment_phase.py

Текущий размер: ~400 строк. Цель: < 150 строк (оркестрация только).

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

### Step 0.5: Проверка

```bash
pytest tests/ -q          # 169/169
python cli.py run --city Астрахань  # completed без ошибок
```

---

## 3. Подготовка существующей БД

### 3.1. Синхронизация ORM ↔ Alembic

**Проблема:** ORM определяет `raw_companies.merged_into`, но Alembic-миграция его не содержит.

**Действие:**
```bash
alembic revision --autogenerate -m "sync_orm_alembic"
alembic upgrade head
pytest tests/test_migrations.py -q
```

**Важно для SQLite:** autogenerate может не добавить `ON DELETE CASCADE`. Проверь сгенерированную миграцию — если CASCADE отсутствует, добавь вручную.

### 3.2. FK CASCADE: проверить поведение

```sql
-- При удалении компании:
DELETE FROM companies WHERE id = 42;

-- CASCADE должно удалить:
-- enriched_companies WHERE id = 42 (ON DELETE CASCADE)
-- raw_companies.merged_into = NULL (ON DELETE SET NULL)
```

Проверь в тесте:
```python
def test_cascade_delete():
    # Создать company, enriched, raw
    session.delete(company)
    session.commit()
    assert session.get(EnrichedCompanyRow, company.id) is None  # CASCADE
    assert raw.merged_into is None  # SET NULL
```

### 3.3. Убрать pool_pre_ping для SQLite

```python
# database.py — убрать pool_pre_ping=True
self.engine = create_engine(
    f"sqlite:///{db_path}",
    echo=False,
    # pool_pre_ping=True,  # ← убрать, лишний SELECT 1 для SQLite
    connect_args={"check_same_thread": False},
)
```

### 3.4. Добавить engine.dispose() в CLI

```python
# cli.py
db = Database(config_path=args.config)
try:
    manager = PipelineManager(config, db)
    manager.run_city(...)
finally:
    db.engine.dispose()
```

Или: сделать Database context manager (`__enter__` / `__exit__`).

### 3.5. Проверить EnrichedCompanyRow.to_dict()

Убедись, что `to_dict()` возвращает все поля, которые понадобятся CRM-API:
```python
def to_dict(self):
    return {
        "id", "name", "phones", "address_raw", "website", "emails", "city",
        "messengers", "tg_trust", "cms", "has_marquiz", "is_network",
        "crm_score", "segment"
    }
```

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

Аналогично — полные определения в dev-плане (granite-crm-dev-plan.md, раздел 2).

### 4.2. Alembic-миграция

```bash
alembic revision --autogenerate -m "add_crm_tables"
```

**Проверить в сгенерированной миграции:**
1. Все FK имеют `ON DELETE CASCADE` (или SET NULL для crm_tasks.company_id)
2. `crm_contacts.company_id` — PRIMARY KEY
3. `crm_email_logs.tracking_id` — UNIQUE
4. Индексы: `crm_contacts.funnel_stage`, `crm_contacts.company_id`

**Ручная правка (SQLite autogenerate не добавляет CASCADE):**
```python
# В сгенерированной миграции:
op.create_table('crm_contacts',
    sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'),
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

## 5. Phase 1.5: CRM-API (FastAPI)

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

### 6.2. JSON в TEXT-полях

**Риск:** `tags`, `color_label`, `action_params` хранятся как TEXT. Конвертация str ↔ list.

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

---

## 7. Контрольный чеклист

### Phase 0

- [ ] `load_config()` валидирует config.yaml (ошибки при старте, не runtime)
- [ ] `pytest tests/` — все тесты проходят
- [ ] `python cli.py run --city Астрахань` — completed без ошибок
- [ ] Rate limiter работает (логи показывают throttled requests)
- [ ] enrichment_phase.py < 150 строк (логика вынесена в enrichers/)

### Существующая БД

- [ ] `alembic upgrade head` — ORM и Alembic синхронизированы
- [ ] FK CASCADE работает (тест на удаление компании)
- [ ] `pool_pre_ping=True` убран
- [ ] `engine.dispose()` вызывается в cli.py
- [ ] `EnrichedCompanyRow.to_dict()` содержит все нужные поля

### Phase 1: CRM-таблицы

- [ ] 8 ORM-моделей созданы в database.py
- [ ] `alembic upgrade head` — 8 новых таблиц созданы
- [ ] CASCADE добавлен вручную в миграцию
- [ ] `test_migrations.py` — ORM и Alembic синхронизированы
- [ ] SEED: шаблоны, auto_rules заполнены
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
