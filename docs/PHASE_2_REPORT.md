# Отчёт: Фаза 2 — Централизация конфигурации

**Дата:** 2026-04-10
**Коммит:** `35df5d5`
**Статус:** ✅ Завершена

## Цель

Убрать захардкоженные значения (magic numbers), привести конфиг к единообразию. Все настраиваемые параметры должны читаться из `config.yaml`, а не быть вшитыми в код.

## Выполненные шаги

### 2.1 Аудит magic numbers

Проведён полный аудит всех файлов на наличие захардкоженных значений. Найдены:

| Файл | Magic number | Описание |
|------|-------------|----------|
| `enrichment_phase.py:203` | `50` | batch flush размер |
| `_tg_common.py:2-3` | `5`, `5` | TG_MAX_RETRIES, TG_INITIAL_BACKOFF |
| `tg_finder.py:11` | `10` | tg_request timeout |
| `tg_trust.py:62` | `1.0`, `2.0` | adaptive_delay min/max |
| `web_search.py:45` | `600` | _FAILED_DOMAINS_TTL |
| `config.yaml:303` | комментарий | «NOT yet used by all scrapers» |

### 2.2 Вынести в config.yaml

Добавлены новые параметры в `config.yaml`:

```yaml
enrichment:
  web_client:
    timeout: 60
    search_limit: 3
  batch_flush: 50
  network_threshold: 2
  tg_finder:
    check_delay: 1.5
    max_retries: 5
    initial_backoff: 5
    request_timeout: 10
  tg_trust:
    empty_profile_penalty: -5
    check_delay_min: 1.0
    check_delay_max: 2.0

scraping:
  failed_domain_cache_ttl: 600
```

**Код обновлён для чтения из конфига:**

- **`_tg_common.py`** — добавлена функция `get_tg_config(config)` для извлечения Telegram-параметров с дефолтами. Константы `TG_MAX_RETRIES` и `TG_INITIAL_BACKOFF` сохранены как дефолтные значения.
- **`tg_finder.py`** — `tg_request()` теперь принимает параметры `max_retries`, `initial_backoff`, `timeout` из конфига. `find_tg_by_phone()` и `find_tg_by_name()` читают конфиг через `get_tg_config()`.
- **`tg_trust.py`** — `check_tg_trust()` принимает `config` (опционально). Читает `check_delay_min/check_delay_max` из `enrichment.tg_trust` и `request_timeout/max_retries` из `enrichment.tg_finder`.
- **`enrichment_phase.py`** — batch flush размер (`50`) заменён на `self.config.get("enrichment", {}).get("batch_flush", 50)`. `check_tg_trust()` теперь получает `self.config`.
- **`manager.py`** — WebClient config читается из новой секции `enrichment.web_client` с fallback на `sources.web_search`.
- **`web_search.py`** — `_FAILED_DOMAINS_TTL` заменён на `scraping.failed_domain_cache_ttl` из конфига. Добавлена функция `_get_failed_domain_ttl(config)`, TTL передаётся через `self._failed_domain_ttl` в `WebSearchScraper.__init__`.

### 2.3 Firecrawl fallback

Уже удалён в Фазе 0 (коммит `044be35`). Подтверждено: в `manager.py` нет ссылок на `firecrawl`. Остатки только в документации (README.md, docs/) — задача Фазы 5.

### Удалённое из config.yaml

- Секция `enrichment.messenger_pages` (7 URL) — нигде не использовалась в коде. MessengerScanner содержит собственный список страниц для сканирования.

## Результаты тестирования

- **240/240 тестов проходят** ✅
- Обратная совместимость сохранена: все дефолтные значения совпадают с бывшими magic numbers
- При отсутствии конфига или новых секций — используются прежние дефолты

## Критерии успеха (из REFACTORING_PLAN.md)

- [x] Нет magic numbers в коде (все настраиваемые значения — из конфига)
- [x] Дефолтный конфиг покрывает все параметры
- [x] Все тесты проходят (240/240)

## Изменённые файлы (7)

| Файл | Изменение |
|------|-----------|
| `config.yaml` | Добавлены `enrichment.web_client`, `enrichment.batch_flush`, `enrichment.network_threshold`, `enrichment.tg_finder.*`, `enrichment.tg_trust.*`, `scraping.failed_domain_cache_ttl`. Удалена неиспользуемая `enrichment.messenger_pages`. |
| `granite/enrichers/_tg_common.py` | Добавлена `get_tg_config(config)` |
| `granite/enrichers/tg_finder.py` | Параметры TG-запросов из конфига |
| `granite/enrichers/tg_trust.py` | Задержки и timeout из конфига |
| `granite/pipeline/enrichment_phase.py` | batch_flush из конфига, config → check_tg_trust |
| `granite/pipeline/manager.py` | WebClient config из enrichment.web_client |
| `granite/scrapers/web_search.py` | failed_domain_cache_ttl из конфига |

## Следующая фаза

**Фаза 3: Улучшение error handling и логирования** — retry для сетевых операций, структурированные ошибки, вынос sys.exit из менеджера.
