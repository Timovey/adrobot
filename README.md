# Adrobot — помощник для кампаний Keitaro

Помощник на FastAPI + SQLAlchemy для [Keitaro](https://docs.keitaro.io/en/development/admin-api.html) с UI на **Vue 3**:

1. **Создание кампаний** — название + ISO-код гео + ID оффера → кампания с 2 потоками (гео → Google, запасной → оффер); домен / группа / источник трафика берутся из API.
2. **Редактор кампаний** — загрузка потоков, добавление/удаление/возврат офферов, закрепление весов, публикация/отмена неопубликованных изменений (логика из видео к ТЗ).

## Установка

```bash
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Требуется Python 3.10+ (разработка велась на 3.12). Отредактируйте `.env` и задайте `KEITARO_URL` и `KEITARO_API_KEY`
(Account → API keys в админке Keitaro: https://tlgk.host/admin). `ADROBOT_API_TOKEN` и `DATABASE_URL`
необязательны (см. [Конфигурация](#конфигурация-env)).

Все команды бэкенда (`uvicorn`, `pytest`) запускайте **из корня репозитория**: `.env` и путь SQLite по умолчанию
`./data/adrobot.db` определяются относительно текущей рабочей директории.
Команда запуска бэкенда приведена в разделе [Запуск](#запуск).

Фронтенд (Vue 3 + TypeScript + Vite; для Vite 8 нужен Node.js 20.19+ или 22.12+):

```bash
cd frontend
npm install
npm run typecheck   # vue-tsc (strict) — also runs as part of npm run build
npm run build       # typecheck + vite build → frontend/dist (served by FastAPI at /)
```

Необязательный API token: если бэкенд настроен с токеном, UI отправляет его как
`Authorization: Bearer <token>`. Токен можно вставить в UI (кнопка **Настройки** в шапке;
хранится в localStorage браузера) или зашить при сборке через
`VITE_ADROBOT_API_TOKEN=<token>` (например, в `frontend/.env.local`; требуется пересборка — учтите, что тогда он
попадает в публичный JS-бандл). Токен, сохранённый в UI, имеет приоритет.
Имя и формат заголовка определены в одном месте: `frontend/src/api.ts`.

Значения по умолчанию для песочницы FORTESTS (можно переопределить в UI / `.env`):

| Настройка | Значение |
|-----------|----------|
| Домен | `1813` (`kylovoa.com`) |
| Группа | `10332` (`FORTESTS`) |
| Источник трафика | `836` (`FORTESTS`) |
| Префикс названия | `test_timovey_` |

### Конфигурация (`.env`)

| Переменная | По умолчанию | Примечания |
|------------|--------------|------------|
| `KEITARO_URL` / `KEITARO_API_KEY` | — | Keitaro Admin API; ключ никогда не покидает сервер и не попадает в логи |
| `KEITARO_TIMEOUT_SECONDS` | `60` | Таймаут на один запрос к Keitaro |
| `ADROBOT_API_TOKEN` | пусто (авторизация выключена) | Если задан, см. [Аутентификация](#аутентификация) |
| `DATABASE_URL` | `sqlite:///./data/adrobot.db` | Любой URL SQLAlchemy (см. [База данных](#база-данных)) |
| `LOG_LEVEL` | `INFO` | `DEBUG` логирует каждый вызов Keitaro (метод, путь, статус, длительность) |
| `DEFAULT_DOMAIN_ID` / `DEFAULT_GROUP_ID` / `DEFAULT_TRAFFIC_SOURCE_ID` | не заданы в коде; `.env.example` задаёт значения FORTESTS | Предвыбранные значения по умолчанию для создания кампаний |
| `CAMPAIGN_NAME_PREFIX` | `test_timovey_` | Префикс названий создаваемых кампаний |
| `GOOGLE_REDIRECT_URL` | `https://www.google.com/` | Адрес редиректа потока гео → Google |

`KEITARO_URL` по умолчанию равен `https://tlgk.host`, а `KEITARO_API_KEY` — пустой; без ключа каждый
маршрут, обращающийся к Keitaro, возвращает 500 `KEITARO_API_KEY is not set in .env`.
Настройки читаются один раз при старте — перезапустите uvicorn после правки `.env`.

### Аутентификация

API хранит рабочий ключ Keitaro, поэтому по умолчанию привязывается к `127.0.0.1`. Чтобы защитить его дополнительно
(или перед тем, как открывать за пределы localhost), задайте токен в `.env`:

```
ADROBOT_API_TOKEN=some-long-random-string
```

Если токен задан, каждый маршрут `/api/*`, кроме `GET /api/health`, требует **один** из вариантов:

- `Authorization: Bearer <token>`
- `X-API-Key: <token>`

Отсутствующий/неверный токен → `401 {"detail": "Missing or invalid API token"}` с заголовком `WWW-Authenticate: Bearer`.
`GET /api/health` остаётся публичным и возвращает `"auth_required": true|false`, чтобы UI мог запросить токен.
SPA (`/`, `/assets`) не защищено.

### База данных

Локальная БД — лишь зеркало Keitaro плюс неопубликованные черновики. По умолчанию используется SQLite;
Postgres тоже работает через `DATABASE_URL` (драйвер `psycopg` **необязателен** и не входит в
`requirements.txt`; устанавливайте его, только если переключаетесь):

```bash
pip install "psycopg[binary]"
# .env
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/adrobot
```

- **Расположение (SQLite):** `data/adrobot.db` относительно директории, из которой запущен uvicorn
  (запускайте из корня репозитория). Папка `data/` создаётся автоматически; `*.db` находится в git-ignore.
- **Таблицы:** создаются автоматически при каждом запуске (`init_db()` в lifespan FastAPI).
- **Миграции:** **Alembic не используется, ручного шага миграции нет.** Единственная миграция —
  крошечная встроенная в `app/database.py`, выполняется при старте до создания таблиц: если
  в `streams` есть колонка `schema` и нет колонки `stream_schema`, один раз выполняется
  `ALTER TABLE streams RENAME COLUMN "schema" TO stream_schema` (идемпотентно; работает в SQLite
  и Postgres). Просто обновите код и перезапустите.
- **Ограничения:** `create_all` создаёт только *отсутствующие таблицы*; он никогда не добавляет колонки в существующие. Любое
  будущее изменение моделей, кроме переименования выше, потребует нового ручного шага в `_migrate()`
  или новой базы данных.
- **Сброс:** БД — лишь локальное зеркало Keitaro плюс неопубликованные черновики, поэтому удаление
  `data/adrobot.db` (при остановленном сервере) безопасно — кампании заново отзеркалируются из Keitaro через
  *Загрузить потоки*. Теряются только неопубликованные локальные черновики.

Примечание: `pytest` никогда не трогает вашу реальную базу данных. `tests/conftest.py` направляет `DATABASE_URL` на
временный одноразовый файл SQLite (удаляется после прогона) до импорта приложения, поэтому `init_db()` в lifespan приложения
работает с этим файлом и прерывает работу, если engine — не тестовая БД. Это относится и к live-тестам.

### Логирование

Логи приложения пишутся в логгеры `adrobot.*` (в stderr, если ничего другого не настроено): создание / публикация /
загрузка / отмена кампаний, ошибки upstream и ошибки **ORPHANED** с ID кампании/потока Keitaro,
если неудавшееся создание не удалось откатить на стороне Keitaro.

## Запуск

### В стиле production (UI отдаёт FastAPI)

Соберите Vue-приложение (`npm run build` сначала проверяет типы через `vue-tsc`, затем запускает `vite build`), затем запустите uvicorn. FastAPI отдаёт `frontend/dist` по адресам `/` и `/assets`.

```bash
cd frontend
npm run build
cd ..
.\.venv\Scripts\activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Откройте http://127.0.0.1:8000

Если UI ещё не собран, `/` возвращает HTTP 503 с инструкцией по сборке (`/api/*` при этом работает, а
`/favicon.svg` возвращает 404). `/assets` монтируется, только если `frontend/dist/assets` существует на момент запуска uvicorn, поэтому после
первого `npm run build` один раз перезапустите uvicorn (последующие пересборки подхватываются без перезапуска).
`--reload` отслеживает только Python-файлы; для запуска не в режиме разработки уберите его.

### Разработка (Vite + прокси к API)

Запускайте API и Vite отдельно. Vite проксирует `/api` → `http://127.0.0.1:8000`.

Терминал 1 — бэкенд:

```bash
.\.venv\Scripts\activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Терминал 2 — фронтенд:

```bash
cd frontend
npm install   # first time only
npm run dev
```

Адрес прокси (`http://127.0.0.1:8000`) задан в `frontend/vite.config.ts`; измените его там, если API работает на другом порту.
Если на бэкенде задан `ADROBOT_API_TOKEN`, вставьте его через **Настройки** в UI (или задайте `VITE_ADROBOT_API_TOKEN` в `frontend/.env.local` и перезапустите `npm run dev`).

Откройте http://127.0.0.1:5173

## API (основное)

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/campaigns` | Создать кампанию (2 потока) |
| GET | `/api/campaigns` | Локальные кампании |
| GET | `/api/campaigns/{keitaro_id}` | Детали кампании |
| POST | `/api/campaigns/{keitaro_id}/fetch-streams` | Синхронизировать потоки из Keitaro |
| POST | `/api/streams/{local_stream_id}/offers` | Добавить оффер (пересчитать доли) |
| DELETE | `/api/streams/{local_stream_id}/offers/{offer_id}` | Мягко удалить оффер |
| POST | `/api/streams/{local_stream_id}/offers/{offer_id}/bring-back` | Восстановить удалённый оффер |
| POST | `/api/streams/{local_stream_id}/offers/{offer_id}/pin` | Закрепить/открепить вес |
| POST | `/api/streams/{local_stream_id}/push` | Опубликовать офферы в Keitaro |
| POST | `/api/streams/{local_stream_id}/cancel` | Отбросить изменения черновика |

Ошибки возвращаются как JSON `{"detail": "..."}`. Локальные ошибки (неизвестный поток/оффер, неверный ввод) используют
400/404; ошибки Keitaro 400/404 пробрасываются как есть, а Keitaro 401/403/5xx, сетевые ошибки и
таймауты превращаются в **502**.

## Тесты

Установите зависимости (включая pytest), затем из корня репозитория с активным venv:

```bash
# Unit + mocked API tests only (default; skips live Keitaro)
pytest

# Live Keitaro verification (needs KEITARO_API_KEY in .env)
# WARNING: creates REAL campaigns (test_timovey_pytest_*) in the Keitaro instance from .env,
# then archives (soft-deletes) them. Use a sandbox account.
$env:RUN_LIVE_TESTS="1"; pytest -m live

# Everything (also runs live tests — same warning)
$env:RUN_LIVE_TESTS="1"; pytest
```

Команды выше — для PowerShell. В cmd используйте `set RUN_LIVE_TESTS=1`, а в bash — `RUN_LIVE_TESTS=1 pytest -m live`.
Без `RUN_LIVE_TESTS=1` live-тесты пропускаются, даже с `-m live`.

Live-тесты архивируют (мягко удаляют) каждую созданную ими кампанию и в конце прогона выводят ID и состояние очистки.
Сценарий редактора с несколькими офферами пропускается, если в Keitaro меньше 2 активных офферов.
Unit-тесты игнорируют `.env` (включая `ADROBOT_API_TOKEN`); live-тесты отправляют токен, если он задан.

| Набор | Путь | Сеть |
|-------|------|------|
| Unit-тесты весов / именования | `tests/test_weights.py`, `tests/test_naming.py` | нет |
| FastAPI TestClient + фейковый Keitaro | `tests/test_api.py`, `tests/test_sync.py`, `tests/test_create_compensation.py` | нет |
| Маппинг ошибок, авторизация, миграция БД | `tests/test_errors_auth.py`, `tests/test_database.py` | нет |
| Live: создание / синхронизация / компенсация | `tests/test_live_keitaro.py` (`@pytest.mark.live`) | реальный Keitaro |

## Структура проекта

```
app/
  main.py              # FastAPI entry (API + serves Vue build)
  config.py            # .env settings
  errors.py            # AppError (local errors; KeitaroError is upstream-only)
  database.py / models.py / schemas.py
  services/
    keitaro.py         # Admin API client
    campaigns.py       # creator + editor logic
    weights.py         # equal share redistribution with pins
tests/                 # pytest (unit, mocked API, optional live)
frontend/              # Vue 3 + TypeScript + Vite SPA
  src/
    App.vue
    main.ts
    api.ts             # typed API client (+ optional auth token header)
    types.ts           # TS mirrors of app/schemas.py
    components/        # creator, editor, streams, offer picker, token settings
  dist/                # production build (npm run build)
data/                  # SQLite DB (created at runtime)
```

## Правила потоков при создании

1. **Гео → Google** — `type=regular`, `schema=redirect`, фильтр по стране `accept` для заданного ISO-кода, HTTP-редирект на Google.
2. **Офферы** — `type=default`, `schema=landings`, выбранный оффер с долей 100%.

Создание сначала проверяет ввод (гео, оффер, значения по умолчанию), затем создаёт кампанию и оба потока
в Keitaro, затем сохраняет локально. Если любой шаг не удался, созданные потоки удаляются, а кампания
архивируется (или отключается как запасной вариант); если и это не удалось, оставшиеся ID логируются как `ORPHANED`.
Если Keitaro разрывает соединение, не ответив на вызов создания, кампания ищется по
своему уникальному alias и тоже откатывается.

## Правила редактора (из видео к ТЗ)

- Загрузка потоков; потоки без офферов отображаются как обычные потоки.
- Добавление оффера → равные доли между активными офферами; поток помечается как неопубликованный (жёлтый).
- Удаление → оффер уходит в локальный архив с 0% (серый) с кнопкой «Вернуть».
- Публикация → запись офферов/долей в Keitaro (всегда целые числа с суммой ровно 100); «Отменить» → отбросить черновик относительно Keitaro.
- Закрепление фиксирует долю, пока остальные перераспределяются до 100%.

### Семантика загрузки / черновика

У потока есть **настоящий черновик**, когда пользователь изменил его локально (`is_dirty`, выставляется только при
добавлении / удалении / возврате / закреплении) **и** его активные офферы или целые доли отличаются от Keitaro. При *Загрузить потоки*:

- Потоки с настоящим черновиком сохраняют свои локальные офферы нетронутыми (по-прежнему `is_dirty=true`).
- Все остальные потоки зеркалируют Keitaro: доли синхронизируются, офферы, исчезнувшие из Keitaro, становятся `removed`
  (0%, можно вернуть), новые удалённые офферы добавляются, а `is_dirty` становится `false`.
- Потоки, удалённые в Keitaro, удаляются и локально (включая любой черновик на них).
