# Granite CRM — План накатывания CRM поверх БД

> Дата: 2026-04-11
> Аудит: анализ актуального состояния кодовой базы (branch `feat/web-search-scraper`)
> Цель: минимальным путём добавить CRM-слой (таблицы + API) поверх работающего пайплайна

---

## 0. Текущее состояние (что УЖЕ работает)

| Компонент | Статус | Детали |
|---|---|---|
| Пайплайн скрапинга | ✅ | 6 scrapers, 48 городов, ThreadPoolExecutor |
| Дедупликация | ✅ | phone + site clustering, Union-Find, merger |
| Обогащение | ✅ | 9 enrichers, sync + async (httpx), 799 строк |
| HTTP-клиент | ✅ | `granite/http_client.py` (220 строк, singleton) |
| Alembic | ✅ | 2 миграции, WAL, FK, batch mode |
| engine.dispose() | ✅ | 4 call site в cli.py |
| DGIS_REGION_IDS DRY | ✅ | `dgis_constants.py` — единый источник |
| config.yaml | ✅ | 555 строк, все секции |
| CLI (Typer) | ✅ | run, export, db upgrade/downgrade/migrate/check |

### Что НЕ готово (блокеры CRM)

| Компонент | Статус | В чём проблема |
|---|---|---|
| CRM-таблицы | ❌ | Нет ни одной crm_* таблицы в ORM или миграциях |
| CRM API | ❌ | Нет FastAPI, нет `granite/api/` |
| name_matcher | ⚠️ | Код готов (77 строк), TODO в dedup_phase.py:64 — не wired |
| pool_pre_ping | ⚠️ | Лишний для SQLite, guide рекомендует убрать |

---

## Фаза 0: Быстрые фиксы (15 минут)

> Цель: починить мелкие проблемы, которые мешают качеству данных.
> Риск: нулевой — точечные изменения без архитектурных последствий.

### 0.1 Убрать pool_pre_ping для SQLite

**Файл:** `granite/database.py:238`

**Что сделать:** Убрать `pool_pre_ping=True` из `create_engine()`.

```python
# Было:
self.engine = create_engine(
    f"sqlite:///{db_path}",
    echo=False,
    pool_pre_ping=True,  # ← убрать
    connect_args={"check_same_thread": False},
)

# Стало:
self.engine = create_engine(
    f"sqlite:///{db_path}",
    echo=False,
    connect_args={"check_same_thread": False},
)
```

**Почему:** `pool_pre_ping` делает лишний `SELECT 1` при каждом checkout соединения. Для SQLite с `check_same_thread=False` это бессмысленно — соединение не переиспользуется между потоками.

### 0.2 Подключить name_matcher в дедуп

**Файл:** `granite/pipeline/dedup_phase.py:64-66`

**Что сделать:** Убрать TODO, импортировать `find_name_matches` и добавить в цепочку кластеризации.

```python
# Было (строка 14-18):
from granite.dedup.phone_cluster import cluster_by_phones
from granite.dedup.site_matcher import cluster_by_site
from granite.dedup.merger import merge_cluster
from granite.dedup.validator import validate_phones, validate_emails

# Стало (добавить импорт):
from granite.dedup.phone_cluster import cluster_by_phones
from granite.dedup.site_matcher import cluster_by_site
from granite.dedup.name_matcher import find_name_matches
from granite.dedup.merger import merge_cluster
from granite.dedup.validator import validate_phones, validate_emails
```

```python
# Было (строка 64-68):
            # Алгоритмы кластеризации (только телефон и сайт — без name_matcher)
            # TODO: подключить find_name_matches из granite.dedup.name_matcher
            # для дедупликации по названиям (см. name_matcher.py)
            clusters_phone = cluster_by_phones(dicts)
            clusters_site = cluster_by_site(dicts)

# Стало:
            # Алгоритмы кластеризации: телефон + сайт + название
            clusters_phone = cluster_by_phones(dicts)
            clusters_site = cluster_by_site(dicts)

            # Дедуп по названиям (порог из config или дефолт 88)
            name_threshold = self.db.config.get("dedup", {}).get(
                "name_similarity_threshold", 88
            )
            clusters_name = find_name_matches(dicts, threshold=name_threshold)
```

```python
# Было (строка 71):
            superclusters = self._union_find(dicts, clusters_phone + clusters_site)

# Стало:
            superclusters = self._union_find(
                dicts, clusters_phone + clusters_site + clusters_name
            )
```

**Важно:** `DedupPhase.__init__` не хранит `config`. Нужно либо:
- Передать config в `__init__` (меньше изменений),
- Либо использовать дефолтный порог 88 (совсем минимально).

**Рекомендуемый вариант — передать config:**

```python
class DedupPhase:
    def __init__(self, db: Database, config: dict | None = None):
        self.db = db
        self.config = config or {}
```

Обновить вызов в `granite/pipeline/manager.py`:
```python
# Было:
self.dedup = DedupPhase(db)
# Стало:
self.dedup = DedupPhase(db, config)
```

### Тестирование фазы 0

```bash
# Запустить все существующие тесты — не должно сломаться
cd /home/z/my-project/granite-crm-db
source .venv/bin/activate
pytest tests/ -q

# Проверить дедуп с name_matcher на тестовой БД (если есть города)
python cli.py run "Астрахань" --no-scrape --re-enrich

# Проверить что name_matcher логирует совпадения (в логах должно быть)
rg "Name matcher" data/logs/granite.log
```

**Критерий успеха:**
- [ ] `pytest tests/ -q` — все тесты проходят
- [ ] В логах есть строки `Name matcher: N компаний, M сравнений, K совпадений`
- [ ] Количество уникальных компаний после дедуп не выросло (не должно быть ложных мёржей)

### Коммит

```
feat: wire name_matcher into dedup pipeline, remove pool_pre_ping

- Убран pool_pre_ping=True из create_engine() (лишний для SQLite)
- find_name_matches() подключена в dedup_phase.py (порог из config.yaml)
- DedupPhase теперь принимает config для чтения name_similarity_threshold
```

---

## Фаза 1: CRM-таблицы — ORM-модели (1-1.5 часа)

> Цель: добавить 8 CRM-таблиц в `database.py` с правильными FK и индексами.
> Зависимость: Фаза 0 (коммит).
> Риск: низкий — новые таблицы не затрагивают существующие 3.

### 1.1 Модели для добавления

Все модели добавляются в **`granite/database.py`** после `EnrichedCompanyRow`.

#### crm_contacts (главная CRM-таблица, 1:1 с companies)

```python
class CrmContactRow(Base):
    __tablename__ = "crm_contacts"

    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )

    # Воронка
    funnel_stage = Column(String, default="new", index=True)
    # Стадии: new, email_sent, email_opened, follow_up_sent,
    # second_follow_up, contacted, portfolio_sent,
    # interested, test_order, regular_client
    # Негативные: not_interested, unreachable

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
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        return f"<{self.__class__.__name__}(company_id={self.company_id}, stage={self.funnel_stage})>"
```

#### crm_touches (лог всех касаний)

```python
class CrmTouchRow(Base):
    __tablename__ = "crm_touches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    channel = Column(String, nullable=False)    # email / tg / wa / manual
    direction = Column(String, nullable=False)  # outgoing / incoming
    status = Column(String, default="sent")     # sent / delivered / read / replied / failed

    subject = Column(String, default="")
    body = Column(Text, default="")
    note = Column(Text, default="")
    response_text = Column(Text, default="")
    response_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.id}, channel={self.channel})>"
```

#### crm_tasks (задачи / follow-up)

```python
class CrmTaskRow(Base):
    __tablename__ = "crm_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title = Column(String, nullable=False)
    description = Column(Text, default="")
    due_date = Column(String, nullable=True)     # DATE as string YYYY-MM-DD
    priority = Column(String, default="normal")  # low / normal / high / urgent
    status = Column(String, default="pending", index=True)  # pending / in_progress / completed / cancelled
    task_type = Column(String, default="follow_up")  # follow_up / send_portfolio / check_response / remind / custom

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.id}, title={self.title!r})>"
```

#### crm_email_logs (трекинг писем)

```python
class CrmEmailLogRow(Base):
    __tablename__ = "crm_email_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id = Column(Integer, nullable=True)  # FK на crm_email_campaigns (пока без constraint)

    email_to = Column(String, nullable=False)
    email_subject = Column(String, default="")
    email_template = Column(String, default="")

    status = Column(String, default="pending")  # pending / sent / bounced / opened / replied / failed

    sent_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    bounced_at = Column(DateTime, nullable=True)
    bounce_reason = Column(Text, default="")
    error_message = Column(Text, default="")

    tracking_id = Column(String, unique=True, nullable=True)  # UUID для tracking pixel

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

#### crm_templates (шаблоны сообщений)

```python
class CrmTemplateRow(Base):
    __tablename__ = "crm_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)  # cold_email_1, tg_intro, wa_follow_up
    channel = Column(String, nullable=False)             # email / tg / wa
    subject = Column(String, default="")                 # только для email
    body = Column(Text, nullable=False)                  # текст шаблона с плейсхолдерами {name}, {city}
    is_default = Column(Integer, default=0)              # 1 = шаблон по умолчанию для канала
    description = Column(String, default="")             # человекочитаемое описание

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<{self.__class__.__name__}(name={self.name!r}, channel={self.channel})>"
```

#### crm_email_campaigns (email-кампании)

```python
class CrmEmailCampaignRow(Base):
    __tablename__ = "crm_email_campaigns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    template_name = Column(String, nullable=False)  # FK к crm_templates.name
    status = Column(String, default="draft", index=True)  # draft / active / paused / completed
    total_sent = Column(Integer, default=0)
    total_opened = Column(Integer, default=0)
    total_replied = Column(Integer, default=0)
    total_bounced = Column(Integer, default=0)

    filters = Column(Text, default="{}")  # JSON: {"city": "Волгоград", "min_score": 40}

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

#### crm_auto_rules (автоматические правила)

```python
class CrmAutoRuleRow(Base):
    __tablename__ = "crm_auto_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)

    trigger_type = Column(String, nullable=False)   # no_response / schedule / event
    trigger_channel = Column(String, default="")    # email / tg / wa
    trigger_days = Column(Integer, default=0)       # дней ожидания
    funnel_stages = Column(String, default="")      # JSON: ["email_sent"] — applicable stages

    action_type = Column(String, nullable=False)    # create_task / change_stage / send_email
    action_params = Column(Text, default="{}")      # JSON: {"task_type":"follow_up","channel":"tg"}

    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

#### crm_orders (заказы)

```python
class CrmOrderRow(Base):
    __tablename__ = "crm_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    description = Column(Text, default="")
    amount = Column(Integer, default=0)           # сумма в рублях
    status = Column(String, default="pending")    # pending / confirmed / in_progress / completed / cancelled
    source = Column(String, default="")           # откуда пришёл заказ: tg / wa / email / manual

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

### 1.2 Обновить __all__ в database.py

После добавления моделей, обновить экспорт (если есть `__all__` — сейчас его нет, но стоит добавить для чистоты):

```python
__all__ = [
    "Base", "Database",
    "RawCompanyRow", "CompanyRow", "EnrichedCompanyRow",
    "CrmContactRow", "CrmTouchRow", "CrmTaskRow",
    "CrmEmailLogRow", "CrmTemplateRow",
    "CrmEmailCampaignRow", "CrmAutoRuleRow", "CrmOrderRow",
]
```

### Тестирование фазы 1

```bash
# 1. Проверить что Python может импортировать новые модели (синтаксис OK)
python -c "from granite.database import CrmContactRow, CrmTouchRow, CrmTaskRow; print('OK')"

# 2. Существующие тесты не сломались
pytest tests/ -q

# 3. Проверить что Alembic видит diff
python cli.py db check
# Ожидаемый вывод: "Обнаружено N различий между ORM и БД"
# N должно быть = 8 (новые таблицы)
```

**Критерий успеха:**
- [ ] Все 8 моделей импортируются без ошибок
- [ ] `pytest tests/ -q` — все тесты проходят
- [ ] `python cli.py db check` видит 8 новых таблиц

### Коммит

```
feat: add 8 CRM ORM models to database.py

Новые модели: CrmContactRow, CrmTouchRow, CrmTaskRow,
CrmEmailLogRow, CrmTemplateRow, CrmEmailCampaignRow,
CrmAutoRuleRow, CrmOrderRow.

Все FK на companies.id с ON DELETE CASCADE (кроме crm_tasks,
crm_orders — SET NULL). Индексы на funnel_stage, status,
company_id, tracking_id.
```

---

## Фаза 2: Alembic-миграция + SEED (30-45 минут)

> Цель: создать миграцию для 8 новых таблиц и заполнить начальными данными.
> Зависимость: Фаза 1 (коммит).
> Риск: средний — миграция затрагивает production-БД. **Обязательно бэкап перед применением.**

### 2.1 Бэкап БД

```bash
cp data/granite.db data/granite.db.backup.$(date +%Y%m%d_%H%M%S)
```

### 2.2 Создать миграцию

```bash
python cli.py db migrate "add_crm_tables"
```

Это сгенерирует файл в `alembic/versions/`.

### 2.3 Проверить сгенерированную миграцию

**Критически важно:** Alembic autogenerate для SQLite НЕ добавляет `ON DELETE CASCADE`.
Открыть сгенерированный файл и проверить:

1. `crm_contacts.company_id` — PRIMARY KEY + `ondelete='CASCADE'`
2. `crm_touches.company_id` — `ondelete='CASCADE'`
3. `crm_tasks.company_id` — `ondelete='SET NULL'`
4. `crm_email_logs.company_id` — `ondelete='CASCADE'`
5. `crm_email_logs.tracking_id` — `unique=True`
6. `crm_templates.name` — `unique=True`
7. `crm_auto_rules.name` — `unique=True`
8. `crm_orders.company_id` — `ondelete='SET NULL'`

Если CASCADE отсутствует — добавить вручную:

```python
# Пример правки в сгенерированной миграции:
sa.Column('company_id', sa.Integer(),
          sa.ForeignKey('companies.id', ondelete='CASCADE'),
          primary_key=True),
```

### 2.4 Применить миграцию

```bash
python cli.py db upgrade head
```

### 2.5 SEED: заполнить crm_contacts для всех существующих компаний

**Файл:** `scripts/seed_crm_contacts.py` (NEW)

```python
"""SEED: создать crm_contacts для всех компаний без CRM-записи.

Использование:
    python -m scripts.seed_crm_contacts
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from granite.database import Database, CompanyRow, CrmContactRow
from loguru import logger

def seed_crm_contacts():
    db = Database()
    with db.session_scope() as session:
        # Найти компании без crm_contacts
        companies_without_crm = (
            session.query(CompanyRow.id)
            .outerjoin(CrmContactRow, CompanyRow.id == CrmContactRow.company_id)
            .filter(CrmContactRow.company_id.is_(None))
            .all()
        )

        if not companies_without_crm:
            logger.info("Все компании уже имеют crm_contacts — SEED не нужен")
            return

        count = 0
        for (company_id,) in companies_without_crm:
            contact = CrmContactRow(company_id=company_id, funnel_stage="new")
            session.add(contact)
            count += 1

        logger.info(f"SEED: создано {count} crm_contacts записей")

    db.engine.dispose()

if __name__ == "__main__":
    seed_crm_contacts()
```

### 2.6 SEED: заполнить шаблоны и автоправила

**Файл:** `scripts/seed_crm_defaults.py` (NEW)

```python
"""SEED: заполнить crm_templates и crm_auto_rules начальными данными.

Использование:
    python -m scripts.seed_crm_defaults
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from granite.database import Database, CrmTemplateRow, CrmAutoRuleRow
from loguru import logger

TEMPLATES = [
    {
        "name": "cold_email_1",
        "channel": "email",
        "subject": "Предложение по ретуши для {city}",
        "body": "Добрый день!\n\nМы занимаемся ретушью фотографий памятников. "
                "Работаем с мастерскими по всей России — помогаем сделать фото готовых "
                "изделий максимально выразительными.\n\n"
                "Если интересно — покажем примеры наших работ.\n\n"
                "С уважением,\nГранит Студия",
        "is_default": 1,
        "description": "Первое холодное письмо",
    },
    {
        "name": "follow_up_email",
        "channel": "email",
        "subject": "Re: Предложение по ретуши для {city}",
        "body": "Здравствуйте!\n\nПишу повторно — возможно, предыдущее письмо "
                "потерялось. Мы бы хотели предложить сотрудничество по ретуши "
                "фотографий памятников.\n\n"
                "Будем рады ответу.",
        "is_default": 0,
        "description": "Follow-up email (если нет ответа на первое)",
    },
    {
        "name": "final_email",
        "channel": "email",
        "subject": "Re: Предложение по ретуши для {city}",
        "body": "Здравствуйте!\n\nВидимо, сейчас не лучшее время для новых "
                "контактов. Буду на связи — если понадобится помощь с ретушью, "
                "обращайтесь в любой момент.\n\n"
                "Хорошего дня!",
        "is_default": 0,
        "description": "Финальное письмо перед переносом в unreachable",
    },
    {
        "name": "tg_intro",
        "channel": "tg",
        "body": "Добрый день! Увидел вашу мастерскую в {city} — занимаетесь памятниками? "
                "Мы делаем ретушь фото готовых изделий, если интересно — покажу примеры.",
        "is_default": 1,
        "description": "Первое сообщение в Telegram",
    },
    {
        "name": "tg_follow_up",
        "channel": "tg",
        "body": "Здравствуйте! Писал ранее по поводу ретуши памятников. "
                "Возможно, сейчас загружены — буду рад ответу, когда будет время.",
        "is_default": 0,
        "description": "Follow-up в TG (если нет ответа)",
    },
    {
        "name": "wa_intro",
        "channel": "wa",
        "body": "Добрый день! Увидел вашу мастерскую в {city} — делаете памятники? "
                "Мы помогаем с ретушью фото готовых изделий. Если интересно — покажу примеры!",
        "is_default": 1,
        "description": "Первое сообщение в WhatsApp",
    },
    {
        "name": "wa_follow_up",
        "channel": "wa",
        "body": "Здравствуйте! Писал ранее по поводу ретуши. Дублирую сюда — "
                "могли бы обсудить сотрудничество, когда будет удобно.",
        "is_default": 0,
        "description": "Follow-up в WA (если нет ответа)",
    },
    {
        "name": "wa_final",
        "channel": "wa",
        "body": "Здравствуйте! Понимаю, что сейчас может быть не до новых контактов. "
                "Буду на связи — обращайтесь, если понадобится ретушь!",
        "is_default": 0,
        "description": "Финальное сообщение в WA",
    },
]

AUTO_RULES = [
    {
        "name": "follow_up_tg_after_email",
        "trigger_type": "no_response",
        "trigger_channel": "email",
        "trigger_days": 4,
        "funnel_stages": '["email_sent"]',
        "action_type": "create_task",
        "action_params": '{"task_type":"follow_up","channel":"tg","template":"tg_intro"}',
        "enabled": 1,
    },
    {
        "name": "follow_up_wa_after_tg",
        "trigger_type": "no_response",
        "trigger_channel": "tg",
        "trigger_days": 4,
        "funnel_stages": '["follow_up_sent"]',
        "action_type": "create_task",
        "action_params": '{"task_type":"follow_up","channel":"wa","template":"wa_intro"}',
        "enabled": 1,
    },
    {
        "name": "second_email_after_wa",
        "trigger_type": "no_response",
        "trigger_channel": "wa",
        "trigger_days": 5,
        "funnel_stages": '["second_follow_up"]',
        "action_type": "create_task",
        "action_params": '{"task_type":"send_email","template":"final_email"}',
        "enabled": 1,
    },
    {
        "name": "mark_unreachable",
        "trigger_type": "schedule",
        "trigger_channel": "",
        "trigger_days": 21,
        "funnel_stages": '["second_follow_up"]',
        "action_type": "change_stage",
        "action_params": '{"funnel_stage":"unreachable"}',
        "enabled": 1,
    },
    {
        "name": "reopen_warm_email",
        "trigger_type": "no_response",
        "trigger_channel": "email",
        "trigger_days": 7,
        "funnel_stages": '["email_opened"]',
        "action_type": "create_task",
        "action_params": '{"task_type":"follow_up","channel":"email","template":"follow_up_email"}',
        "enabled": 1,
    },
]


def seed_templates(db):
    with db.session_scope() as session:
        existing = {t.name for t in session.query(CrmTemplateRow.name).all()}
        new = [t for t in TEMPLATES if t["name"] not in existing]
        if not new:
            logger.info("Все шаблоны уже есть в БД")
            return
        for t in new:
            session.add(CrmTemplateRow(**t))
        logger.info(f"SEED: добавлено {len(new)} шаблонов")


def seed_auto_rules(db):
    with db.session_scope() as session:
        existing = {r.name for r in session.query(CrmAutoRuleRow.name).all()}
        new = [r for r in AUTO_RULES if r["name"] not in existing]
        if not new:
            logger.info("Все автоправила уже есть в БД")
            return
        for r in new:
            session.add(CrmAutoRuleRow(**r))
        logger.info(f"SEED: добавлено {len(new)} автоправил")


if __name__ == "__main__":
    db = Database()
    seed_templates(db)
    seed_auto_rules(db)
    db.engine.dispose()
    logger.info("SEED завершён")
```

### 2.7 Запустить SEED

```bash
python -m scripts.seed_crm_contacts
python -m scripts.seed_crm_defaults
```

### Тестирование фазы 2

```bash
# 1. Проверить миграцию
python cli.py db current
# Ожидание: revision = head (новая ревизия)

python cli.py db history --verbose
# Ожидание: 3 миграции (initial, drop_pipeline_runs, add_crm_tables)

# 2. Проверить таблицы в БД
python -c "
from granite.database import Database
db = Database()
from sqlalchemy import inspect
tables = inspect(db.engine).get_table_names()
print('Tables:', sorted(tables))
assert 'crm_contacts' in tables
assert 'crm_touches' in tables
assert 'crm_tasks' in tables
assert 'crm_email_logs' in tables
assert 'crm_templates' in tables
assert 'crm_email_campaigns' in tables
assert 'crm_auto_rules' in tables
assert 'crm_orders' in tables
print('All 8 CRM tables exist!')
db.engine.dispose()
"

# 3. Проверить SEED данные
python -c "
from granite.database import Database, CrmContactRow, CrmTemplateRow, CrmAutoRuleRow
db = Database()
with db.session_scope() as s:
    contacts = s.query(CrmContactRow).count()
    templates = s.query(CrmTemplateRow).count()
    rules = s.query(CrmAutoRuleRow).count()
    print(f'crm_contacts: {contacts}')
    print(f'crm_templates: {templates}')
    print(f'crm_auto_rules: {rules}')
    assert contacts > 0, 'No contacts seeded!'
    assert templates == 8, f'Expected 8 templates, got {templates}'
    assert rules == 5, f'Expected 5 rules, got {rules}'
db.engine.dispose()
"

# 4. Проверить FK CASCADE
python -c "
from granite.database import Database, CompanyRow, CrmContactRow, EnrichedCompanyRow
db = Database()
with db.session_scope() as s:
    # Создать тестовую компанию
    c = CompanyRow(name_best='TEST_CASCADE', city='TEST', status='raw')
    s.add(c)
    s.flush()
    cid = c.id
    # Добавить enriched + crm_contact
    e = EnrichedCompanyRow(id=cid, name='TEST', city='TEST')
    s.add(e)
    crm = CrmContactRow(company_id=cid)
    s.add(crm)
    s.commit()

    # Удалить компанию
    s.delete(c)
    s.commit()

    # Проверить CASCADE
    assert s.get(EnrichedCompanyRow, cid) is None, 'CASCADE failed for enriched!'
    assert s.get(CrmContactRow, cid) is None, 'CASCADE failed for crm_contact!'
    print('FK CASCADE works correctly!')
db.engine.dispose()
"

# 5. Все тесты проходят
pytest tests/ -q
```

**Критерий успеха:**
- [ ] `python cli.py db current` показывает head
- [ ] В БД 11 таблиц (3 старых + 8 новых)
- [ ] crm_contacts заполнен для всех companies
- [ ] crm_templates = 8 записей, crm_auto_rules = 5 записей
- [ ] FK CASCADE работает (тест выше)
- [ ] `pytest tests/ -q` — все тесты проходят
- [ ] Бэкап `data/granite.db.backup.*` существует

### Коммит

```
feat: add Alembic migration for 8 CRM tables with SEED

Миграция: add_crm_tables — 8 новых таблиц (crm_contacts, crm_touches,
crm_tasks, crm_email_logs, crm_templates, crm_email_campaigns,
crm_auto_rules, crm_orders). CASCADE проверен вручную.

SEED: seed_crm_contacts.py — создание crm_contacts для всех
существующих companies. seed_crm_defaults.py — 8 шаблонов
и 5 автоправил.
```

---

## Фаза 3: CRM API — FastAPI (2-3 часа)

> Цель: минимальный REST API для работы с CRM-данными из фронтенда.
> Зависимость: Фаза 2 (коммит).
> Риск: средний — новый слой поверх БД, но изолирован.

### 3.1 Зависимости

```bash
source .venv/bin/activate
pip install fastapi uvicorn[standard]
```

Добавить в `requirements.txt`:
```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
```

### 3.2 Структура файлов

```
granite/api/
├── __init__.py       # пустой
├── app.py            # FastAPI app, CORS, lifespan
├── deps.py           # get_db(), get_config()
├── schemas.py        # Pydantic-схемы (запрос/ответ)
├── companies.py      # GET /companies, GET /companies/{id}, PATCH /companies/{id}
├── touches.py        # POST /companies/{id}/touches, GET /companies/{id}/touches
├── tasks.py          # POST /companies/{id}/tasks, GET /tasks, PATCH /tasks/{id}
└── funnel.py         # GET /funnel
```

### 3.3 app.py — FastAPI приложение

```python
"""CRM API — Granite."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from granite.api.deps import get_db, get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ничего не делаем, БД уже существует
    yield
    # Shutdown:.dispose engine
    from granite.api.deps import _engine
    if _engine is not None:
        _engine.dispose()


app = FastAPI(
    title="Granite CRM API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # прод заменить на конкретный домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роутеры
from granite.api import companies, touches, tasks, funnel
app.include_router(companies.router, prefix="/api/v1", tags=["companies"])
app.include_router(touches.router, prefix="/api/v1", tags=["touches"])
app.include_router(tasks.router, prefix="/api/v1", tags=["tasks"])
app.include_router(funnel.router, prefix="/api/v1", tags=["funnel"])


@app.get("/health")
def health():
    return {"status": "ok"}
```

### 3.4 deps.py — зависимости

```python
"""FastAPI dependencies."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import yaml, os

_engine = None
_SessionLocal = None


def _init_engine():
    global _engine, _SessionLocal
    if _engine is not None:
        return
    config_path = os.environ.get("GRANITE_CONFIG", "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    db_path = config.get("database", {}).get("path", "data/granite.db")
    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    _SessionLocal = sessionmaker(bind=_engine)


def get_db():
    _init_engine()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_config() -> dict:
    config_path = os.environ.get("GRANITE_CONFIG", "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
```

### 3.5 schemas.py — Pydantic-схемы

```python
"""Pydantic-схемы для CRM API."""
from pydantic import BaseModel, field_validator
from typing import Literal
from datetime import datetime
import json


# ===== Funnel stages (enum-like) =====
VALID_FUNNEL_STAGES = {
    "new", "email_sent", "email_opened", "follow_up_sent",
    "second_follow_up", "contacted", "portfolio_sent",
    "interested", "test_order", "regular_client",
    "not_interested", "unreachable",
}


# ===== Company Response =====
class CompanyResponse(BaseModel):
    id: int
    name: str
    phones: list[str] = []
    address: str = ""
    website: str | None = None
    emails: list[str] = []
    city: str = ""
    messengers: dict = {}
    # Enriched
    cms: str = ""
    has_marquiz: bool = False
    is_network: bool = False
    crm_score: int = 0
    segment: str = ""
    # CRM
    funnel_stage: str = "new"
    tags: list[str] = []
    notes: str = ""
    color_label: str = ""
    archived: bool = False
    contact_count: int = 0
    last_contact_at: datetime | None = None
    last_contact_channel: str = ""
    order_count: int = 0
    total_revenue: int = 0

    @field_validator("funnel_stage", mode="before")
    def validate_stage(cls, v):
        if v not in VALID_FUNNEL_STAGES:
            raise ValueError(f"Invalid funnel_stage: {v}")
        return v

    @field_validator("tags", mode="before")
    def parse_tags(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v.startswith("[") else []
        return v or []

    model_config = {"from_attributes": True}


# ===== Contact Update =====
class ContactUpdate(BaseModel):
    funnel_stage: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    color_label: str | None = None
    archived: bool | None = None


# ===== Touch =====
class TouchCreate(BaseModel):
    channel: Literal["email", "tg", "wa", "manual"]
    direction: Literal["outgoing", "incoming"] = "outgoing"
    status: str = "sent"
    subject: str = ""
    body: str = ""
    note: str = ""


class TouchResponse(BaseModel):
    id: int
    company_id: int
    channel: str
    direction: str
    status: str
    subject: str
    body: str
    note: str
    response_text: str = ""
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# ===== Task =====
class TaskCreate(BaseModel):
    title: str
    description: str = ""
    due_date: str | None = None
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    task_type: Literal["follow_up", "send_portfolio", "check_response", "remind", "custom"] = "follow_up"


class TaskUpdate(BaseModel):
    status: str | None = None
    title: str | None = None
    description: str | None = None
    due_date: str | None = None
    priority: str | None = None


class TaskResponse(BaseModel):
    id: int
    company_id: int | None = None
    title: str
    description: str = ""
    due_date: str | None = None
    priority: str = "normal"
    status: str = "pending"
    task_type: str = "follow_up"
    created_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


# ===== Paginated list =====
class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    per_page: int
```

### 3.6 companies.py — список компаний + обновление CRM

```python
"""GET /companies, GET /companies/{id}, PATCH /companies/{id}"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Literal

from granite.api.deps import get_db
from granite.api.schemas import CompanyResponse, ContactUpdate, PaginatedResponse
from granite.database import (
    CompanyRow, EnrichedCompanyRow, CrmContactRow,
)

router = APIRouter()

VALID_ORDER_BY = {
    "crm_score", "name", "city", "funnel_stage",
    "contact_count", "last_contact_at", "created_at",
}


@router.get("/companies", response_model=PaginatedResponse)
def list_companies(
    city: str | None = Query(None),
    segment: str | None = Query(None),
    funnel_stage: str | None = Query(None),
    has_telegram: bool | None = Query(None),
    has_website: bool | None = Query(None),
    min_score: int | None = Query(None),
    max_score: int | None = Query(None),
    search: str | None = Query(None),
    archived: bool | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    order_by: str = Query("crm_score"),
    order_dir: Literal["asc", "desc"] = Query("desc"),
    db: Session = Depends(get_db),
):
    if order_by not in VALID_ORDER_BY:
        order_by = "crm_score"

    q = (
        db.query(CompanyRow, EnrichedCompanyRow, CrmContactRow)
        .outerjoin(EnrichedCompanyRow, CompanyRow.id == EnrichedCompanyRow.id)
        .outerjoin(CrmContactRow, CompanyRow.id == CrmContactRow.company_id)
    )

    if city:
        q = q.filter(CompanyRow.city == city)
    if segment:
        q = q.filter(EnrichedCompanyRow.segment == segment)
    if funnel_stage:
        q = q.filter(CrmContactRow.funnel_stage == funnel_stage)
    if has_telegram:
        q = q.filter(EnrichedCompanyRow.messengers.cast(String).like('%"telegram"%'))
    if has_website:
        q = q.filter(CompanyRow.website.isnot(None), CompanyRow.website != "")
    if min_score is not None:
        q = q.filter(EnrichedCompanyRow.crm_score >= min_score)
    if max_score is not None:
        q = q.filter(EnrichedCompanyRow.crm_score <= max_score)
    if archived is not None:
        q = q.filter(CrmContactRow.archived == (1 if archived else 0))
    if search:
        pattern = f"%{search}%"
        q = q.filter(CompanyRow.name_best.ilike(pattern))

    total = q.count()

    # Сортировка
    col_map = {
        "crm_score": EnrichedCompanyRow.crm_score,
        "name": CompanyRow.name_best,
        "city": CompanyRow.city,
        "funnel_stage": CrmContactRow.funnel_stage,
        "contact_count": CrmContactRow.contact_count,
        "last_contact_at": CrmContactRow.last_contact_at,
        "created_at": CrmContactRow.created_at,
    }
    order_col = col_map.get(order_by, EnrichedCompanyRow.crm_score)
    q = q.order_by(order_col.desc() if order_dir == "desc" else order_col.asc())

    offset = (page - 1) * per_page
    rows = q.offset(offset).limit(per_page).all()

    items = []
    for comp, enriched, crm in rows:
        item = {
            "id": comp.id,
            "name": comp.name_best,
            "phones": enriched.phones if enriched and enriched.phones else comp.phones or [],
            "address": comp.address or "",
            "website": comp.website,
            "emails": enriched.emails if enriched and enriched.emails else comp.emails or [],
            "city": comp.city,
            "messengers": enriched.messengers if enriched and enriched.messengers else comp.messengers or {},
            "cms": enriched.cms if enriched else "",
            "has_marquiz": enriched.has_marquiz if enriched else False,
            "is_network": enriched.is_network if enriched else False,
            "crm_score": enriched.crm_score if enriched else 0,
            "segment": enriched.segment if enriched else "",
            "funnel_stage": crm.funnel_stage if crm else "new",
            "tags": crm.tags if crm else "[]",
            "notes": crm.notes if crm else "",
            "color_label": crm.color_label if crm else "",
            "archived": bool(crm.archived) if crm else False,
            "contact_count": crm.contact_count if crm else 0,
            "last_contact_at": crm.last_contact_at if crm else None,
            "last_contact_channel": crm.last_contact_channel if crm else "",
            "order_count": crm.order_count if crm else 0,
            "total_revenue": crm.total_revenue if crm else 0,
        }
        items.append(item)

    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/companies/{company_id}", response_model=CompanyResponse)
def get_company(company_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(CompanyRow, EnrichedCompanyRow, CrmContactRow)
        .outerjoin(EnrichedCompanyRow, CompanyRow.id == EnrichedCompanyRow.id)
        .outerjoin(CrmContactRow, CompanyRow.id == CrmContactRow.company_id)
        .filter(CompanyRow.id == company_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
    comp, enriched, crm = row
    # ... assembly similar to list_companies
    return _assemble_company(comp, enriched, crm)


@router.patch("/companies/{company_id}")
def update_company(
    company_id: int, data: ContactUpdate, db: Session = Depends(get_db)
):
    comp = db.get(CompanyRow, company_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")

    crm = db.get(CrmContactRow, company_id)
    if crm is None:
        # Upsert
        crm = CrmContactRow(company_id=company_id)
        db.add(crm)

    update_data = data.model_dump(exclude_unset=True)
    # Конвертируем tags list → JSON string
    if "tags" in update_data and isinstance(update_data["tags"], list):
        import json
        update_data["tags"] = json.dumps(update_data["tags"], ensure_ascii=False)

    for k, v in update_data.items():
        setattr(crm, k, v)

    db.commit()
    return {"ok": True, "company_id": company_id}
```

### 3.7 touches.py — логирование касаний

```python
"""POST /companies/{id}/touches, GET /companies/{id}/touches"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from granite.api.deps import get_db
from granite.api.schemas import TouchCreate, TouchResponse, PaginatedResponse
from granite.database import CompanyRow, CrmContactRow, CrmTouchRow

router = APIRouter()


@router.post("/companies/{company_id}/touches")
def create_touch(
    company_id: int, data: TouchCreate, db: Session = Depends(get_db)
):
    comp = db.get(CompanyRow, company_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")

    now = datetime.now(timezone.utc)

    # 1. Записать касание
    touch = CrmTouchRow(
        company_id=company_id,
        channel=data.channel,
        direction=data.direction,
        status=data.status,
        subject=data.subject,
        body=data.body,
        note=data.note,
        created_at=now,
    )
    db.add(touch)

    # 2. Обновить crm_contacts
    crm = db.get(CrmContactRow, company_id)
    if crm is None:
        crm = CrmContactRow(company_id=company_id)
        db.add(crm)

    crm.contact_count = (crm.contact_count or 0) + 1
    crm.last_contact_at = now
    crm.last_contact_channel = data.channel
    if crm.first_contact_at is None:
        crm.first_contact_at = now

    if data.channel == "tg":
        crm.tg_sent_count = (crm.tg_sent_count or 0) + 1
        crm.last_tg_at = now
    elif data.channel == "wa":
        crm.wa_sent_count = (crm.wa_sent_count or 0) + 1
        crm.last_wa_at = now
    elif data.channel == "email":
        crm.email_sent_count = (crm.email_sent_count or 0) + 1

    db.commit()
    db.refresh(touch)
    return {"ok": True, "touch_id": touch.id}


@router.get("/companies/{company_id}/touches", response_model=PaginatedResponse)
def list_touches(
    company_id: int,
    page: int = 1,
    per_page: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(CrmTouchRow).filter_by(company_id=company_id)
    total = q.count()
    rows = q.order_by(CrmTouchRow.created_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return {"items": rows, "total": total, "page": page, "per_page": per_page}
```

### 3.8 tasks.py — задачи

```python
"""POST /companies/{id}/tasks, GET /tasks, PATCH /tasks/{id}"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from granite.api.deps import get_db
from granite.api.schemas import TaskCreate, TaskUpdate, TaskResponse, PaginatedResponse
from granite.database import CrmTaskRow

router = APIRouter()


@router.post("/companies/{company_id}/tasks")
def create_task(
    company_id: int, data: TaskCreate, db: Session = Depends(get_db)
):
    task = CrmTaskRow(
        company_id=company_id,
        title=data.title,
        description=data.description,
        due_date=data.due_date,
        priority=data.priority,
        task_type=data.task_type,
        created_at=datetime.now(timezone.utc),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return {"ok": True, "task_id": task.id}


@router.get("/tasks", response_model=PaginatedResponse)
def list_tasks(
    status: str | None = Query(None),
    priority: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(CrmTaskRow)
    if status:
        q = q.filter_by(status=status)
    if priority:
        q = q.filter_by(priority=priority)
    total = q.count()
    rows = q.order_by(CrmTaskRow.created_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return {"items": rows, "total": total, "page": page, "per_page": per_page}


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, data: TaskUpdate, db: Session = Depends(get_db)):
    task = db.get(CrmTaskRow, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    update_data = data.model_dump(exclude_unset=True)
    if "status" in update_data and update_data["status"] == "completed":
        update_data["completed_at"] = datetime.now(timezone.utc)

    for k, v in update_data.items():
        setattr(task, k, v)

    db.commit()
    return {"ok": True, "task_id": task.id}
```

### 3.9 funnel.py — воронка

```python
"""GET /funnel"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from granite.api.deps import get_db
from granite.database import CrmContactRow

router = APIRouter()


@router.get("/funnel")
def get_funnel(db: Session = Depends(get_db)):
    rows = (
        db.query(CrmContactRow.funnel_stage, func.count())
        .group_by(CrmContactRow.funnel_stage)
        .all()
    )
    return {stage: count for stage, count in rows}


@router.get("/funnel/summary")
def get_funnel_summary(db: Session = Depends(get_db)):
    """Сводка по воронке с метриками."""
    from sqlalchemy import case
    total = db.query(func.count(CrmContactRow.company_id)).scalar()
    contacted = db.query(func.count(CrmContactRow.company_id)).filter(
        CrmContactRow.contact_count > 0
    ).scalar()
    with_tg = db.query(func.count(CrmContactRow.company_id)).filter(
        CrmContactRow.tg_sent_count > 0
    ).scalar()
    with_wa = db.query(func.count(CrmContactRow.company_id)).filter(
        CrmContactRow.wa_sent_count > 0
    ).scalar()
    with_email = db.query(func.count(CrmContactRow.company_id)).filter(
        CrmContactRow.email_sent_count > 0
    ).scalar()
    archived = db.query(func.count(CrmContactRow.company_id)).filter(
        CrmContactRow.archived == 1
    ).scalar()

    return {
        "total_contacts": total,
        "contacted": contacted,
        "with_tg": with_tg,
        "with_wa": with_wa,
        "with_email": with_email,
        "archived": archived,
        "not_contacted": total - contacted,
    }
```

### 3.10 Добавить команду запуска в cli.py

```python
@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Хост"),
    port: int = typer.Option(8000, "--port", "-p", help="Порт"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload на изменение файлов"),
):
    """Запустить CRM API сервер."""
    import uvicorn
    os.environ["GRANITE_CONFIG"] = _config_path
    uvicorn.run(
        "granite.api.app:app",
        host=host,
        port=port,
        reload=reload,
    )
```

### Тестирование фазы 3

```bash
# 1. Проверить импорт
python -c "from granite.api.app import app; print('FastAPI app loaded:', app.title)"

# 2. Запустить сервер (в отдельном терминале)
python cli.py serve --reload

# 3. Проверить эндпоинты (curl или httpie)

# Health check
curl http://localhost:8000/health
# Ожидание: {"status":"ok"}

# Swagger UI
# Открыть в браузере: http://localhost:8000/docs

# Список компаний (первые 5)
curl "http://localhost:8000/api/v1/companies?per_page=5"
# Ожидание: {"items": [...], "total": N, "page": 1, "per_page": 5}

# Фильтр по городу
curl "http://localhost:8000/api/v1/companies?city=Волгоград&per_page=3"

# Фильтр по сегменту
curl "http://localhost:8000/api/v1/companies?segment=A&per_page=3"

# Фильтр по воронке
curl "http://localhost:8000/api/v1/companies?funnel_stage=new&per_page=3"

# Фильтр: есть Telegram
curl "http://localhost:8000/api/v1/companies?has_telegram=true&per_page=3"

# Воронка
curl http://localhost:8000/api/v1/funnel
# Ожидание: {"new": N, "email_sent": M, ...}

# Сводка воронки
curl http://localhost:8000/api/v1/funnel/summary

# Обновить CRM-данные компании
curl -X PATCH http://localhost:8000/api/v1/companies/1 \
  -H "Content-Type: application/json" \
  -d '{"funnel_stage": "email_sent", "notes": "Первый контакт", "tags": ["hot"]}'

# Создать касание
curl -X POST http://localhost:8000/api/v1/companies/1/touches \
  -H "Content-Type: application/json" \
  -d '{"channel": "email", "direction": "outgoing", "subject": "Предложение", "body": "..."}'

# Создать задачу
curl -X POST http://localhost:8000/api/v1/companies/1/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Follow-up в TG", "task_type": "follow_up", "priority": "high", "due_date": "2026-04-20"}'

# Список задач
curl "http://localhost:8000/api/v1/tasks?status=pending"

# Завершить задачу
curl -X PATCH http://localhost:8000/api/v1/tasks/1 \
  -H "Content-Type: application/json" \
  -d '{"status": "completed"}'

# 4. Проверить что пайплайн не сломался
pytest tests/ -q
```

**Критерий успеха:**
- [ ] `python cli.py serve` запускается без ошибок
- [ ] `http://localhost:8000/docs` — Swagger UI доступен
- [ ] `GET /api/v1/companies?per_page=5` — возвращает JSON с пагинацией
- [ ] `PATCH /api/v1/companies/1` — обновляет funnel_stage, notes, tags
- [ ] `POST /api/v1/companies/1/touches` — логирует касание, обновляет счётчики
- [ ] `POST /api/v1/companies/1/tasks` — создаёт задачу
- [ ] `GET /api/v1/tasks?status=pending` — список задач
- [ ] `GET /api/v1/funnel` — воронка по стадиям
- [ ] `pytest tests/ -q` — все тесты проходят

### Коммит

```
feat: add CRM API (FastAPI) with companies, touches, tasks, funnel

Новые файлы: granite/api/{app,deps,schemas,companies,touches,tasks,funnel}.py
Эндпоинты: GET/PATCH /companies, POST /touches, POST/GET/PATCH /tasks,
GET /funnel, GET /funnel/summary.
Новая CLI-команда: python cli.py serve [--host] [--port] [--reload].
Зависимости: fastapi, uvicorn.
```

---

## Фаза 4: Интеграционные тесты API (1 час)

> Цель: покрыть API тестами, чтобы при рефакторинге не сломать.
> Зависимость: Фаза 3 (коммит).

### 4.1 Файл: `tests/test_crm_api.py`

```python
"""Интеграционные тесты CRM API."""
import pytest
from fastapi.testclient import TestClient
from granite.api.app import app


@pytest.fixture
def client(tmp_path):
    """TestClient с временной БД."""
    import os, yaml
    # Создаём временную config.yaml с тестовой БД
    db_path = tmp_path / "test.db"
    config = {
        "database": {"path": str(db_path)},
        "cities": [{"name": "Тестбург", "region": "Тест"}],
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    os.environ["GRANITE_CONFIG"] = str(config_path)

    # Инициализировать БД
    from granite.database import Database
    db = Database(config_path=str(config_path))
    from granite.api.deps import _engine, _SessionLocal
    _engine = db.engine
    _SessionLocal = db.SessionLocal

    with TestClient(app) as c:
        yield c

    db.engine.dispose()


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestCompanies:
    def _seed_company(self, db):
        from granite.database import CompanyRow, EnrichedCompanyRow, CrmContactRow
        c = CompanyRow(name_best="Тест Мастерская", city="Тестбург", status="raw")
        db.add(c)
        db.flush()
        e = EnrichedCompanyRow(
            id=c.id, name="Тест Мастерская", city="Тестбург",
            crm_score=50, segment="B",
        )
        db.add(e)
        crm = CrmContactRow(company_id=c.id, funnel_stage="new")
        db.add(crm)
        db.commit()
        return c.id

    def test_list_empty(self, client):
        r = client.get("/api/v1/companies")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_list_with_data(self, client):
        from granite.database import Database
        db = Database()
        cid = self._seed_company(db)
        r = client.get("/api/v1/companies")
        assert r.status_code == 200
        assert r.json()["total"] >= 1
        db.engine.dispose()

    def test_patch_company(self, client):
        from granite.database import Database
        db = Database()
        cid = self._seed_company(db)
        r = client.patch(
            f"/api/v1/companies/{cid}",
            json={"funnel_stage": "email_sent", "notes": "test note"},
        )
        assert r.status_code == 200
        # Проверить что обновилось
        r2 = client.get(f"/api/v1/companies/{cid}")
        data = r2.json()
        assert data["funnel_stage"] == "email_sent"
        assert data["notes"] == "test note"
        db.engine.dispose()

    def test_patch_company_404(self, client):
        r = client.patch("/api/v1/companies/99999", json={"notes": "x"})
        assert r.status_code == 404


class TestTouches:
    def test_create_touch(self, client):
        from granite.database import Database, CompanyRow, EnrichedCompanyRow, CrmContactRow
        db = Database()
        c = CompanyRow(name_best="Touch Test", city="Тестбург", status="raw")
        db.add(c); db.flush()
        db.add(EnrichedCompanyRow(id=c.id, name="Touch Test", city="Тестбург"))
        db.add(CrmContactRow(company_id=c.id))
        db.commit()
        cid = c.id

        r = client.post(
            f"/api/v1/companies/{cid}/touches",
            json={"channel": "email", "direction": "outgoing", "subject": "test"},
        )
        assert r.status_code == 200
        assert "touch_id" in r.json()

        # Проверить что счётчик обновился
        r2 = client.get(f"/api/v1/companies/{cid}")
        assert r2.json()["contact_count"] == 1
        assert r2.json()["email_sent_count"] >= 1
        db.engine.dispose()


class TestTasks:
    def test_create_task(self, client):
        from granite.database import Database, CompanyRow, EnrichedCompanyRow
        db = Database()
        c = CompanyRow(name_best="Task Test", city="Тестбург", status="raw")
        db.add(c); db.flush()
        db.add(EnrichedCompanyRow(id=c.id, name="Task Test", city="Тестбург"))
        db.commit()

        r = client.post(
            f"/api/v1/companies/{c.id}/tasks",
            json={"title": "Follow-up", "task_type": "follow_up", "priority": "high"},
        )
        assert r.status_code == 200
        assert "task_id" in r.json()
        db.engine.dispose()

    def test_list_tasks(self, client):
        r = client.get("/api/v1/tasks")
        assert r.status_code == 200


class TestFunnel:
    def test_funnel(self, client):
        r = client.get("/api/v1/funnel")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_funnel_summary(self, client):
        r = client.get("/api/v1/funnel/summary")
        assert r.status_code == 200
        data = r.json()
        assert "total_contacts" in data
        assert "contacted" in data
```

### Тестирование фазы 4

```bash
# Запустить API-тесты
pytest tests/test_crm_api.py -v

# Все тесты вместе
pytest tests/ -q
```

**Критерий успеха:**
- [ ] `pytest tests/test_crm_api.py -v` — все тесты проходят
- [ ] Покрыты: health, companies (list, patch, 404), touches (create + counters), tasks (create, list), funnel

### Коммит

```
test: add integration tests for CRM API

tests/test_crm_api.py: health, companies CRUD, touches с обновлением
счётчиков, tasks create/list, funnel + funnel/summary.
TestClient с временной SQLite-БД.
```

---

## Фаза 5: CLI-команда seed и CLI serve (30 минут)

> Цель: сделать seed и serve частью CLI, чтобы не запускать скрипты руками.
> Зависимость: Фазы 2 + 3 (коммиты).

### 5.1 Добавить `db seed` команду в cli.py

```python
@db_app.command("seed")
def db_seed(
    what: str = typer.Argument("all", help="Что заполнить: contacts, templates, rules, all")
):
    """Заполнить CRM-таблицы начальными данными."""
    import json
    from granite.database import Database, CompanyRow, CrmContactRow, CrmTemplateRow, CrmAutoRuleRow

    config = load_config()
    db = Database(config_path=_config_path)

    if what in ("all", "contacts"):
        with db.session_scope() as session:
            companies_without = (
                session.query(CompanyRow.id)
                .outerjoin(CrmContactRow, CompanyRow.id == CrmContactRow.company_id)
                .filter(CrmContactRow.company_id.is_(None))
                .count()
            )
            if companies_without == 0:
                print_status("crm_contacts: все компании уже имеют записи", "success")
            else:
                session.execute(
                    text("""INSERT OR IGNORE INTO crm_contacts (company_id, funnel_stage, created_at, updated_at)
                           SELECT id, 'new', datetime('now'), datetime('now')
                           FROM companies
                           WHERE id NOT IN (SELECT company_id FROM crm_contacts)""")
                )
                print_status(f"crm_contacts: создано {companies_without} записей", "success")

    if what in ("all", "templates", "rules"):
        # Импортировать TEMPLATES и AUTO_RULES из scripts.seed_crm_defaults
        # ... (аналогично скрипту)
        print_status(f"CRM SEED ({what}) завершён", "success")

    db.engine.dispose()
```

### Тестирование фазы 5

```bash
python cli.py db seed
python cli.py serve --port 8000
# Открыть http://localhost:8000/docs
```

### Коммит

```
feat: add 'db seed' and 'serve' CLI commands

- python cli.py db seed [contacts|templates|rules|all]
- python cli.py serve [--host] [--port] [--reload]
```

---

## Обзор всех коммитов

| Фаза | Коммит | Время | Что меняется |
|---|---|---|---|
| 0 | `feat: wire name_matcher, remove pool_pre_ping` | 15 мин | 2 файла (dedup_phase.py, database.py) |
| 1 | `feat: add 8 CRM ORM models` | 1-1.5 ч | 1 файл (database.py) |
| 2 | `feat: Alembic migration + SEED` | 30-45 мин | миграция + 2 скрипта |
| 3 | `feat: CRM API (FastAPI)` | 2-3 ч | 8 новых файлов в granite/api/ |
| 4 | `test: CRM API integration tests` | 1 ч | 1 файл (tests/test_crm_api.py) |
| 5 | `feat: db seed + serve CLI commands` | 30 мин | 1 файл (cli.py) |

**Итого:** ~5-7 часов работы. После Фазы 3 у тебя уже работающий CRM API, который можно подключать к фронтенду.

---

## Что делать после (не часть этого плана)

| Задача | Приоритет | Почему потом |
|---|---|---|
| Фронтенд (таблица лидов + воронка) | P0 | API готов — нужен UI |
| Рефакторинг enrichment_phase.py (799→150 строк) | P2 | Работает, не ломается |
| Pydantic ConfigSchema | P3 | Текущий валидатор справляется |
| Email-отправка (smtplib / Resend) | P1 | Следующий шаг после UI |
| WhatsApp-воркер (Node.js) | P1 | Из posting.md |
| Telegram UserBot (Pyrogram) | P1 | Из posting.md |
| Tracking pixel (email open detection) | P1 | Из posting.md |
| Cron для crm_auto_rules | P2 | Автоматизация воронки |

---

## Чек-лист готовности к CRM

После завершения всех 5 фаз:

- [ ] 11 таблиц в БД (3 pipeline + 8 CRM)
- [ ] crm_contacts заполнен для всех companies
- [ ] 8 шаблонов + 5 автоправил в БД
- [ ] `python cli.py serve` — API работает
- [ ] `GET /api/v1/companies` — список с фильтрами и пагинацией
- [ ] `PATCH /api/v1/companies/{id}` — обновление CRM-полей
- [ ] `POST /api/v1/companies/{id}/touches` — логирование касаний
- [ ] `POST /api/v1/companies/{id}/tasks` — создание задач
- [ ] `GET /api/v1/funnel` — воронка
- [ ] Swagger UI на `/docs`
- [ ] Все тесты проходят
- [ ] Бэкап БД создан перед миграцией
