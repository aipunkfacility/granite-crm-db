# Granite CRM — Web UI: подробный дев-план (Версия 6.0)

**Дата:** 2026-04-12
**Стек:** Next.js 16 (App Router), TypeScript, Tailwind CSS 4, shadcn/ui
**Управление состоянием и кэширование:** TanStack React Query v5
**Синхронизация URL-стейта:** nuqs
**Бэкенд:** Granite CRM REST API (FastAPI, `feat/web-search-scraper`, HEAD `703e969`)
**Аутентификация:** не требуется (локальное использование одним пользователем)

**Что изменилось в v6.0 по сравнению с v5.1:**

| # | Изменение | Обоснование |
|---|-----------|-------------|
| 1 | Версия финализирована как v6.0 | Все аудиты (Super Z + Claude) применены, contradictions resolved |
| 2 | Desktop-only (без мобильной адаптации) | CRM для локального десктопного использования, mobile не нужен |
| 3 | SSE GET /stream помечен как optional | Требует изменения бэкенд-архитектуры стриминга; `@microsoft/fetch-event-source` допустим на v1 |
| 4 | 429 handling помечен как defensive | Текущий бэкенд не возвращает 429; обработка добавлена проактивно |

**Что изменилось в v5.1 (аудит Клода):**

| # | Изменение | Обоснование |
|---|-----------|-------------|
| 1 | Sidebar переведён в `"use client"` | `usePathname()` — клиентский хук, не работает в Server Components |
| 2 | Исправлены все cross-reference номера п. 3.x | Нумерация расходилась с заголовками в 10+ местах |
| 3 | Health check код: `JSONResponse` вместо кортежа | FastAPI не поддерживает `return (body, status_code)` — это паттерн Flask |
| 4 | SSE: нативный `EventSource` через `GET /stream` (optional) | Упрощение: отдельный GET-endpoint вместо `@microsoft/fetch-event-source` |
| 5 | `queryKey` мемоизация для React Query | Объект фильтров сравнивается по ссылке → лишние запросы при ререндере |
| 6 | Batch send: `beforeunload` handler | "Не закрывайте вкладку" ненадёжно — нужен программный блок |
| 7 | `CreateTaskRequest.title` → required | Бэкенд требует title, фронтенд не должен позволять отправить без него |
| 8 | Обработка `429 Too Many Requests` (defensive) | Текущий бэкенд не возвращает 429, но обработка добавлена проактивно |
| 9 | `Template.variables` — вычисляется на фронте | Поля нет в ORM-модели; парсинг через regex `body.match(/\{(\w+)\}/g)` |
| 10 | Dashboard: сразу `/stats`, без временного решения | `/stats` — 10 минут на бэкенде; нет смысла делать 3 запроса потом рефакторить |
| 11 | ErrorBoundary в Фазу 0 | Без него React Query errors = белый экран |
| 12 | `nuqs@2.x` зафиксирован + `<NuqsAdapter>` | Совместимость с Next.js 16; в некоторых конфигурациях требуется обёртка |

**Что изменилось в v5.0 по сравнению с v4:**

| # | Изменение | Обоснование |
|---|-----------|-------------|
| 1 | Новые пп. 3.4–3.5, 3.10–3.11 в Backend Requirements | Обнаружены при code review: SQL-инъекция, follow-up OOM, health check, seed templates |
| 2 | Пересмотрены приоритеты бэкенд-требований | 3.4 (SQL fix) и 3.6 (array filters) повышены до «высокий» |
| 3 | Исправлена таблица переходов воронки (п. 4) | Код `stage_transitions.py` допускает WA-отправку с любой стадии (кроме final), v4 указывал только `tg_sent` |
| 4 | Task types обновлены на актуальные | Убран `call`, добавлены `send_test_offer` и `check_response` — повсюду в документе |
| 5 | Подтверждено: touch-логирование в messenger работает | `dispatcher.py` создаёт `CrmTouchRow` и вызывает `apply_outgoing_touch`; commit через `get_db` auto-commit |
| 6 | Добавлено требование обновления seed-скрипта шаблонов | Текущие шаблоны устарели: нет ссылки на сайт, нет цен, короткий текст |
| 7 | Обновлён порядок работы (п. 9) | Новые задачи интегрированы в road map |
| 8 | Добавлен п. 3.11: seed-скрипт шаблонов | Текущий `seed_crm_templates.py` только INSERT, нужно UPDATE |

---

## 1. Архитектура

### 1.1 Принципы

*   **Next.js App Router** — маршрутизация средствами фреймворка. Server Components используются **только** для корневого Layout, Sidebar и метаданных. Все таблицы, панели, фильтры — `"use client"`.
*   **API-first** — UI это тонкая оболочка над существующим REST API. Все данные приходят через `/api/v1/...`.
*   **Настраиваемый API URL** — `NEXT_PUBLIC_CRM_API_URL` (dev: `http://localhost:8000`, prod: часть деплоя).
*   **Глубокие ссылки (Deep linking)** — состояние таблиц (пагинация, фильтры) хранится в URL Search Parameters через библиотеку `nuqs`.
*   **Реактивное кэширование** — TanStack React Query v5 для мгновенной навигации и инвалидации.
*   **Аутентификация не требуется** — проект для локального использования. JWT/OAuth не реализуются.
*   **Desktop-only** — основной viewport 1280px+. Мобильная адаптация не требуется — CRM для локального десктопного использования.

### 1.2 Структура маршрутов

Единый Layout с боковым Sidebar, контент рендерится через навигацию:

| # | Маршрут | Rendering | Назначение | Ключевые элементы |
|---|--------|-----------|-----------|-------------------|
| 1 | `/` | Server | Редирект на `/dashboard` | `redirect('/dashboard')` |
| 2 | `/dashboard` | Client | Обзор + KPI | Воронка, счётчики, таблица |
| 3 | `/companies` | Client | Список компаний | Datatable, фильтры, пагинация, side panel |
| 4 | `/tasks` | Client | Задачи/напоминания | Datatable, фильтры, CRUD |
| 5 | `/campaigns` | Client | Email-кампании | Список, создание, SSE-прогресс, статистика |
| 6 | `/followup` | Client | Очередь follow-up | Список, batch-send |
| 7 | `/templates` | Client | База шаблонов | Список, редактор с превью |

**Side Panel для компании:** открывается через `?companyId=123` в URL на странице `/companies`. Это не отдельный роут — просто query-параметр, управляющий видимостью панели поверх таблицы.

### 1.3 Физическая структура проекта

```
src/
├── app/
│   ├── layout.tsx               # Root layout — Server Component (meta, fonts, Providers)
│   ├── page.tsx                 # Server Component — redirect to /dashboard
│   ├── (main)/                  # Route group
│   │   ├── layout.tsx           # Server Component — Sidebar, Header (static, без данных)
│   │   ├── dashboard/page.tsx   # "use client" — KPI, funnel, таблица
│   │   ├── companies/page.tsx   # "use client" — таблица + side panel
│   │   ├── tasks/page.tsx       # "use client" — таблица задач
│   │   ├── campaigns/page.tsx   # "use client" — кампании + SSE
│   │   ├── followup/page.tsx    # "use client" — очередь follow-up
│   │   └── templates/page.tsx   # "use client" — редактор шаблонов
│   └── globals.css              # Tailwind CSS 4
├── lib/
│   ├── api-client.ts            # fetch-wrapper + error interceptor + API status tracking
│   ├── query-client.ts          # QueryClient с настройками (staleTime, gcTime)
│   ├── types.ts                 # TypeScript типы — Раздел 6
│   └── utils.ts                 # cn(), formatDate() и прочие хелперы
├── components/
│   ├── layout/
│   │   ├── sidebar.tsx          # "use client" — навигация через <Link>, usePathname() для active state
│   │   └── header.tsx           # "use client" — индикатор API, breadcrumbs
│   ├── dashboard/               # "use client" — kpi-cards, funnel-chart, recent-table
│   ├── companies/               # "use client" — data-table, filters-bar, company-panel
│   ├── tasks/                   # "use client" — data-table, task-dialog
│   ├── campaigns/               # "use client" — list, create-dialog, run-progress, stats
│   ├── followup/                # "use client" — queue-list, batch-send-button
│   ├── templates/               # "use client" — template-list, template-editor
│   └── ui/                      # shadcn/ui (button, dialog, select, table, badge...)
└── hooks/
    ├── queries/                 # useCompanies, useCompany, useTasks, useFunnel, useFollowup...
    ├── mutations/               # useUpdateCompany, useCreateTask, usePatchTask...
    ├── use-sse.ts               # SSE Hook для кампаний
    └── use-api-status.ts        # Глобальный статус подключения к API
```

### 1.4 API Client и React Query

**Два слоя:**

1.  **`api-client.ts`** — тонкий fetch-wrapper. Базовый URL из `NEXT_PUBLIC_CRM_API_URL`. Перехватывает network errors → обновляет глобальный статус через `use-api-status.ts`. При первой ошибке включает фоновый пинг `/health` (каждые 15 сек) до восстановления. Не использует axios — достаточно нативного fetch.

    > **v5.1:** добавить обработку `429 Too Many Requests`. При batch-send и campaign run бэкенд ставит `time.sleep(3)` внутри; если фронтенд отправляет несколько запросов параллельно (двойной клик), возможны конфликты. Реализация: при получении 429 — показать toast "Слишком много запросов, подождите" + отключить кнопку на 3 секунды. Автоматический retry **не** делаем — это может усилить нагрузку.

    > **v5 примечание:** текущий `GET /health` возвращает `{"status": "ok"}` без проверки БД. Для корректного индикатора подключения в Header рекомендуется обновить health endpoint (п. 3.10). До обновления — использовать ping как есть: если сервер отвечает, считаем API доступным.

2.  **React Query хуки** — инкапсулируют queryKey, queryFn, кэширование.

```typescript
// hooks/queries/useCompanies.ts
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { useMemo } from 'react';
import { getCompanies } from '@/lib/api-client';
import type { CompanyFilters } from '@/lib/types';

export const useCompanies = (filters: CompanyFilters) => {
  // v5.1: мемоизация queryKey — без useMemo объект filters сравнивается
  // по ссылке при каждом ререндере, вызывая лишние запросы
  const queryKey = useMemo(
    () => ['companies', filters],
    [filters.city, filters.search, filters.funnel_stage, filters.page,
     filters.per_page, filters.order_by, filters.order_dir, filters.has_telegram,
     filters.has_email, filters.has_whatsapp, filters.min_score, filters.segment]
  );
  return useQuery({
    queryKey,
    queryFn: () => getCompanies(filters),
    placeholderData: keepPreviousData,
  });
};
```

> **v5.1 примечание:** если `filters` передаётся как объект, React Query сравнивает его по ссылке (`Object.is`). При каждом ререндере компонента с `nuqs` создаётся новый объект → лишние запросы. Мемоизация через явный список зависимостей (или `useMemo(() => filters, [JSON.stringify(filters)])` для краткости) решает проблему.

**Инвалидация:** при завершении мутации (PATCH компании, POST задачи) → `queryClient.invalidateQueries({ queryKey: ['companies'] })`. React Query обновит данные прозрачно на фоне.

### 1.5 Соглашения по компонентам

| Слой | Директива | Данные | Примеры |
|---|---|---|---|
| `app/layout.tsx`, `app/(main)/layout.tsx` | Server Component | Нет API-вызовов | HTML-обёртка, `<html>`, `<body>` |
| `components/layout/sidebar.tsx` | `"use client"` | Нет (статические ссылки) | Навигация, `usePathname()` для active state |
| `components/layout/header.tsx` | `"use client"` | API status из контекста | Индикатор подключения |
| Все `components/*/*.tsx` | `"use client"` | React Query хуки | Таблицы, панели, диалоги |
| Все `app/(main)/*/page.tsx` | `"use client"` | React Query хуки | Страницы с данными |

**Загрузка:** на клиенте отображаются спиннеры или skeleton-компоненты. SSR pre-fetching данных не используется — CRM не требует SEO и усложняет код.

---

## 2. Фазы реализации

### Фаза 0 — Фундамент и Инфраструктура

*   **Root Layout** (`app/layout.tsx`) — Server Component с `<html>`, `<body>`, шрифтами, метаданными. Оборачивает children в `<QueryClientProvider>`.
*   **Main Layout** (`app/(main)/layout.tsx`) — Server Component с Sidebar и `<Outlet />` (Next.js: `{children}`).
*   **Sidebar** — `"use client"`. Навигация через `<Link>`. Активный пункт определяется через `usePathname()` из `next/navigation`.

    > **v5.1 исправление:** в v5 sidebar был помечен как Server Component, но `usePathname()` — клиентский хук и **не работает** в Server Components. Вариант с выносом активного индикатора в отдельный `<ActiveLink>` client component добавляет лишнюю сложность. Проще и надёжнее — сделать весь sidebar `"use client"`.
*   **API Client** (`lib/api-client.ts`) — fetch-wrapper с базовым URL, обработкой ошибок, JSON-парсингом.
*   **API Status** (`hooks/use-api-status.ts`) — React Context + хук. Глобальный state: `"connected"` / `"error"`. Header рендерит индикатор на основе этого контекста.
*   **Types** (`lib/types.ts`) — все TypeScript типы из Раздел 6.
*   **shadcn/ui init** — установить `button`, `dialog`, `select`, `table`, `badge`, `input`, `textarea`, `dropdown-menu`, `pagination`, `tooltip`, `skeleton`, `card`, `alert`, `switch`, `tabs`.
*   **ErrorBoundary** (`react-error-boundary`) — глобальный перехватчик ошибок. Обернуть приложение в `<ErrorBoundary>` внутри `app/layout.tsx` (после `QueryClientProvider`). При ошибке любого React Query запроса без ErrorBoundary — белый экран. Fallback: карточка с описанием ошибки и кнопкой "Перезагрузить".

    > **v5.1:** добавлено по результатам аудита. Без ErrorBoundary любая ошибка API-запроса, не обработанная в конкретном компоненте, крашит всё приложение.
*   **nuqs@2.x** — зафиксировать мажорную версию. В некоторых конфигурациях Next.js 16 требуется обёртка `<NuqsAdapter>` в корневом Layout.

    > **v5.1:** зафиксирована версия и добавлено предупреждение о совместимости. API `nuqs` менялся между версиями App Router.

### Фаза 1 — Dashboard

*   **Источники данных (2 параллельных запроса):**
    *   `GET /api/v1/stats` → `{companies_total, tasks_total, tasks_pending, campaigns_total, campaigns_completed}`.
    *   `GET /api/v1/funnel` → `{new: N, email_sent: N, ...}`.

    > **v5.1:** реализация `/stats` endpoint (п. 3.9) занимает ~10 минут на бэкенде. Рекомендуется сделать его **до начала** Dashboard — это сразу даёт 2 запроса вместо 3 и избавляет от необходимости использовать временные хаки (`?per_page=1`). Временный вариант с 3 запросами (v5) **не рекомендуется**.

*   **KPI-карточки (4 шт.):**
    1.  Всего компаний (из `stats.companies_total`).
    2.  Активных задач (из `stats.tasks_pending`).
    3.  Конверсия email: `email_opened / email_sent * 100`% (из funnel).
    4.  Ответивших (из `funnel.replied`).
*   **Funnel Chart** — div-based. Каждая стадия — горизонтальный bar, ширина пропорциональна числу. Цвета из Tailwind palette (от серого `new` до зелёного `interested`).
*   **Таблица "Последние компании"** — `/companies?per_page=5&order_by=crm_score&order_dir=desc`. Кликабельные строки (ведут на `/companies?companyId={id}`).

### Фаза 2 — Компании: Список, Фильтры и Side Panel

#### Фаза 2a — Таблица и URL-фильтры

*   **URL-стейт через `nuqs`:** все фильтры синхронизированы с URL.
    ```typescript
    // companies/page.tsx
    const [search, setSearch] = useQueryState('search', { defaultValue: '' });
    const [stage, setStage] = useQueryState('funnel_stage');
    const [page, setPage] = useQueryState('page', { defaultValue: '1', parse: Number });
    const [orderBy, setOrderBy] = useQueryState('order_by', { defaultValue: 'crm_score' });
    const [orderDir, setOrderDir] = useQueryState('order_dir', { defaultValue: 'desc' });
    ```
*   **Пример URL:** `/companies?page=2&search=ООО&funnel_stage=new&funnel_stage=email_sent`
*   **Фильтры:**
    *   `funnel_stage` — одно значение (текущий бэкенд). Multi-select — после бэкенд-обновления (п. 3.6).
    *   `has_telegram` — toggle (0/1).
    *   `has_email` — toggle (0/1).
    *   `has_whatsapp` — toggle (0/1), после бэкенд-обновления (п. 3.8).
    *   `min_score` — number input.
    *   `city`, `search` — текстовые поля.

    > **v5 примечание по `search`:** текущая реализация бэкенда использует `ilike(f"%{search}%")` без экранирования символов `%` и `_`. Пользователь может ввести `%` и получить все записи. Это исправляется в п. 3.4. Фронтенд должен быть готов к тому, что после исправления бэкенда поведение поиска незначительно изменится (литеральные `%` и `_` будут искаться как символы, а не как wildcard).

*   **Сортировка:** клик по заголовку колонки → обновляет `order_by` и `order_dir` в URL.
    *   Допустимые `order_by`: `crm_score`, `name_best`, `city`, `funnel_stage`.
*   **Пагинация:** shadcn `Pagination` → обновляет `page` в URL.
*   **Колонки таблицы:** Название, Город, Стадия (badge), CRM Score, Email (иконка-индикатор), TG (иконка), WA (иконка), Последний контакт.

#### Фаза 2b — Company Detail (Side Panel)

*   **Открытие:** клик на строку → `setCompanyId(id)`. Панель появляется справа (400px width), таблица сужается. URL: `/companies?companyId=123`. Закрытие — крестик или `setCompanyId(null)`.
*   **Данные:** `GET /api/v1/companies/{id}` через `useCompany(id)`. Панель показывает skeleton пока загружается.
*   **Поля (секции):**

    *   **Шапка:** название, город, сегмент, CRM score (badge).
    *   **Контакты:** телефоны, emails, сайт, telegram, whatsapp, vk — кликабельные ссылки.
    *   **CRM-статус:** funnel_stage (dropdown для смены), stop_automation (Switch toggle), заметки (textarea).
    *   **Счётчики:** email_sent / email_opened / tg_sent / wa_sent.

*   **PATCH-мутации:**
    *   `funnel_stage` — `<Select>` со всеми стадиями. При выборе → `PATCH /api/v1/companies/{id}` → инвалидация кэша.
    *   `notes` — `<Textarea>` с автосохранением: `onBlur` + `Cmd/Ctrl+Enter`. Индикатор "Сохранено ✓" (green) / "Не сохранено" (yellow) / "Ошибка" (red).
    *   `stop_automation` — shadcn `<Switch>`. При toggle → PATCH. Визуально: если включён — оранжевый badge "Автоматизация остановлена".

*   **Вкладки внутри панели:**
    1.  **Касания** — `GET /api/v1/companies/{id}/touches` → хронология (channel badge, direction, subject, date). Пустое состояние: "Нет записей".

        > **v5 подтверждение:** touch-логирование работает корректно. `MessengerDispatcher` (dispatcher.py:72-85) создаёт `CrmTouchRow` и вызывает `apply_outgoing_touch()` при успешной отправке. Email-касания логируются аналогично в `campaigns.py`. Commit происходит автоматически через `get_db` dependency (deps.py:15 — `session.commit()` после yield).

    2.  **Задачи** — список задач этой компании + кнопка "+ Задача" → модальное окно создания.
    3.  **Отправить** — кнопки "Telegram" и "WhatsApp" → `POST /api/v1/companies/{id}/send` с `channel: "tg"` или `"wa"`. После бэкенд-доработки (п. 3.7) — также кнопка "Email".

### Фаза 3 — Задачи (Tasks)

*   **Список:** `GET /api/v1/tasks` → пагинированный.
    *   Фильтры в URL: `status`, `priority`, `company_id`.
    *   *Multi-select по `status` — после бэкенд-обновления (п. 3.6).*
*   **Поля ответа:** `id`, `company_id`, `company_name`, `title`, `task_type`, `priority`, `status`, `due_date`, `created_at`.
    *   `company_name` — добавляется через JOIN на бэкенде (п. 3.2). **Фронтенд не должен resolve название компании сам** — это N+1 проблема при пагинации.
*   **Task types:** `follow_up`, `send_portfolio`, `send_test_offer`, `check_response`, `other`.

    > **v5:** эти типы уже отражены в TypeScript типах (п. 6). Бэкенд обновляется в п. 3.3. Старый тип `call` удалён — он не нужен, весь аутрич идёт через сообщения.

*   **Колонки:** Название компании, Заголовок, Тип (badge), Приоритет (color dot), Статус (badge), Дедлайн, Действия.
*   **Быстрые действия:**
    *   Чекбокс → toggle `pending ↔ done`. Оптимистичный апдейт: UI меняется мгновенно, PATCH идёт в фоне. При ошибке — rollback.
    *   Клик по строке → открытие модального окна редактирования.
    *   Кнопка удаления (trash icon) → `DELETE /api/v1/tasks/{id}` с подтверждением (`AlertDialog`).
*   **Создание задачи:** кнопка "+ Задача" в header'е. Модальное окно:
    *   `title` (Input, required).
    *   `task_type` (Select).
    *   `priority` (Select: low/normal/high).
    *   `due_date` (DatePicker).
    *   `company_id` (опционально, если создаётся из контекста компании — презаполняется).
    *   `description` (Textarea).

### Фаза 4 — Кампании (Campaigns)

*   **Список:** `GET /api/v1/campaigns` → массив (не пагинирован). Колонки: Название, Шаблон, Статус (badge), Отправлено, Открыто, Ответили, Дата.
*   **Создание:** кнопка "+ Кампания" → Dialog:
    *   `name` (Input, required).
    *   `template_name` (Select из GET /templates — после п. 3.1).
    *   `filters` — группа: `city` (Input), `segment` (Input), `min_score` (Number).
*   **Детали:** клик на кампанию → раскрытие карточки с:
    *   Статистика: `GET /api/v1/campaigns/{id}/stats` → `open_rate` и т.д.
    *   Фильтры, с которыми была создана.
    *   Кнопка "Запустить" (если `status != running`).

    > **v5 примечание:** `_get_campaign_recipients()` вызывается дважды — в `get_campaign()` для предпросмотра и в `run_campaign()` для отправки. При больших списках получателей это создаёт двойную нагрузку. В будущем стоит кэшировать результат или передавать recipients между вызовами. Для текущей версии (локальное использование, <1000 получателей) — допустимо.

*   **Запуск (SSE):**
    *   Кнопка "Запустить" → `POST /api/v1/campaigns/{id}/run`.
    *   Хук `useCampaignSSE` подписывается на `text/event-stream`.

    > **v5.1:** вместо `@microsoft/fetch-event-source` (POST через `fetch` + `ReadableStream`) — добавить на бэкенде отдельный `GET /api/v1/campaigns/{id}/stream`. Этот endpoint проверяет, что кампания `running`, и стримит события. Тогда работает **нативный `EventSource`** без дополнительных зависимостей.

    **Бэкенд-доработка (добавить к п. 3.7 или отдельным PR):**
    ```python
    @router.get("/campaigns/{campaign_id}/stream")
    async def stream_campaign(campaign_id: int, db: Session = Depends(get_db)):
        campaign = db.get(CrmEmailCampaignRow, campaign_id)
        if not campaign or campaign.status != "running":
            raise HTTPException(409, "Campaign not running")
        return StreamingResponse(
            _campaign_event_generator(campaign_id, db),
            media_type="text/event-stream",
        )
    ```
    **Хук:** `new EventSource(`/api/v1/campaigns/${id}/stream`)` — стандартный API, работает надёжно, автопереподключение бесплатно.

    *   **UI прогресса:** progress bar + `"Отправлено: {sent} из {total}"` + текущий email.
    *   По завершении — уведомление toast: `"Кампания завершена: {sent} писем отправлено"`.
    *   Кнопка "Отмена" — закрывает EventSource (бэкенд пометит кампанию как `paused`).
*   **SSE формат (документация для хука):**
    ```
    data: {"status": "started", "total": 42}
    data: {"sent": 1, "total": 42, "current": "shop1@mail.ru"}
    data: {"sent": 2, "total": 42, "current": "shop2@mail.ru"}
    ...
    data: {"status": "completed", "sent": 42, "total": 42}
    ```
    Ошибки: `data: {"error": "Campaign not found"}` / `data: {"error": "Already running"}` / `data: {"error": "Template 'xxx' not found"}`.
*   **Лимиты бэкенда:** 3 сек между отправками, батч-коммит каждые 10, макс. 100 за запуск.
*   **Tracking pixel:** бэкенд автоматически вставляет `<img>` в письма. UI не участвует.

### Фаза 5 — Очередь Follow-up

*   **Список:** `GET /api/v1/followup?limit=100` → массив с рекомендациями.
*   **Колонки:** Компания, Город, Стадия, Дней с последнего контакта, Рекомендуемый канал (badge), Шаблон, Действие (badge), Кнопка "Отправить".
*   **Fallback каналов:** если `recommended_channel = tg`, но TG отсутствует → `wa`. Если и WA нет → `channel_available = false` (кнопка disabled, tooltip: "Нет контактных данных").
*   **Индивидуальная отправка:** кнопка "Отправить" → `POST /api/v1/companies/{company_id}/send` с `channel` и `template_name` из очереди. После успеха — refetch очереди.
*   **Batch Send ("Отправить всё"):**
    *   **Критично:** используется `for...of` с `await` (строгая последовательность, concurrency = 1). **Не `Promise.all`** — браузер ограничивает до 6 одновременных соединений, а бэкенду нужна последовательность для корректной обработки.
    *   UI: progress bar + счётчик + Warning alert (жёлтый): *"Не закрывайте вкладку во время массовой отправки"*.
    *   **v5.1:** добавить `beforeunload` event handler пока идёт отправка — программный блок закрытия вкладки. Предупреждение текстом ненадёжно, `beforeunload` даёт реальную защиту.
        ```typescript
        useEffect(() => {
          if (!isSending) return;
          const handler = (e: BeforeUnloadEvent) => {
            e.preventDefault();
            e.returnValue = 'Массовая отправка ещё не завершена. Вы уверены?';
          };
          window.addEventListener('beforeunload', handler);
          return () => window.removeEventListener('beforeunload', handler);
        }, [isSending]);
        ```
    *   Кнопка "Стоп" — прерывает цикл. Уже отправленные не откатываются.
*   **Skip:** нет отдельного endpoint. Для пропуска — открыть Company Detail Panel (кнопка-ссылка) и сменить стадию вручную.

    > **v5 предупреждение о производительности:** текущий `followup.py` загружает **все** подходящие компании в память через `q.all()` (строка 52), затем фильтрует по датам в Python (строки 62-68). При 5000+ компаний в CRM это может вызвать OOM или значительную задержку. Оптимизация описана в п. 3.5 (перенос фильтрации в SQL). Для первой версии UI — допустимо при <2000 компаний.

### Фаза 6 — Шаблоны (Templates)

*   **CRUD** через `GET/POST/PATCH/DELETE /api/v1/templates` (после бэкенд-доработки п. 3.1).
*   **Список:** grid или table. Колонки: Имя, Канал (badge), Предпросмотр (первые 50 символов body).
*   **Существующие шаблоны в БД:** `cold_email_1` (email), `follow_up_email` (email), `tg_intro` (tg), `tg_follow_up` (tg), `wa_intro` (wa), `wa_follow_up` (wa).

    > **v5 примечание:** текущие шаблоны в seed-скрипте устарели: нет ссылки на сайт (`monument-web`), нет цен и сроков, текст короткий и сухой. Обновлённые шаблоны описаны в п. 3.11 и в документе `update-templates-tasktypes.md`.

*   **Редактор:** клик на шаблон → side panel или dialog.
    *   `name` (Input, disabled для существующих — первичный ключ).
    *   `channel` (Select: email/tg/wa).
    *   `subject` (Input, только для email-шаблонов).
    *   `body` (Textarea или простой WYSIWYG).
    *   **Подсветка переменных:** `{from_name}`, `{city}`, `{company_name}`, `{website}` — выделяются цветом/фоном в textarea.
    *   **Превью:** кнопка "Превью" → подставляет тестовые данные → отображает результат.
*   **Создание:** кнопка "+ Шаблон" → dialog с полями name, channel, subject, body.

### Фаза 7 — Дополнения

*   Индивидуальная email-отправка из Company Detail Panel (п. 3.7).
*   Кнопка "Отправить тест" в Follow-up (создаёт task с `send_test_offer` type — п. 3.3).

### Фаза 8 — Полировка

*   Страница настроек (опционально): тестовые подключения TG/WA/Email.
*   Dark mode (опционально).
*   Виртуализация длинных списков (`@tanstack/react-virtual`).

---

## 3. Требования к бэкенду (Backend Requirements)

Перед или параллельно с разработкой UI. Разделены на три группы по критичности.

### 🔴 Критические (блокируют ключевой UI-функционал)

#### 3.1 [КРИТИЧЕСКИЙ] CRUD для шаблонов

> Приоритет: **критический** (повышен с «высокий» в v4). Без этого endpoint Фаза 6 (Templates UI) полностью неработоспособна: нет ни списка, ни создания, ни редактирования шаблонов. Кроме того, создание кампании (Фаза 4) требует Select из списка шаблонов.

Создать `granite/api/templates.py`:

| Метод | Endpoint | Описание |
|---|---|---|
| `GET` | `/api/v1/templates` | Список всех шаблонов |
| `GET` | `/api/v1/templates/{name}` | Один шаблон по имени |
| `POST` | `/api/v1/templates` | Создать шаблон |
| `PATCH` | `/api/v1/templates/{name}` | Обновить шаблон |
| `DELETE` | `/api/v1/templates/{name}` | Удалить шаблон |

Таблица `crm_templates` уже существует в БД (миграция `20260411_add_crm_tables`). ORM-модель `CrmTemplateRow` определена в `database.py` (строки 196-231) и имеет метод `render(**kwargs)` для подстановки переменных. Зарегистрировать роутер в `app.py`.

**Схемы (Pydantic):**

```python
class CreateTemplateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    channel: str = Field(..., pattern="^(email|tg|wa)$")
    subject: str = ""
    body: str = Field(..., min_length=1)
    description: str = ""

class UpdateTemplateRequest(BaseModel):
    channel: Optional[str] = Field(None, pattern="^(email|tg|wa)$")
    subject: Optional[str] = None
    body: Optional[str] = None
    description: Optional[str] = None
```

#### 3.2 [КРИТИЧЕСКИЙ] `company_name` в ответе `/tasks`

> Приоритет: **критический** (повышен с «высокий» в v4). Без JOIN таблица задач не может показать название компании, что делает её практически бесполезной. Фронтенд НЕ должен resolve название самостоятельно — это N+1 при пагинации.

Текущий `GET /api/v1/tasks` (tasks.py:40-79) возвращает `company_id` но не имя. Нужно добавить JOIN:

```python
# tasks.py — list_tasks()
from sqlalchemy import select
from granite.database import CompanyRow

@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db), ...):
    stmt = (
        select(CrmTaskRow, CompanyRow.name_best.label("company_name"))
        .outerjoin(CompanyRow, CrmTaskRow.company_id == CompanyRow.id)
    )
    # ... применить фильтры к stmt ...
    total = db.query(CrmTaskRow).filter(...).count()  # для total без JOIN
    rows = db.execute(stmt).all()

    return {
        "items": [
            {
                **task_fields,
                "company_name": company_name,  # str | None
            }
            for task, company_name in rows
        ],
        "total": total,
        ...
    }
```

Ответ добавляет поле `"company_name": "ООО Гранит-М"` (или `null` если компания удалена).

#### 3.3 [КРИТИЧЕСКИЙ] Обновление task types

> Приоритет: **критический** (повышен с «высокий» в v4). Тип `call` присутствует в `schemas.py` но не используется (всё аутрич через сообщения). Отсутствующие `send_test_offer` и `check_response` нужны для UI задач (Фаза 3) и кнопки "Отправить тест" (Фаза 7).

В `granite/api/schemas.py`:

```python
# Было (строка 29):
task_type: str = Field("follow_up", pattern="^(follow_up|send_portfolio|call|other)$")

# Стало:
task_type: str = Field("follow_up", pattern="^(follow_up|send_portfolio|send_test_offer|check_response|other)$")
```

**Также обновить `UpdateTaskRequest`** — добавить аналогичную валидацию `task_type` если используется (текущий `UpdateTaskRequest` не содержит `task_type`, но стоит добавить для будущей редакции):

```python
class UpdateTaskRequest(BaseModel):
    status: Optional[str] = Field(None, pattern="^(pending|in_progress|done|cancelled)$")
    priority: Optional[str] = Field(None, pattern="^(low|normal|high)$")
    title: Optional[str] = Field(None, min_length=1)
    task_type: Optional[str] = Field(None, pattern="^(follow_up|send_portfolio|send_test_offer|check_response|other)$")
```

**Новые task types:**

| task_type | Название (RU) | Смысл |
|-----------|---------------|-------|
| `follow_up` | Follow-up | Отправить follow-up сообщение по расписанию |
| `send_portfolio` | Отправить портфолио | Отправить ссылку на сайт с портфолио |
| `send_test_offer` | Предложить тест | Предложить бесплатный тест ретуши 1-2 фото |
| `check_response` | Проверить ответ | Проверить, был ли ответ от компании |
| `other` | Другое | Задача нестандартного типа |

### 🟡 Важные (улучшают надёжность и UX)

#### 3.4 [ВЫСОКИЙ] SQL-инъекция в `search` параметре

> Приоритет: **высокий** (новое в v5). Текущий код `companies.py:93` использует `ilike(f"%{search}%")` без экранирования символов `%` и `_`. Пользователь, введя `%`, получит все записи — это и баг, и потенциальная уязвимость.

Исправление в `companies.py`:

```python
def _escape_like(search: str) -> str:
    """Экранировать спецсимволы LIKE для безопасного поиска."""
    return search.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")

# В list_companies():
if search:
    q = q.filter(CompanyRow.name_best.ilike(f"%{_escape_like(search)}%", escape="\\"))
```

То же исправление применяется к любым другим `ilike`/`like` вызовам в проекте (tasks.py не имеет search, но будущие endpoints могут).

#### 3.5 [ВЫСОКИЙ] Follow-up SQL-оптимизация

> Приоритет: **высокий** (новое в v5). Текущий `followup.py` загружает все подходящие компании через `q.all()` (строка 52), затем фильтрует по `days_since_last_contact` в Python (строки 62-68). При 5000+ компаний — OOM или задержки.

Перенести фильтрацию дат в SQL:

```python
from datetime import timedelta, datetime, timezone

@router.get("/followup")
def get_followup_queue(db: Session = Depends(get_db), ...):
    now = datetime.now(timezone.utc)

    q = (
        db.query(CompanyRow, EnrichedCompanyRow, CrmContactRow)
        .outerjoin(...)
        .join(CrmContactRow, ...)
        .filter(
            CrmContactRow.funnel_stage.in_(list(STAGE_NEXT_ACTION.keys())),
            CrmContactRow.stop_automation == 0,
        )
    )

    # SQL-фильтрация: для каждой стадии — свой cutoff
    conditions = []
    for stage, rule in STAGE_NEXT_ACTION.items():
        if rule["days"] == 0:
            # Стадия "new": показать только если нет касаний
            conditions.append(
                (CrmContactRow.funnel_stage == stage) & (CrmContactRow.last_contact_at.is_(None))
            )
        else:
            cutoff = now - timedelta(days=rule["days"])
            conditions.append(
                (CrmContactRow.funnel_stage == stage) &
                (CrmContactRow.last_contact_at < cutoff)
            )
    from sqlalchemy import or_
    q = q.filter(or_(*conditions))

    if city:
        q = q.filter(CompanyRow.city == city)

    rows = q.all()  # Теперь в памяти только релевантные записи
    # ... остальная логика fallback каналов без изменений ...
```

**Ожидаемый эффект:** при 5000 компаний и 100 в очереди — запрос вернёт ~100 строк вместо 5000. Экономия памяти ~50x.

#### 3.6 [ВЫСОКИЙ] Массивы в фильтрах

> Приоритет: **высокий** (повышен с «средний» в v4). Multi-select по `funnel_stage` и `status` — ключевая UX-фича для таблиц компаний и задач.

`GET /api/v1/companies` и `GET /api/v1/tasks` — поддержка массивов в query-параметрах:

```python
# companies.py
from typing import List
from fastapi import Query

@router.get("/companies")
def list_companies(
    funnel_stage: Optional[List[str]] = Query(None),  # было: Optional[str]
    ...
):
    if funnel_stage:
        q = q.filter(CrmContactRow.funnel_stage.in_(funnel_stage))
```

Формат запроса: `?funnel_stage=new&funnel_stage=email_sent`.

Аналогично для `GET /tasks?status=pending&status=in_progress`.

### 🟢 Средние (опциональные, для полноты)

#### 3.7 [СРЕДНИЙ] Индивидуальный Email-API

> Приоритет: средний. Нужен для кнопки "Отправить email" в Company Detail Panel (Фаза 2b / 7). До реализации — email-отправка только через кампании.

`POST /api/v1/companies/{id}/send-email` — отправка email конкретной компании вне кампании.

Тело: `{subject: str, body: str, template_name?: str}`.

Логика: взять первый email из `emails[]`, рендерить шаблон (если `template_name` указан), отправить через `granite.email.sender.EmailSender`, создать `CrmEmailLogRow`, создать `CrmTouchRow`, обновить `CrmContactRow` счётчики и стадию (через `apply_outgoing_touch`).

#### 3.7a [СРЕДНИЙ] GET /stream для кампаний (нативный EventSource)

> Приоритет: средний (новое в v5.1). Позволяет использовать нативный `EventSource` браузера вместо `@microsoft/fetch-event-source`. Убирает одну фронтенд-зависимость.

Добавить в `campaigns.py`:

```python
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse  # или ручной generator

@router.get("/campaigns/{campaign_id}/stream")
async def stream_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(CrmEmailCampaignRow, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if campaign.status != "running":
        raise HTTPException(409, f"Campaign is '{campaign.status}', not 'running'")

    async def event_generator():
        # Переиспользуем тот же generator, что и POST /run
        # Проверяем очередь Redis / in-memory на предмет новых событий
        ...

    return EventSourceResponse(event_generator())
```

> **Примечание:** реализация зависит от того, как текущий `run_campaign` стримит события. Если используется in-memory очередь или polling БД — генератор будет опрашивать её. Если `sse_starlette` не установлен — можно использовать `StreamingResponse` с ручным форматированием `data: ...\n\n`.

```python
@router.post("/companies/{company_id}/send-email")
def send_email(company_id: int, data: SendEmailRequest, db: Session = Depends(get_db)):
    company = db.get(CompanyRow, company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    emails = company.emails or []
    if not emails:
        raise HTTPException(400, "No email for this company")

    # Рендер шаблона или прямой текст
    if data.template_name:
        template = db.query(CrmTemplateRow).filter_by(name=data.template_name).first()
        if not template:
            raise HTTPException(404, f"Template not found: {data.template_name}")
        body = template.render(from_name=..., city=...)
        subject = template.render_subject(...) or data.subject
    else:
        body, subject = data.body, data.subject

    sender = EmailSender()
    tracking_id = sender.send(
        company_id=company_id, email_to=emails[0],
        subject=subject, body_text=body, template_name=data.template_name or "",
        db_session=db,
    )

    # Обновить CRM-контакт
    contact = db.get(CrmContactRow, company_id)
    if contact and tracking_id:
        apply_outgoing_touch(contact, "email")
        db.add(CrmTouchRow(
            company_id=company_id, channel="email", direction="outgoing",
            subject=subject, body=f"[tracking_id={tracking_id}]",
        ))

    return {"ok": bool(tracking_id), "tracking_id": tracking_id}
```

#### 3.8 [СРЕДНИЙ] `has_whatsapp` фильтр

> Приоритет: низкий (без изменений). Добавить в `GET /api/v1/companies`:

```python
has_whatsapp: Optional[int] = Query(None)
# → EnrichedCompanyRow.messengers (JSON) содержит ключ "whatsapp"
```

Реализация аналогична `has_telegram` (companies.py:81-84): `EnrichedCompanyRow.messengers.cast(String).contains('"whatsapp"')`.

#### 3.9 [СРЕДНИЙ] Агрегирующий `/stats` endpoint

> Приоритет: **повышен до критического в v5.1**. Нужен для Dashboard (Фаза 1) — без него пришлось бы делать 3 параллельных запроса (`/companies?per_page=1`, `/tasks?per_page=1`, `/funnel`). Реализация занимает ~10 минут, поэтому рекомендуется сделать до старта Dashboard.

```
GET /api/v1/stats → {
  "companies_total": 1500,
  "tasks_total": 42,
  "tasks_pending": 15,
  "campaigns_total": 5,
  "campaigns_completed": 3
}
```

Реализация через `func.count()` без пагинации — лёгкий запрос:

```python
@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func
    companies_total = db.query(func.count(CompanyRow.id)).scalar()
    tasks_total = db.query(func.count(CrmTaskRow.id)).scalar()
    tasks_pending = db.query(func.count(CrmTaskRow.id)).filter_by(status="pending").scalar()
    campaigns_total = db.query(func.count(CrmEmailCampaignRow.id)).scalar()
    campaigns_completed = db.query(func.count(CrmEmailCampaignRow.id)).filter_by(status="completed").scalar()
    return {
        "companies_total": companies_total,
        "tasks_total": tasks_total,
        "tasks_pending": tasks_pending,
        "campaigns_total": campaigns_total,
        "campaigns_completed": campaigns_completed,
    }
```

Без этого Dashboard делает 3 запроса (`/companies?per_page=1`, `/tasks?per_page=1`, `/funnel`). С `/stats` — 2 запроса (`/stats` + `/funnel`).

#### 3.10 [СРЕДНИЙ] Health check с проверкой БД

> Приоритет: средний (новое в v5). Текущий `GET /health` (app.py:87-89) возвращает `{"status": "ok"}` без проверки соединения с БД. UI-индикатор подключения опирается на этот endpoint — при падении БД индикатор будет показывать "connected", хотя API неработоспособен.

```python
from fastapi.responses import JSONResponse

@app.get("/health")
def health():
    try:
        from sqlalchemy import text
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return JSONResponse(
            {"status": "error", "database": str(e)},
            status_code=503
        )
```

> **v5.1 исправление:** FastAPI не поддерживает возврат кортежа `(body, status_code)` из обычной функции — это паттерн Flask. Нужно использовать `JSONResponse` из `fastapi.responses`. Временная альтернатива — `Response(content=json.dumps(...), status_code=503, media_type="application/json")`.

#### 3.11 [СРЕДНИЙ] Обновление seed-скрипта шаблонов

> Приоритет: средний (новое в v5). Текущие шаблоны в `scripts/seed_crm_templates.py` устарели: нет ссылки на сайт, нет цен, короткий текст. Новые шаблоны описаны в `update-templates-tasktypes.md`.

**Изменения в `scripts/seed_crm_templates.py`:**

1.  **Полная замена массива `TEMPLATES`** на обновлённые шаблоны (ссылка на `monument-web`, цены, сроки, оффер бесплатного теста, партнёрские условия).
2.  **Изменение логики seed:** текущая логика пропускает существующие шаблоны (`if t["name"] not in existing`). Нужно заменить на **UPDATE**:

```python
def seed_crm_templates():
    db = Database()
    with db.session_scope() as session:
        updated = 0
        inserted = 0
        for t in TEMPLATES:
            existing = session.query(CrmTemplateRow).filter_by(name=t["name"]).first()
            if existing:
                existing.channel = t["channel"]
                existing.subject = t["subject"]
                existing.body = t["body"]
                existing.description = t["description"]
                updated += 1
            else:
                session.add(CrmTemplateRow(**t))
                inserted += 1
        logger.info(f"SEED crm_templates: обновлено {updated}, создано {inserted}")
    db.engine.dispose()
    return updated, inserted
```

---

## 4. Funnel State Machine

Реализовано в `granite/api/stage_transitions.py`.

> **v5 исправление:** таблица исходящих переходов обновлена в соответствии с реальным кодом. В v4 WA-отправка указывалась только со стадии `tg_sent`, но код (строки 31-32) допускает WA с **любой** стадии, кроме финальных (`replied`, `interested`, `not_interested`).

```
new → email_sent → email_opened → tg_sent → wa_sent → replied → interested
                                                                      ↘ not_interested
                                                                      ↘ unreachable
```

**Исходящие касания (outgoing) — актуальный код `stage_transitions.py`:**

| Текущая стадия | `email` | `tg` | `wa` |
|---|---|---|---|
| `new` | → `email_sent` | → `tg_sent` | → `wa_sent` |
| `email_sent` | — (только "new"→email) | → `tg_sent` | → `wa_sent` |
| `email_opened` | — | → `tg_sent` | → `wa_sent` |
| `tg_sent` | — | — | → `wa_sent` |
| `wa_sent` | — | — | — (уже `wa_sent`) |
| `replied` / `interested` / `not_interested` / `unreachable` | — | — | — |

> **v5 примечание:** WA-переход (строка 31: `if contact.funnel_stage not in ("replied", "interested", "not_interested")`) технически позволяет WA-отправку с `email_sent`, `email_opened`, `new` и даже `wa_sent` (хотя с `wa_sent` это бессмысленно). Это отличается от v4, где указывалось только `tg_sent → wa_sent`. Код является более перmissive — рекомендуется либо зафиксировать это поведение, либо ограничить переходы, если это нежелательно.

**Входящие касания (incoming):** любая стадия (кроме `interested` / `not_interested`) → `replied`. Побочный эффект: `stop_automation = 1`.

**Follow-up правила (бэкенд `followup.py:16-22`):**

| Стадия | Дней ожидания | Канал | Шаблон | Действие |
|---|---|---|---|---|
| `new` | 0 | email | `cold_email_1` | Отправить холодное письмо |
| `email_sent` | 4 | tg | `tg_intro` | Написать в Telegram |
| `email_opened` | 2 | tg | `tg_intro` | Написать в TG (открыл письмо!) |
| `tg_sent` | 4 | wa | `wa_intro` | Написать в WhatsApp |
| `wa_sent` | 7 | email | `follow_up_email` | Финальное письмо |

Fallback каналов: `tg` → `wa` → `channel_available = false`.

---

## 5. SSE Events Reference

**Endpoint:** `GET /api/v1/campaigns/{campaign_id}/stream` (v5.1: нативный EventSource, без POST)
**Content-Type:** `text/event-stream`

> **v5.1:** вместо POST-запроса через `@microsoft/fetch-event-source` — используется отдельный GET-endpoint `/stream`. Это позволяет применить нативный `EventSource` браузера без дополнительных библиотек. POST `/run` остаётся для запуска кампании, GET `/stream` — для подписки на прогресс.

```
data: {"status": "started", "total": 42}

data: {"sent": 1, "total": 42, "current": "shop1@mail.ru"}

data: {"sent": 2, "total": 42, "current": "shop2@mail.ru"}

...

data: {"status": "completed", "sent": 42, "total": 42}
```

Ошибки (не прерывают stream):
```
data: {"error": "Campaign not found"}
data: {"error": "Already running"}
data: {"error": "Template 'xxx' not found"}
```

**Хук `use-sse.ts`:**
```typescript
interface CampaignSSECallbacks {
  onProgress: (sent: number, total: number, current: string) => void;
  onComplete: (sent: number, total: number) => void;
  onError: (message: string) => void;
}

function useCampaignSSE(
  campaignId: number | null,
  callbacks: CampaignSSECallbacks
): { isRunning: boolean; cancel: () => void }
```

> **v5.1:** используется нативный `EventSource` (GET `/campaigns/{id}/stream`). Запуск кампании: `POST /run`, после получения ответа — открыть `EventSource` на `/stream`. При `cancel()` — закрыть EventSource (бэкенд пометит кампанию как `paused`). Автопереподключение EventSource при разрыве — бесплатно.

---

## 6. TypeScript Types

```typescript
// ===== Funnel =====
type FunnelStage =
  | 'new' | 'email_sent' | 'email_opened'
  | 'tg_sent' | 'wa_sent'
  | 'replied' | 'interested' | 'not_interested' | 'unreachable';

const FUNNEL_STAGES: FunnelStage[] = [
  'new', 'email_sent', 'email_opened', 'tg_sent', 'wa_sent',
  'replied', 'interested', 'not_interested', 'unreachable',
];

const FUNNEL_STAGE_LABELS: Record<FunnelStage, string> = {
  new: 'Новые',
  email_sent: 'Email отправлен',
  email_opened: 'Email открыт',
  tg_sent: 'TG отправлено',
  wa_sent: 'WA отправлено',
  replied: 'Ответили',
  interested: 'Заинтересованы',
  not_interested: 'Не заинтересованы',
  unreachable: 'Недоступны',
};

interface FunnelCounts {
  new: number;
  email_sent: number;
  email_opened: number;
  tg_sent: number;
  wa_sent: number;
  replied: number;
  interested: number;
  not_interested: number;
  unreachable: number;
}

// ===== Company =====
interface Company {
  id: number;
  name: string;
  phones: string[];
  website: string | null;
  emails: string[];
  city: string | null;
  segment: string | null;
  crm_score: number | null;
  cms: string | null;
  has_marquiz: boolean | null;
  is_network: boolean | null;
  telegram: string | null;
  whatsapp: string | null;
  vk: string | null;
  messengers: Record<string, string>;
  tg_trust: Record<string, unknown>;
  funnel_stage: FunnelStage | null;
  email_sent_count: number;
  email_opened_count: number;
  tg_sent_count: number;
  wa_sent_count: number;
  last_contact_at: string | null;
  notes: string | null;
  stop_automation: boolean;
}

interface CompanyListResponse {
  items: Company[];
  total: number;
  page: number;
  per_page: number;
}

interface CompanyFilters {
  city?: string;
  segment?: string;
  funnel_stage?: FunnelStage | FunnelStage[];  // [] после п. 3.6
  has_telegram?: 0 | 1;
  has_email?: 0 | 1;
  has_whatsapp?: 0 | 1;          // после п. 3.8
  min_score?: number;
  search?: string;
  page?: number;
  per_page?: number;
  order_by?: 'crm_score' | 'name_best' | 'city' | 'funnel_stage';
  order_dir?: 'asc' | 'desc';
}

interface UpdateCompanyRequest {
  funnel_stage?: FunnelStage;
  notes?: string;
  stop_automation?: boolean;
}

// ===== Touch =====
type TouchChannel = 'email' | 'tg' | 'wa' | 'manual';
type TouchDirection = 'outgoing' | 'incoming';

interface Touch {
  id: number;
  channel: TouchChannel;
  direction: TouchDirection;
  subject: string;
  body: string;
  note: string;
  created_at: string;
}

// ===== Task =====
type TaskType = 'follow_up' | 'send_portfolio' | 'send_test_offer' | 'check_response' | 'other';

const TASK_TYPE_LABELS: Record<TaskType, string> = {
  follow_up: 'Follow-up',
  send_portfolio: 'Отправить портфолио',
  send_test_offer: 'Предложить тест',
  check_response: 'Проверить ответ',
  other: 'Другое',
};

type TaskPriority = 'low' | 'normal' | 'high';
type TaskStatus = 'pending' | 'in_progress' | 'done' | 'cancelled';

interface Task {
  id: number;
  company_id: number;
  company_name: string | null;    // после п. 3.2 (JOIN). null если компания удалена.
  title: string;
  task_type: TaskType;
  priority: TaskPriority;
  status: TaskStatus;
  due_date: string | null;
  created_at: string;
}

interface TaskListResponse {
  items: Task[];
  total: number;
  page: number;
  per_page: number;
}

interface CreateTaskRequest {
  title: string;               // v5.1: обязательное. Бэкенд требует title (min_length=1),
                              // хотя имеет default="Follow-up". Фронтенд НЕ должен отправлять без title.
  description?: string;
  due_date?: string;
  priority?: TaskPriority;
  task_type?: TaskType;
}

interface UpdateTaskRequest {
  status?: TaskStatus;
  priority?: TaskPriority;
  title?: string;
  task_type?: TaskType;    // добавлено в v5
}

// ===== Campaign =====
type CampaignStatus = 'draft' | 'running' | 'completed' | 'paused';

interface Campaign {
  id: number;
  name: string;
  template_name: string;
  status: CampaignStatus;
  filters: Record<string, unknown>;
  total_sent: number;
  total_opened: number;
  total_replied: number;
  open_rate?: number;
  created_at: string;
}

interface CampaignStats {
  id: number;
  name: string;
  status: CampaignStatus;
  total_sent: number;
  total_opened: number;
  total_replied: number;
  open_rate: number;
}

interface CreateCampaignRequest {
  name?: string;
  template_name?: string;
  filters?: {
    city?: string;
    segment?: string;
    min_score?: number;
  };
}

// ===== Follow-up =====
interface FollowupItem {
  company_id: number;
  name: string;
  city: string | null;
  funnel_stage: FunnelStage;
  days_since_last_contact: number;
  recommended_channel: 'email' | 'tg' | 'wa';
  channel_available: boolean;
  template_name: string;
  action: string;
  telegram: string | null;
  whatsapp: string | null;
  emails: string[];
  crm_score: number | null;
  segment: string | null;
}

// ===== Messenger =====
type SendChannel = 'tg' | 'wa';   // после п. 3.7: 'tg' | 'wa' | 'email'

interface SendMessageRequest {
  channel: SendChannel;
  template_name?: string;
  text?: string;
}

interface SendMessageResponse {
  ok: boolean;
  channel: string;
  contact_id: string | null;
  error: string | null;
}

// ===== Template =====
interface Template {
  name: string;
  channel: 'email' | 'tg' | 'wa';
  subject: string | null;
  body: string;
  description?: string;
  // variables — НЕ поле из API. Вычисляется на фронте:
  // const variables = useMemo(() => {
  //   const matches = template.body.match(/\{(\w+)\}/g);
  //   return matches ? [...new Set(matches)] : [];
  // }, [template.body]);
  created_at?: string;
  updated_at?: string;
}

// ===== Stats (после п. 3.9) =====
interface DashboardStats {
  companies_total: number;
  tasks_total: number;
  tasks_pending: number;
  campaigns_total: number;
  campaigns_completed: number;
}
```

---

## 7. Сводная оценка компонентов

| Роут | API Calls | UI Компоненты | Сложность |
|---|---|---|---|
| `/dashboard` | `GET /funnel`, `GET /stats` | KPI Cards, Funnel Chart, Recent Table | Средняя |
| `/companies` | `GET /companies`, `GET /companies/{id}`, `PATCH`, `GET /touches`, `POST /tasks`, `POST /send`, `POST /send-email` (п. 3.7) | Datatable, Filters Bar, Side Panel, Tabs | Высокая |
| `/tasks` | `GET /tasks` (с company_name JOIN), `POST /tasks`, `PATCH /tasks/{id}`, `DELETE /tasks/{id}` | Datatable, Checkbox, Dialog | Средняя |
| `/campaigns` | `GET /campaigns`, `POST /campaigns`, `GET /{id}`, `POST /{id}/run`, `GET /{id}/stream` (SSE), `GET /{id}/stats` | List, Dialog, Progress Bar, Stats Cards | Высокая |
| `/followup` | `GET /followup`, `POST /send` | List, Batch Send (sequential), Warning Alert | Средняя |
| `/templates` | `GET/POST/PATCH/DELETE /templates` (п. 3.1) | Grid/List, Editor, Variable Highlight, Preview | Средняя |

---

## 8. Запуск среды разработки

```bash
# Фронтенд (Next.js)
NEXT_PUBLIC_CRM_API_URL=http://localhost:8000 bun run dev

# Бэкенд (FastAPI)
python cli.py api
# или
uvicorn granite.api.app:app --reload --port 8000
```

---

## 9. Порядок работы

```
БЭКЕНД (параллельно)          ФРОНТЕНД (последовательно)
─────────────────────         ─────────────────────────

┌─ КРИТИЧЕСКИЕ (до Фазы 1) ──────────────────────────────────┐
│ п. 3.1 Templates CRUD          (блокирует Фазу 4, 6)         │
│ п. 3.2 company_name JOIN       (блокирует Фазу 3)           │
│ п. 3.3 Task types update       (блокирует Фазу 3, 7)        │
│ п. 3.4 Search SQL fix          (безопасность)               │
│ п. 3.9 /stats endpoint         (блокирует Фазу 1 Dashboard) │
└──────────────────────────────────────────────────────────────┘

                              Фаза 0: Фундамент
                              Фаза 1: Dashboard
                              │
┌─ ВАЖНЫЕ (до Фазы 2-3) ────────────────────────────────────┐
│ п. 3.5 Follow-up SQL optimize                               │
│ п. 3.6 Array filters                                        │
└──────────────────────────────────────────────────────────────┘
                              ├─ Фаза 2: Компании
                              ├─ Фаза 3: Задачи
                              ├─ Фаза 4: Кампании
                              │
┌─ СРЕДНИЕ (по мере необходимости) ──────────────────────────┐
│ п. 3.7  Individual email API   (Фаза 7)                     │
│ п. 3.8  has_whatsapp filter    (Фаза 2a — доп. фильтр)     │
│ п. 3.10 Health check + DB      (надёжность индикатора)      │
│ п. 3.11 Seed templates update  (данные для UI)              │
│ + GET /campaigns/{id}/stream   (нативный EventSource, п. 5) │
└──────────────────────────────────────────────────────────────┘
                              ├─ Фаза 5: Follow-up
                              ├─ Фаза 6: Шаблоны
                              ├─ Фаза 7: Дополнения
                              └─ Фаза 8: Полировка
```

**Блокировки:**
- **Фаза 1** (Dashboard) частично заблокирована до п. 3.9 (`/stats` endpoint) — без него нужны 3 запроса вместо 2.
- **Фаза 6** (Templates UI) полностью заблокирована до п. 3.1 (Templates CRUD endpoint).
- **Фаза 3** (Tasks) частично заблокирована до п. 3.2 (company_name JOIN) — без него таблица задач не показывает название компании.
- **Фаза 4** (Campaigns) частично заблокирована до п. 3.1 — Select шаблонов при создании кампании требует `GET /templates`.
- Остальные фазы можно начинать без бэкенд-доработок (с ограничениями функциональности).

**Рекомендуемый порядок бэкенд-работ:**
1. п. 3.1 + 3.2 + 3.3 + 3.4 + 3.9 — параллельно, ~2.5 часа суммарно. Это разблокирует все критические UI-функции и Dashboard.
2. п. 3.5 + 3.6 — параллельно, ~1 час. Улучшения производительности и UX.
3. п. 3.7–3.11 + GET /stream endpoint — по мере необходимости, не блокируют UI.
