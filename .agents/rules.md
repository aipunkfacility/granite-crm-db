# 🏛️ Granite CRM — Правила агента

> Этот файл загружается Antigravity автоматически при каждой сессии.
> Приоритет: этот файл > GEMINI.md > AGENTS.md > AGENT_CAPABILITIES.md

---

## ⚡ Техстек

- **Python:** 3.12 (`.venv` в корне проекта)
- **Package Manager:** ТОЛЬКО `uv`. Никогда не использовать `pip` напрямую.
- **DB:** SQLite `data/granite.db` (WAL mode), SQLAlchemy 2.0, Alembic.
- **CLI:** `uv run cli.py` или `.bat`-файлы из корня проекта.
- **OS:** Windows / PowerShell. Пути: `f:\Dev\...`

---

## 🤖 Алгоритм работы

1. **Планирование:** Перед любой задачей сложнее «прочитай файл» — используй `sequentialthinking` MCP.
2. **Skills:** Задачи по аудиту БД, мониторингу пайплайна, отладке скреперов — читай соответствующий `SKILL.md` в `.agents/skills/`.
3. **Документация библиотек:** Всегда используй `context7` MCP для SQLAlchemy 2.x, FastAPI, Alembic, Playwright.
4. **Пайплайн:** Перед изменениями — читай `data/logs/granite.log` (последние 200 строк).

---

## 📌 Золотые правила кода

```python
# ВСЕГДА — session_scope
with db.session_scope() as session:
    ...  # commit() вызывается автоматически при выходе

# НИКОГДА внутри session_scope
session.commit()  # ❌

# ВСЕГДА — проверка URL
if is_safe_url(url):
    fetch_page(url)

# ВСЕГДА — type hints + docstrings на публичных методах
def scrape(self, city: str) -> list[CompanyData]:
    """Scrape companies for given city. Returns list of raw company records."""
```

---

## 🔐 Матрица разрешений

| Действие | Статус |
|----------|--------|
| `uv run pytest` | ✅ Всегда |
| Чтение логов, конфигов, БД | ✅ Всегда |
| `uv run cli.py run [City] --force` | ✅ Разрешено |
| `uv add <package>` | ⚠️ Спросить |
| `git commit` / `git push` | ⚠️ Спросить |
| `uv run cli.py db migrate` | ⚠️ Спросить |
| `uv run cli.py db upgrade head` | ⚠️ Спросить |
| Изменение `config.yaml` | ⚠️ Спросить |
| `DELETE FROM` в SQLite MCP | ⚠️ Спросить |
| `DROP TABLE` / `TRUNCATE` | ❌ Запрещено |
| Удаление файлов из `alembic/versions/` | ❌ Запрещено |
| Прямая запись в `data/granite.db` через MCP | ❌ Запрещено |

---

## 🗂️ Навигация по проекту

```
granite/
├── scrapers/      # Парсеры: BaseScraper → scrape()
├── enrichers/     # Обогащение: Telegram, CMS, мессенджеры
├── pipeline/      # Фазы: *_phase.py → manager.py
├── api/           # FastAPI endpoints → app.py
└── dedup/         # Union-Find дедупликация (трогать осторожно)

data/
├── granite.db     # Основная БД (~6000 компаний, 29 городов)
└── logs/granite.log  # Логи пайплайна

.agents/
└── skills/        # Workspace Skills — читать перед работой с БД/пайплайном
```

---

*Версия: 1.0 · Проект: Granite CRM · Antigravity workspace rules*
