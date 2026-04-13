# Granite CRM — Шаблоны и task types: обновление

**Дата:** 2026-04-12
**Ветка:** `feat/web-search-scraper`
**HEAD:** `703e969`

---

## 1. Контекст

### Бизнес

**RetouchPro** — профессиональная подготовка портретов для гравировки на памятниках (мемориальных плитах). Freelance-сервис Александра.

**Сайт:** https://aipunkfacility.github.io/monument-web/

**Целевая аудитория (B2B):**
- Ритуальные мастерские (изготовление памятников)
- Ритуальные агентства

**Ключевые selling points:**
1. Бесплатный тест ретуши 1-2 фото — без обязательств
2. Оплата после результата (для новых клиентов)
3. Сроки: 12-24 часа стандартно, 3-6 срочно (+50%)
4. Нейросети + ручная обработка — вытягивает детали из любых фото
5. Цены: 1 000 ₽ ретушь, до 2 000 ₽ сложный монтаж
6. Партнёрские условия для мастерских (10+ заказов/нед — спеццены, приоритет)
7. До 3 бесплатных правок
8. Любой формат: JPG, PNG, TIFF, RAW, сканы, фото с телефона, даже скриншоты

**Услуги:**
- Портретная ретушь (1 000 ₽) — детализация лица, контраст, замена одежды, подготовка под гравировку
- Сложный монтаж (до 2 000 ₽) — замена фона, сборка композиции, работа по эскизу
- Восстановление старых фото — нейросети + ручная обработка
- Техническая подготовка файла — строго по требованиям и технологии гравировки

**Контакты:**
- Telegram: @ganjavagen → https://t.me/ganjavagen
- WhatsApp: +8 494 694 35 43 → https://wa.me/84946943543
- Email: ganjavagen@gmail.com

**Сайт для отправки в сообщениях:** https://aipunkfacility.github.io/monument-web/

---

## 2. Проблема

### 2.1 Task types устарели

Текущие task types в Pydantic-схеме (`granite/api/schemas.py`):
```python
task_type: str = Field("follow_up", pattern="^(follow_up|send_portfolio|call|other)$")
```

**"call" не нужен** — никаких звонков, весь аутрич через сообщения (email, TG, WA).

### 2.2 Шаблоны устарели

Текущие 6 шаблонов в `scripts/seed_crm_templates.py`:
- Не содержат ссылку на сайт
- Текст слишком сухой и короткий
- Нет конкретных selling points (цены, сроки, условия)
- Нет differentiation от конкурентов
- Нет ссылки на портфолио

### 2.3 БД: шаблоны уже сидированы

В БД уже есть 6 записей в `crm_templates` (из Phase 2 seed). При обновлении seed-скрипта нужно:
- Обновить существующие шаблоны (UPDATE, не INSERT)
- Не дублировать

---

## 3. Задача

### 3.1 Обновить task types

**Файл:** `granite/api/schemas.py`

**Было:**
```python
class CreateTaskRequest(BaseModel):
    task_type: str = Field("follow_up", pattern="^(follow_up|send_portfolio|call|other)$")
```

**Стало:**
```python
class CreateTaskRequest(BaseModel):
    task_type: str = Field("follow_up", pattern="^(follow_up|send_portfolio|send_test_offer|check_response|other)$")
```

**Новые task types:**

| task_type | Название (RU) | Смысл |
|-----------|---------------|-------|
| `follow_up` | Follow-up | Отправить follow-up сообщение по расписанию |
| `send_portfolio` | Отправить портфолио | Отправить ссылку на сайт с портфолио и примерами работ |
| `send_test_offer` | Предложить тест | Предложить бесплатный тест ретуши 1-2 фото |
| `check_response` | Проверить ответ | Проверить, был ли ответ от компании |
| `other` | Другое | Задача нестандартного типа |

**Также обновить `UpdateTaskRequest`** если там есть pattern для task_type.

### 3.2 Обновить шаблоны в seed-скрипте

**Файл:** `scripts/seed_crm_templates.py`

**Полная замена массива TEMPLATES** на следующие 6 шаблонов:

```python
TEMPLATES = [
    {
        "name": "cold_email_1",
        "channel": "email",
        "subject": "Ретушь портретов для памятников — сотрудничество",
        "body": (
            "Здравствуйте!\n\n"
            "Меня зовут {from_name}, я занимаюсь профессиональной подготовкой портретов "
            "для гравировки на памятниках.\n\n"
            "Кратко о том, что предлагаю:\n"
            "- Ретушь и детализация лиц — контраст и чёткость, которые хорошо читаются на камне\n"
            "- Сложный монтаж — замена фона, сборка по эскизу, восстановление старых фото\n"
            "- Нейросети + ручная обработка — вытягиваю детали даже из очень плохих исходников\n\n"
            "Сроки: 12-24 часа стандартно, 3-6 часов срочно.\n"
            "Цены: от 1 000 ₽ за ретушь, до 2 000 ₽ за сложный монтаж.\n\n"
            "Первый портрет — бесплатно, чтобы вы могли оценить качество без обязательств.\n\n"
            "Примеры работ и подробности: https://aipunkfacility.github.io/monument-web/\n\n"
            "Если актуально для вашей мастерской — буду рад обсудить сотрудничество.\n"
            "Партнёрам, которые отправляют 10+ заказов в неделю — спецусловия.\n\n"
            "С уважением,\n{from_name}\n"
            "Telegram: @ganjavagen | WhatsApp: +8 494 694 35 43"
        ),
        "description": "Первое холодное письмо. Полное — с услугами, ценами, сроками, ссылкой на сайт, контактами.",
    },
    {
        "name": "follow_up_email",
        "channel": "email",
        "subject": "Re: Ретушь портретов для памятников",
        "body": (
            "Добрый день!\n\n"
            "Недавно писал вам по теме ретуши портретов для гравировки на памятниках.\n\n"
            "Понимаю, что входящих много — просто хочу оставить ссылку на примеры работ, "
            "чтобы вы могли посмотреть в удобное время:\n"
            "https://aipunkfacility.github.io/monument-web/\n\n"
            "Если когда-нибудь понадобится ретушь — буду рад помочь. "
            "Первый портрет делаю бесплатно.\n\n"
            "Если это не актуально — просто дайте знать, больше не буду беспокоить.\n\n"
            "С уважением,\n{from_name}"
        ),
        "description": "Follow-up если не ответили на первое письмо. Мягкий, с ссылкой на сайт.",
    },
    {
        "name": "tg_intro",
        "channel": "tg",
        "subject": "",
        "body": (
            "Добрый день! Меня зовут {from_name}. "
            "Занимаюсь профессиональной ретушью портретов для гравировки на памятниках.\n\n"
            "Подготавливаю фото так, чтобы на камне читался каждый элемент лица. "
            "Сроки 12-24 часа, срочно — 3-6 часов.\n\n"
            "Примеры работ: https://aipunkfacility.github.io/monument-web/\n\n"
            "Первый портрет — бесплатно, без обязательств. "
            "Если интересно — пришлю подробности по условиям для мастерских."
        ),
        "description": "Первое сообщение в Telegram. Ссылка на сайт + оффер бесплатного теста.",
    },
    {
        "name": "tg_follow_up",
        "channel": "tg",
        "subject": "",
        "body": (
            "Добрый день, писал ранее про ретушь портретов для памятников.\n\n"
            "Оставлю ссылку на примеры, чтобы можно было посмотреть в удобное время: "
            "https://aipunkfacility.github.io/monument-web/\n\n"
            "Если это не актуально — дайте знать, больше не буду писать. "
            "Если интересно — отвечу на любые вопросы."
        ),
        "description": "Follow-up в TG если не ответили. Ссылка на сайт + мягкий exit.",
    },
    {
        "name": "wa_intro",
        "channel": "wa",
        "subject": "",
        "body": (
            "Здравствуйте! Меня зовут {from_name}. "
            "Занимаюсь ретушью портретов для гравировки на памятниках.\n\n"
            "Подготавливаю фото с идеальным контрастом и детализацией для нанесения на камень. "
            "Беру фото любого качества — даже очень старые и повреждённые.\n\n"
            "Сроки: 12-24 часа. Цены: от 1 000 ₽.\n\n"
            "Примеры работ: https://aipunkfacility.github.io/monument-web/\n\n"
            "Первый портрет делаю бесплатно — можно оценить качество без обязательств."
        ),
        "description": "Первое сообщение в WhatsApp. Ссылка на сайт + оффер бесплатного теста.",
    },
    {
        "name": "wa_follow_up",
        "channel": "wa",
        "subject": "",
        "body": (
            "Добрый день, писал вам ранее по теме ретуши портретов для памятников.\n\n"
            "Примеры работ: https://aipunkfacility.github.io/monument-web/\n\n"
            "Если не актуально — напишите \"нет\", больше не беспокою. "
            "Если интересно — отвечу на любые вопросы."
        ),
        "description": "Follow-up в WA. Короткий, со ссылкой на сайт + exit.",
    },
]
```

### 3.3 Обновить seed-логику: UPDATE вместо INSERT

Текущая логика seed:
```python
existing = {row[0] for row in session.query(CrmTemplateRow.name).all()}
to_insert = [t for t in TEMPLATES if t["name"] not in existing]
```

Она пропускает существующие шаблоны. Нужно **заменить на UPDATE**:

```python
def seed_crm_templates():
    db = Database()
    with db.session_scope() as session:
        updated = 0
        inserted = 0
        for t in TEMPLATES:
            existing = session.query(CrmTemplateRow).filter_by(name=t["name"]).first()
            if existing:
                # UPDATE существующего шаблона
                existing.channel = t["channel"]
                existing.subject = t["subject"]
                existing.body = t["body"]
                existing.description = t["description"]
                updated += 1
            else:
                # INSERT нового
                session.add(CrmTemplateRow(**t))
                inserted += 1

        logger.info(
            f"SEED crm_templates: обновлено {updated}, создано {inserted}"
        )
    db.engine.dispose()
    return updated, inserted
```

### 3.4 Запустить обновлённый seed

```bash
cd /home/z/my-project/granite-crm-db
source .venv/bin/activate
python -m scripts.seed_crm_templates
```

Ожидание: `SEED crm_templates: обновлено 6, создано 0`

### 3.5 Проверить результат

```bash
python -c "
from granite.database import Database, CrmTemplateRow
db = Database()
with db.session_scope() as s:
    for t in s.query(CrmTemplateRow).order_by(CrmTemplateRow.id).all():
        has_link = 'monument-web' in t.body
        print(f'{t.name:20s} | channel={t.channel:5s} | link={has_link} | len={len(t.body)}')
db.engine.dispose()
"
```

Ожидание: все 6 шаблонов содержат ссылку `monument-web`.

---

## 4. Файлы для изменения

| Файл | Действие |
|------|----------|
| `granite/api/schemas.py` | ИЗМЕНИТЬ — task_type pattern: убрать `call`, добавить `send_test_offer`, `check_response` |
| `scripts/seed_crm_templates.py` | ИЗМЕНИТЬ — полная замена TEMPLATES + логика UPDATE |

**НЕ менять:** database.py, api/deps.py, api/tasks.py (там только схемы, не БД), любой другой файл.

---

## 5. Что ещё стоит обновить (TODO, не в этой задаче)

1. **`granite/database.py`** — добавить CHECK constraint на `crm_tasks.task_type` (если SQLAlchemy поддерживает для SQLite):
   ```python
   task_type = Column(String, nullable=False, server_default="follow_up")
   # CHECK (task_type IN ('follow_up','send_portfolio','send_test_offer','check_response','other'))
   ```

2. **`granite/api/tasks.py`** — если есть валидация task_type вне Pydantic схем — обновить.

3. **UI-план** — обновить task_type dropdown в форме создания задачи.

---

## 6. Тестирование

```bash
# 1. Pydantic валидация
python -c "
from granite.api.schemas import CreateTaskRequest, UpdateTaskRequest
# Допустимые типы
for t in ['follow_up', 'send_portfolio', 'send_test_offer', 'check_response', 'other']:
    CreateTaskRequest(title='test', task_type=t)
    print(f'  OK: {t}')
# Недопустимый
try:
    CreateTaskRequest(title='test', task_type='call')
    print('  FAIL: call should be rejected')
except Exception:
    print('  OK: call rejected')
"

# 2. Seed шаблонов
python -m scripts.seed_crm_templates

# 3. Проверка ссылок в шаблонах
python -c "
from granite.database import Database, CrmTemplateRow
db = Database()
with db.session_scope() as s:
    for t in s.query(CrmTemplateRow).all():
        assert 'monument-web' in t.body, f'{t.name}: нет ссылки на сайт!'
        assert len(t.body) > 100, f'{t.name}: слишком короткий!'
        print(f'  OK: {t.name} ({len(t.body)} chars)')
db.engine.dispose()
"

# 4. Все тесты pass
pytest --tb=short -q
```

---

## 7. Критерии успеха

1. `CreateTaskRequest(task_type="call")` → ValidationError (422)
2. `CreateTaskRequest(task_type="send_test_offer")` → OK
3. `CreateTaskRequest(task_type="check_response")` → OK
4. Все 6 шаблонов в БД обновлены (UPDATE, не INSERT)
5. Все 6 шаблонов содержат `https://aipunkfacility.github.io/monument-web/`
6. Все шаблоны содержат упоминание бесплатного теста
7. `pytest --tb=short -q` — pass

## 8. Коммит

```
git add -A && git commit -m "feat: update CRM templates with site link + selling points, remove 'call' task type

Templates: add monument-web URL, prices, timelines, free test offer, partner conditions.
Task types: remove 'call', add 'send_test_offer' and 'check_response'.
Seed script: UPDATE existing templates instead of skip."
```
