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
