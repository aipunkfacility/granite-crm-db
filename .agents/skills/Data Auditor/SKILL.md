---
name: Data Auditor
description: |
  Аудит качества данных в Granite CRM (granite.db). Анализ аномалий скоринга,
  дублей, «мёртвых» записей, пустых контактов. Генерация health-check отчётов.
---

# Data Auditor — Инструкция для агента

## Обзор

Этот скилл описывает, как проводить аудит базы данных `granite.db` проекта
Granite Workshops DB. База содержит около **6000+ компаний** в **29 городах**.

Инструменты: **SQLite MCP** (прямые SQL-запросы) + `scripts/audit_database.py`.

---

## Когда использовать этот скилл

- Перед экспортом данных в CSV или передачей в CRM
- После парсинга нового города
- При жалобах на некорректный CRM-скор или лишние дубли
- Когда пользователь просит «проверить базу»

---

## Структура базы данных

```
raw_companies     — сырые данные скраперов (дубли допустимы)
companies         — после дедупликации (уникальные записи)
enriched_companies — основная таблица с обогащёнными данными и скором
```

Ключевые поля `enriched_companies`:
- `id`, `name`, `city` — идентификатор
- `phones` (JSON), `website`, `emails` (JSON) — контакты
- `messengers` (JSON) — `{"telegram": "...", "whatsapp": "...", "vk": "..."}`
- `crm_score` (INTEGER) — скоринговый балл
- `segment` (VARCHAR) — A / B / C / D
- `cms` — Bitrix / WordPress / Tilda / etc.
- `is_network` (BOOLEAN) — является ли филиальной сетью
- `has_marquiz` (BOOLEAN)
- `tg_trust` (JSON) — `{"trust_score": N}`

---

## Чек-лист аудита (выполнять последовательно)

### 1. Общая статистика

```sql
SELECT 
  (SELECT COUNT(*) FROM raw_companies) as raw_total,
  (SELECT COUNT(*) FROM companies) as companies_total,
  (SELECT COUNT(*) FROM enriched_companies) as enriched_total,
  (SELECT COUNT(*) FROM enriched_companies WHERE segment IS NULL) as no_segment,
  (SELECT COUNT(*) FROM enriched_companies WHERE crm_score IS NULL OR crm_score = 0) as zero_score,
  (SELECT COUNT(*) FROM enriched_companies 
   WHERE website IS NULL AND (emails IS NULL OR emails = '[]') 
   AND (messengers IS NULL OR messengers = '{}')) as no_contacts,
  (SELECT COUNT(DISTINCT city) FROM enriched_companies) as cities_count;
```

**Норма:** `no_segment = 0`. `zero_score` не должен превышать 30% от `enriched_total`.

---

### 2. Аномалии скоринга

Находим компании с богатыми данными, но низким скором (возможный баг в скоринге):

```sql
SELECT id, name, city, crm_score, segment, website, messengers
FROM enriched_companies
WHERE crm_score < 15
  AND website IS NOT NULL
  AND messengers != '{}'
  AND messengers IS NOT NULL
LIMIT 20;
```

**Ожидание:** Таких записей должно быть 0. Если есть — это скоринговый баг.

---

### 3. Качество по городам

```sql
SELECT 
  city,
  COUNT(*) as total,
  SUM(CASE WHEN crm_score = 0 OR crm_score IS NULL THEN 1 ELSE 0 END) as zero_score,
  SUM(CASE WHEN website IS NULL AND (emails IS NULL OR emails = '[]') 
           AND (messengers IS NULL OR messengers = '{}') THEN 1 ELSE 0 END) as no_contacts,
  ROUND(100.0 * SUM(CASE WHEN crm_score = 0 OR crm_score IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_zero
FROM enriched_companies
GROUP BY city
ORDER BY pct_zero DESC;
```

**Норма:** `pct_zero` < 25%. Города выше 30% — кандидаты на повторный парсинг.

**Текущие проблемные города (на дату аудита):**
- Ростов-на-Дону: 41.2% нулевых записей
- Севастополь: 40%
- Липецк: 37%
- Краснодар: 34.8%

---

### 4. Поиск дублей (не пойманных дедупликацией)

```sql
SELECT name, city, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
FROM enriched_companies
GROUP BY LOWER(TRIM(name)), city
HAVING cnt > 1
ORDER BY cnt DESC
LIMIT 20;
```

Также проверить дубли по телефону:

```sql
SELECT phones, city, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
FROM enriched_companies
WHERE phones != '[]' AND phones IS NOT NULL
GROUP BY phones, city
HAVING cnt > 1
ORDER BY cnt DESC
LIMIT 20;
```

---

### 5. Мёртвые записи (только название, ничего больше)

```sql
SELECT id, name, city
FROM enriched_companies
WHERE (phones IS NULL OR phones = '[]')
  AND website IS NULL
  AND (emails IS NULL OR emails = '[]')
  AND (messengers IS NULL OR messengers = '{}')
ORDER BY city;
```

Эти компании — кандидаты на ручную проверку или удаление.

---

### 6. Проверка Telegram Trust

```sql
SELECT 
  SUM(CASE WHEN json_extract(tg_trust, '$.trust_score') >= 2 THEN 1 ELSE 0 END) as tg_live,
  SUM(CASE WHEN json_extract(tg_trust, '$.trust_score') = 0 THEN 1 ELSE 0 END) as tg_dead,
  SUM(CASE WHEN messengers LIKE '%telegram%' THEN 1 ELSE 0 END) as has_telegram
FROM enriched_companies;
```

---

### 7. CMS-распределение

```sql
SELECT cms, COUNT(*) as cnt
FROM enriched_companies
WHERE cms IS NOT NULL
GROUP BY cms
ORDER BY cnt DESC;
```

---

## Запуск скрипта аудита

Скрипт генерирует полный Markdown-отчет:

```bash
uv run scripts/audit_database.py
# или с указанием города:
uv run scripts/audit_database.py --city "Краснодар"
# сохранить отчет:
uv run scripts/audit_database.py --output data/audit_report.md
```

---

## Интерпретация результатов и рекомендации

| Проблема | Действие |
|----------|----------|
| `pct_zero > 30%` в городе | `python cli.py run "Город" --re-enrich` |
| Дубли по названию | Запустить `--no-scrape` с пересчётом дедупликации |
| Скоринговые аномалии | Проверить `granite/enrichers/classifier.py` |
| Много мёртвых записей | Включить `reverse_lookup` в `config.yaml` |
| `tg_dead` >> `tg_live` | Проверить `tg_finder.py` на изменение API t.me |

---

## Вывод отчёта

При выявлении проблем — формируй Markdown-резюме вида:

```markdown
## 🔍 Health Check — Granite CRM (дата)

**Всего компаний:** 6046 в 29 городах
**Требуют внимания:** 1513 без контактов (25%)

### ⚠️ Города с плохим качеством (> 30% нулевых записей)
| Город | Всего | Без контактов | % |
|-------|-------|--------------|---|
| Ростов-на-Дону | 413 | 168 | 41.2% |
...

### ✅ Рекомендации
1. Запустить `re-enrich` для Ростова, Севастополя, Липецка
2. Включить reverse_lookup для городов с > 35% мёртвых записей
```
