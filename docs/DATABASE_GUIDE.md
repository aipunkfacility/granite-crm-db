# Гайд по базе данных проекта

## 1. Общая архитектура

Проект использует **SQLite** как хранилище — файл `data/granite.db`. Доступ к БД осуществляется через **SQLAlchemy ORM** (декларативные модели в `granite/database.py`). Схема управляется через **Alembic** — систему миграций, позволяющую менять структуру таблиц без потери данных.

Связь между компонентами:

```
config.yaml                 granite/database.py              alembic/
┌──────────────┐         ┌─────────────────────┐         ┌─────────────────┐
│ database:    │────────▶│ Database()          │────────▶│ env.py          │
│   path: ...  │         │  ├─ engine (SQLite)  │         │  ├─ get_url()   │
│              │         │  ├─ WAL PRAGMAs      │         │  ├─ online()    │
│              │         │  ├─ alembic upgrade  │         │  └─ offline()   │
│              │         │  └─ SessionLocal     │         │                 │
└──────────────┘         └─────────────────────┘         │ versions/       │
                                                         │  ├─ 0001_...    │
                     granite/models.py                   │  └─ ...         │
                     ┌─────────────────────┐             └─────────────────┘
                     │ RawCompany (Pydantic)│
                     │ Company (Pydantic)   │
                     │ EnrichedCompany      │
                     └─────────────────────┘
```

## 2. Таблицы и схема

### 2.1 raw_companies — сырые данные скреперов

Каждый скрепер сохраняет результаты в эту таблицу без изменений. Один и тот же реальный объект может иметь несколько записей (от разных источников).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | INTEGER PK | Автоинкремент |
| `source` | VARCHAR, NOT NULL | Источник: `jsprav`, `web_search`, `2gis`, `yell` |
| `source_url` | VARCHAR | URL страницы-источника |
| `name` | VARCHAR, NOT NULL | Название компании (как на сайте) |
| `phones` | JSON | Список телефонов `["79001234567", ...]` |
| `address_raw` | TEXT | Адрес одной строкой |
| `website` | VARCHAR | URL сайта |
| `emails` | JSON | Список email `["info@firm.ru", ...]` |
| `geo` | VARCHAR | Координаты `"lat,lon"` |
| `messengers` | JSON | Мессенджеры `{"telegram": "...", "vk": "..."}` |
| `scraped_at` | DATETIME | Время парсинга (UTC) |
| `city` | VARCHAR, NOT NULL | Город из config.yaml (индекс) |
| `merged_into` | INTEGER FK | ⚠️ DEPRECATED — не используется. Слияние записей через `merged_from` в companies |

Индексы: `ix_raw_companies_city`, `ix_raw_companies_source`.

### 2.2 companies — после дедупликации

Уникальные компании, полученные слиянием дублей из `raw_companies`. Алгоритм кластеризации — Union-Find по общим телефонам и доменам сайтов.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | INTEGER PK | Автоинкремент |
| `merged_from` | JSON | Список ID из `raw_companies` `[1, 5, 12]` |
| `name_best` | VARCHAR, NOT NULL | Лучшее название (самое длинное) |
| `phones` | JSON | Объединённые уникальные телефоны |
| `address` | TEXT | Лучший адрес |
| `website` | VARCHAR | Нормализованный URL сайта |
| `emails` | JSON | Объединённые уникальные email |
| `city` | VARCHAR, NOT NULL | Город (индекс) |
| `messengers` | JSON | Мессенджеры из сырых данных |
| `status` | VARCHAR | `raw` → `validated` → `enriched` → `contacted` (индекс) |
| `segment` | VARCHAR | `A` / `B` / `C` / `D` / `Не определено` |
| `needs_review` | BOOLEAN | Флаг конфликта при слиянии |
| `review_reason` | VARCHAR | Причина: `same_name_diff_address` и т.п. |
| `created_at` | DATETIME | Время создания записи (UTC) |
| `updated_at` | DATETIME | Время последнего обновления (UTC) |

Индексы: `ix_companies_city`, `ix_companies_status`.

### 2.3 enriched_companies — обогащённые данные

Связь **1:1** с `companies` по `id` (Primary Key + Foreign Key). Содержит результаты обогащения: мессенджеры, анализ Telegram, CMS, скоринг. При удалении компании из `companies` запись автоматически удаляется (`ON DELETE CASCADE`).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | INTEGER PK, FK | → `companies.id` (CASCADE) |
| `name` | VARCHAR | Копия `name_best` |
| `phones` | JSON | Телефоны (могут быть дополнены) |
| `address_raw` | TEXT | Копия адреса |
| `website` | VARCHAR | Сайт (может быть найден через web_search) |
| `emails` | JSON | Email (могут быть дополнены) |
| `city` | VARCHAR, NOT NULL | Город (индекс) |
| `messengers` | JSON | Итоговые мессенджеры `{"telegram": "t.me/...", "whatsapp": "..."}` |
| `tg_trust` | JSON | Анализ TG: `{"trust_score": 3, "has_avatar": true, "has_description": true}` |
| `cms` | VARCHAR | CMS сайта: `bitrix`, `wordpress`, `tilda`, `unknown` |
| `has_marquiz` | BOOLEAN | Наличие виджета Marquiz на сайте |
| `is_network` | BOOLEAN | Является частью филиальной сети |
| `crm_score` | INTEGER | Итоговый скор (0–100+) (индекс) |
| `segment` | VARCHAR | `A` / `B` / `C` / `D` (индекс) |
| `updated_at` | DATETIME | Время обновления (UTC, auto on update) |

Индексы: `ix_enriched_companies_city`, `ix_enriched_companies_crm_score`, `ix_enriched_companies_segment`.

### 2.4 alembic_version — служебная таблица

Создаётся и управляется Alembic автоматически. Хранит текущую ревизию схемы:

| Колонка | Тип | Описание |
|---------|-----|----------|
| `version_num` | VARCHAR | ID текущей миграции (например, `ecda7d78a38f`) |

### 2.5 Диаграмма связей (ER)

```
┌─────────────────┐       ┌──────────────────────┐
│  raw_companies  │       │     companies        │
├─────────────────┤       ├──────────────────────┤
│ id (PK)         │──┐    │ id (PK)              │
│ source          │  │    │ merged_from (JSON)   │
│ source_url      │  │    │ name_best            │
│ name            │  └──▶│ phones (JSON)        │
│ phones (JSON)   │       │ address              │
│ address_raw     │       │ website              │
│ website         │       │ emails (JSON)        │
│ emails (JSON)   │       │ city                 │
│ geo             │       │ messengers (JSON)    │
│ messengers      │       │ status               │
│ scraped_at      │       │ segment              │
│ city            │       │ needs_review         │
│                 │       │ review_reason        │
└─────────────────┘       │ created_at           │
                           │ updated_at           │
                           └──────────┬───────────┘
                                      │ 1:1 (PK = FK)
                           ┌──────────▼───────────┐
                           │ enriched_companies   │
                           ├──────────────────────┤
                           │ id (PK, FK→companies)│
                           │ name                 │
                           │ phones, emails       │
                           │ messengers (JSON)    │
                           │ tg_trust (JSON)      │
                           │ cms                  │
                           │ has_marquiz          │
                           │ is_network           │
                           │ crm_score            │
                           │ segment              │
                           │ updated_at           │
                           └─────────────────────┘
```

## 3. SQLite: WAL-режим и оптимизации

БД работает в **WAL (Write-Ahead Logging)** режиме. Это позволяет одновременно читать и писать без блокировок — критично для параллельного парсинга через `ThreadPoolExecutor`.

PRAGMA, устанавливаемые при каждом подключении:

| PRAGMA | Значение | Зачем |
|--------|----------|-------|
| `journal_mode=WAL` | Позволяет параллельные чтения во время записи | Без "database is locked" при ThreadPoolExecutor |
| `foreign_keys=ON` | Включает проверку внешних ключей | CASCADE при удалении компании |
| `busy_timeout=5000` | 5 сек ожидания блокировки | Если другой поток пишет — ждать, а не падать |

Настройки заданы в двух местах для полноты:
- `granite/database.py` — событие `@event.listens_for(engine, "connect")` для класса `Database`
- `alembic/env.py` — для миграций

## 4. Система миграций Alembic

### 4.1 Как это работает

Alembic отслеживает текущую версию схемы в таблице `alembic_version`. При каждом изменении ORM-моделей создаётся файл миграции с функциями `upgrade()` и `downgrade()`. Команда `upgrade head` применяет все незаписанные миграции по порядку.

```
Версия схемы:
  base ──▶ ecda7d78a38f (initial_schema) ──▶ ... будущие миграции ...
              │
              ▼
     alembic_version.version_num = "ecda7d78a38f"
```

### 4.2 Источники URL БД

`alembic/env.py` определяет URL базы данных по приоритету:

1. **`sqlalchemy.url` из Alembic config** — когда URL установлен программно (`set_main_option`), например в CLI-командах или тестах
2. **`DATABASE_URL` из окружения** — для CI/Docker (только валидные SQLAlchemy URL: `sqlite://`, `postgresql://`, ...)
3. **`config.yaml` → `database.path`** — для локальной разработки (по умолчанию `data/granite.db`)
4. **Фоллбэк** — `sqlite:///data/granite.db`

### 4.3 Автоматические миграции при запуске

Класс `Database()` автоматически применяет `alembic upgrade head` при инициализации (параметр `auto_migrate=True` по умолчанию). Если Alembic не настроен — фоллбэк на `Base.metadata.create_all()`.

```python
# Стандартное использование — миграции применяются автоматически
db = Database()                  # auto_migrate=True
db = Database(auto_migrate=True) # то же самое

# Без миграций — только create_all (для быстрых скриптов/тестов)
db = Database(auto_migrate=False)
```

### 4.4 Имена файлов миграций

Шаблон из `alembic.ini`:
```
%(year)d%(month).2d%(day).2d_%(hour).2d%(minute).2d%(second).2d_%(rev)s_%(slug)s
```

Пример: `20260406_191015_ecda7d78a38f_initial_schema.py`

## 5. Типовые операции

### 5.1 Добавление новой колонки

Пример: нужно добавить колонку `last_contacted_at` в `companies`.

**Шаг 1.** Изменить ORM-модель в `granite/database.py`:

```python
class CompanyRow(Base):
    # ... существующие колонки ...
    last_contacted_at = Column(DateTime, nullable=True)  # НОВАЯ
```

**Шаг 2.** Сгенерировать миграцию:

```bash
python cli.py db migrate "add last_contacted_at to companies"
```

Alembic создаст файл в `alembic/versions/` с `op.add_column("companies", ...)`.

**Шаг 3.** Проверить сгенерированный файл:

```bash
python cli.py db check
```

**Шаг 4.** Применить:

```bash
python cli.py db upgrade head
```

Все существующие данные сохранятся. Новая колонка будет `NULL` для старых записей.

### 5.2 Добавление новой таблицы

Пример: нужна таблица `contacts_log` для истории контактов.

**Шаг 1.** Создать ORM-модель в `granite/database.py`:

```python
class ContactLogRow(Base):
    __tablename__ = "contacts_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String, nullable=False)  # "telegram", "phone", "email"
    status = Column(String, default="pending") # "pending", "responded", "rejected"
    contacted_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc))
    notes = Column(Text, default="")
```

**Шаг 2.** Сгенерировать и применить миграцию:

```bash
python cli.py db migrate "add contacts_log table"
python cli.py db upgrade head
```

### 5.3 Добавление индекса

```python
# В ORM-модели:
name = Column(String, nullable=False, index=True)  # автоматически создаст ix_companies_name

# Или через миграцию вручную:
op.create_index("ix_raw_companies_source", "raw_companies", ["source"])
```

### 5.4 Откат изменения

```bash
# На одну миграцию назад
python cli.py db downgrade -1

# До конкретной ревизии
python cli.py db downgrade ecda7d78a38f

# Полный откат (удаление всех таблиц, кроме alembic_version)
python cli.py db downgrade base
```

### 5.5 Удаление колонки

**Шаг 1.** Убрать из ORM-модели:

```python
class CompanyRow(Base):
    # review_reason удалена
    ...
```

**Шаг 2.** Сгенерировать миграцию (Alembic увидит, что колонки больше нет в модели):

```bash
python cli.py db migrate "remove review_reason from companies"
```

**Шаг 3.** Проверить и применить:

```bash
# Проверить, что detected правильно
python cli.py db check

# Применить (колонка и её данные будут удалены!)
python cli.py db upgrade head
```

## 6. CLI-команды для управления БД

Все команды доступны через `python cli.py db ...`:

| Команда | Описание | Пример |
|---------|----------|--------|
| `db upgrade` | Применить миграции | `python cli.py db upgrade head` |
| `db downgrade` | Откатить миграции | `python cli.py db downgrade -1` |
| `db history` | История миграций | `python cli.py db history -v` |
| `db current` | Текущая версия | `python cli.py db current` |
| `db migrate` | Создать миграцию | `python cli.py db migrate "add column"` |
| `db stamp` | Пометить версию | `python cli.py db stamp head` |
| `db check` | Проверить различия ORM ↔ БД | `python cli.py db check` |

### Примеры использования

```bash
# Посмотреть текущую версию
python cli.py db current

# История всех миграций (подробно)
python cli.py db history --verbose

# Проверить, нужны ли миграции
python cli.py db check

# Создать миграцию для изменений в моделях
python cli.py db migrate "add yandex_maps_rating to enriched_companies"

# Применить
python cli.py db upgrade head

# Что-то пошло не так — откатить
python cli.py db downgrade -1

# Несколько шагов назад
python cli.py db downgrade -3
```

## 7. Перенос существующей БД на Alembic

Если у вас есть БД, созданная до внедрения Alembic (без таблицы `alembic_version`):

```bash
# 1. Проверить, что ORM и БД совпадают
python cli.py db check

# 2. Пометить текущую схему как head (без выполнения SQL)
python cli.py db stamp head

# 3. Убедиться, что версия установлена
python cli.py db current
# → Rev: ecda7d78a38f (head)
```

После этого все последующие миграции будут применяться инкрементально.

## 8. Поток данных через таблицы

```
Скреперы (jsprav, web_search, dgis, yell)
        │
        ▼
┌─────────────────┐     ┌───────────────────┐
│ raw_companies   │────▶│ companies         │  Фаза 2: Дедупликация
│ (сырые данные)  │     │ (уникальные)      │  Union-Find по телефонам/сайтам
└─────────────────┘     └─────────┬─────────┘
                                  │
                                  ▼
                        ┌───────────────────────┐
                        │ enriched_companies     │  Фаза 3: Обогащение
                        │ (мессенджеры, CMS,    │  Сканирование сайтов,
                        │  скоринг, сегмент)     │  поиск TG, Web Search
                        └───────────┬───────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │ Reverse Lookup (Фаза 6)│  Поиск в 2GIS и Yell
                        │ Дополнение contacts    │  для компаний с малым
                        │ (телефоны, email, TG)  │  количеством данных
                        └───────────┬───────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │ data/export/           │  Экспорт
                        │ {city}_enriched.csv    │  CSV + Markdown + пресеты
                        └───────────────────────┘
```

Ключевые моменты потока:

1. **raw_companies → companies**: кластеризация по телефонам/сайтам (Union-Find). После слияния ID исходных строк сохраняются в поле `merged_from` (JSON-массив).
2. **companies → enriched_companies**: связь 1:1 по `id` (PK = FK). При обновлении обогащения используется `session.merge()` — это позволяет перезаписывать данные без дублирования.
3. **ON DELETE CASCADE**: при удалении компании из `companies` автоматически удаляется соответствующая запись из `enriched_companies` (благодаря `ondelete="CASCADE"` на FK).

## 9. Работа с БД в коде

### 9.1 Создание подключения

```python
from granite.database import Database

# Стандартный способ — читает путь из config.yaml
db = Database()

# Явный путь
db = Database(db_path="data/granite.db")

# С другим config
db = Database(config_path="config.prod.yaml")

# Без авто-миграций (для тестов/скриптов)
db = Database(auto_migrate=False)
```

### 9.2 Чтение данных

```python
from granite.database import Database, EnrichedCompanyRow

session = db.get_session()

# Все обогащённые компании города
companies = session.query(EnrichedCompanyRow).filter_by(city="Волгоград").all()

# Сегмент A с Telegram
hot_leads = session.query(EnrichedCompanyRow).filter(
    EnrichedCompanyRow.city == "Волгоград",
    EnrichedCompanyRow.segment == "A",
    EnrichedCompanyRow.messengers["telegram"].isnot(None)
).all()

# Сортировка по скору
top = session.query(EnrichedCompanyRow)\
    .filter_by(city="Волгоград")\
    .order_by(EnrichedCompanyRow.crm_score.desc())\
    .limit(20)\
    .all()

session.close()
```

### 9.3 Запись данных

```python
from granite.database import RawCompanyRow, EnrichedCompanyRow

session = db.get_session()
try:
    # Новая сырая запись
    raw = RawCompanyRow(
        source="web_search",
        name="ГранитМастер",
        phones=["79001234567"],
        city="Волгоград",
    )
    session.add(raw)

    # Обновление обогащённых данных (merge = insert or update)
    enriched = EnrichedCompanyRow(
        id=company_id,  # должен существовать в companies
        name="ГранитМастер",
        messengers={"telegram": "t.me/granitmaster"},
        crm_score=45,
        segment="B",
    )
    session.merge(enriched)  # Если id существует — обновит, иначе — создаст

    session.commit()
except Exception as e:
    session.rollback()
    raise
finally:
    session.close()
```

Рекомендуется использовать `session_scope()` вместо ручного управления сессией:

```python
from granite.database import Database, RawCompanyRow, EnrichedCompanyRow

db = Database()
with db.session_scope() as session:
    # Новая сырая запись
    raw = RawCompanyRow(
        source="web_search",
        name="ГранитМастер",
        phones=["79001234567"],
        city="Волгоград",
    )
    session.add(raw)

    # Обновление обогащённых данных (merge = insert or update)
    enriched = EnrichedCompanyRow(
        id=company_id,  # должен существовать в companies
        name="ГранитМастер",
        messengers={"telegram": "t.me/granitmaster"},
        crm_score=45,
        segment="B",
    )
    session.merge(enriched)
# commit() вызывается автоматически при выходе из with
# при исключении — автоматически rollback()
```

## 10. Бэкап и восстановление

### Полный бэкап

```bash
# Копирование файла БД (WAL-режим — можно копировать без остановки)
cp data/granite.db data/backups/granite_20260406.db
cp data/granite.db-wal data/backups/granite_20260406.db-wal  # если есть
cp data/granite.db-shm data/backups/granite_20260406.db-shm  # если есть
```

### Восстановление

```bash
# Заменить файл БД
cp data/backups/granite_20260406.db data/granite.db

# Проверить, что версия схемы совпадает
python cli.py db current

# Если версии не совпадают — применить недостающие миграции
python cli.py db upgrade head
```

### Экспорт данных в SQL

```bash
# Через sqlite3 CLI
sqlite3 data/granite.db .dump > data/backups/granite_dump.sql

# Восстановление из дампа
sqlite3 data/granite.db < data/backups/granite_dump.sql
```

## 11. JSON-колонки

Несколько колонок хранят данные в формате JSON (SQLite 3.38+ поддерживает нативный JSON). Работа с ними через SQLAlchemy:

```python
# Запись
company.phones = ["79001234567", "79160000000"]
company.messengers = {"telegram": "t.me/firm", "vk": "vk.com/firm"}

# Чтение
phones = company.phones or []         # list[str]
messengers = company.messengers or {} # dict[str, str]

# Фильтрация (SQLite JSON1)
from sqlalchemy import func
# Компании, у которых есть telegram в messengers
result = session.query(EnrichedCompanyRow).filter(
    func.json_extract(EnrichedCompanyRow.messengers, '$.telegram').isnot(None)
).all()
```

Колонки с JSON-данными: `phones`, `emails`, `messengers`, `merged_from`, `tg_trust`.

## 12. Тестирование

Все тесты миграций находятся в `tests/test_migrations.py` (9 тестов). Они используют временные БД и проверяют:

- Создание всех таблиц при `upgrade head`
- Полное удаление при `downgrade base`
- Идемпотентность: `upgrade → downgrade → upgrade` даёт ту же схему
- Корректную запись версии в `alembic_version`
- Автоматическую миграцию через `Database()`
- Фоллбэк на `create_all()` при `auto_migrate=False`
- Отсутствие различий между ORM и БД после `upgrade`
- Наличие внешних ключей с правильными `referred_table`

Запуск:

```bash
# Все тесты
python -m pytest tests/ -v

# Только тесты миграций
python -m pytest tests/test_migrations.py -v
```
