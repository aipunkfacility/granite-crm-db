# AGENTS.md — Granite CRM

Стандарты разработки для всех AI-агентов, работающих над проектом.
Читается: Antigravity (v1.20.3+), Cursor, Claude Code, Codex.
Antigravity-специфичные переопределения — в `.agents/rules.md` и `docs/GEMINI.md`.

---

## 📌 Контекст проекта

**Что это:** Python-пайплайн сбора базы ритуальных мастерских России + FastAPI CRM бэкенд.
**Стек:** Python 3.12, SQLAlchemy 2.x, Alembic, FastAPI, SQLite (WAL), asyncio/httpx.
**Package manager:** `uv` — единственный инструмент управления зависимостями.
**Смежный проект:** Granite Web UI — Next.js, TypeScript, shadcn/ui, TanStack Query v5.

**Ключевые файлы:**
- `granite/database.py` — ORM-модели и класс Database
- `granite/pipeline/` — фазы пайплайна (scraping → dedup → enrichment → scoring → export)
- `granite/scrapers/` — парсеры источников (jsprav, dgis, web_search)
- `granite/enrichers/` — обогащение (TG, мессенджеры, CMS)
- `granite/api/` — FastAPI CRM endpoints
- `config.yaml` — конфигурация городов, источников, скоринга
- `data/granite.db` — SQLite база, ~6000 компаний, 29 городов
- `pyproject.toml` — зависимости проекта (управляется через `uv`)

---

## 🐍 Python — правила кодирования

### Зависимости
- **Добавить пакет:** `uv add <package>`
- **Удалить пакет:** `uv remove <package>`
- **Синхронизировать env:** `uv sync`
- **Запустить скрипт:** `uv run <script.py>`
- **Запустить CLI:** `uv run cli.py <command>`
- **Никогда:** `pip install`, `pip-tools`, `poetry`

### БД и сессии
- **Всегда** использовать `session_scope()` контекстный менеджер вместо `get_session()`:
  ```python
  with db.session_scope() as session:
      ...
  # commit() и close() вызываются автоматически
  ```
- **Никогда** не вызывать `session.commit()` внутри `session_scope()` — он делает это сам при выходе.
- **Никогда** не передавать SQLAlchemy `session` между потоками ThreadPoolExecutor.
- `session.flush()` допустим внутри `session_scope()` для промежуточной записи.

### HTTP и безопасность
- Все URL через `is_safe_url(url)` перед запросом — обязательно (SSRF protection).
- Логировать URL через `_sanitize_url_for_log(url)`, не сырой URL — в логах может быть PII.
- Таймаут для одиночных запросов: 15с. Для batch-scraping: 8с.
- `fetch_page()` уже имеет retry через tenacity — не оборачивать дополнительно.

### Async vs Sync
- Если `config.enrichment.async_enabled: true` → использовать `http_client.py` (httpx.AsyncClient).
- Иначе → ThreadPoolExecutor в `_enrich_companies_parallel()`.
- Не смешивать sync и async без `run_async()` из `http_client.py`.
- В async-контексте: `async_fetch_page()`, `async_adaptive_delay()` — не `fetch_page()`.

### Стиль
- Type hints на всех функциях — обязательно.
- Docstrings на публичных методах (кратко: что делает, что возвращает, что кидает).
- Максимальная длина строки: 100 символов.
- Импорты в порядке: stdlib → third-party → local (`granite.*`).

---

## 🗄️ Изменения схемы БД

**Всегда через Alembic — никогда напрямую:**

```bash
# 1. Изменить ORM-модель в granite/database.py
# 2. Проверить что Alembic видит изменения:
uv run cli.py db check
# 3. Создать миграцию:
uv run cli.py db migrate "краткое описание на английском"
# 4. Проверить сгенерированный файл в alembic/versions/ перед применением
# 5. Применить:
uv run cli.py db upgrade head
```

**Никогда:**
- `Base.metadata.create_all()` в production коде (только для тестов с `auto_migrate=False`)
- Прямые `ALTER TABLE` / `CREATE TABLE` в SQLite
- Удалять файлы из `alembic/versions/`

---

## 🧪 Тестирование

```bash
uv run pytest tests/ -v                       # все тесты
uv run pytest tests/test_enrichers.py -v      # enrichers
uv run pytest tests/test_pipeline.py -v       # pipeline
uv run pytest tests/test_migrations.py -v     # миграции
uv run pytest -k "async" -v                   # только async тесты
```

- Перед PR: все тесты должны проходить.
- Моки HTTP через `unittest.mock.patch` — не реальные запросы в тестах.
- Тесты с БД: использовать `tmp_path` fixture и `Database(auto_migrate=False)`.

---

## 🚀 Пайплайн — как запускать

```bash
# Полный цикл для города
uv run cli.py run "Ярославль"

# С очисткой старых данных
uv run cli.py run "Ярославль" --force

# Только обогащение (scrape+dedup уже есть)
uv run cli.py run "Ярославль" --re-enrich

# Все города из config.yaml
uv run cli.py run all
```

**Чекпоинты:** пайплайн запоминает прогресс. Прерванный запуск продолжится с нужного этапа.

---

## ⚠️ Частые ошибки — не делать

1. **Не читать `r.phones`/`r.emails` в потоках без eager loading** — SQLAlchemy lazy load не thread-safe.
2. **Не хардкодить таймаут 15с везде** — для detail-страниц jsprav достаточно 8с, иначе зависание.
3. **Не писать голые `except Exception:`** — минимум логировать категорию ошибки через `_classify_error()`.
4. **Не менять `config.yaml` во время работы пайплайна** — читается один раз при старте.
5. **Не запускать `DROP TABLE` через SQLite MCP** — использовать `uv run cli.py run "Город" --force`.
6. **Не использовать `pip install`** — только `uv add`.

---

## 🌐 FastAPI (CRM API)

- Все изменения данных — через `session_scope()`, не через raw SQL.
- Новые endpoints регистрировать в `granite/api/app.py` через `app.include_router()`.
- Pydantic схемы для request/response — в `granite/api/schemas.py`.
- Зависимость `get_db` из `granite/api/deps.py` — auto-commit при выходе, rollback при ошибке.

---

## 📁 Структура — куда что класть

```
granite/
├── scrapers/      # Новый скрепер → наследовать BaseScraper, реализовать scrape()
├── enrichers/     # Новый enricher → добавить в __init__.py
├── pipeline/      # Новая фаза → отдельный файл *_phase.py, добавить в manager.py
├── api/           # Новый endpoint → отдельный файл, router, register в app.py
└── dedup/         # Дедупликация — трогать осторожно, Union-Find алгоритм
```

---

## 🔒 Безопасность

- `is_safe_url()` — проверять ВСЕ внешние URL перед HTTP-запросом.
- SQL в FastAPI: никаких f-string в запросах. Если `ilike` — экранировать `%` и `_`:
  ```python
  def _escape_like(s: str) -> str:
      return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
  q.filter(CompanyRow.name_best.ilike(f"%{_escape_like(search)}%", escape="\\"))
  ```
- Секреты (API ключи) — только через `.env` / переменные окружения. Не в `config.yaml`.

---

*Проект: Granite CRM · Последнее обновление: 2026-04-13*
