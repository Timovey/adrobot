# Adrobot frontend (Vue 3 + TypeScript + Vite)

SPA для создания кампаний и редактирования офферов в потоках. Вызовы API идут на `/api/*` (в режиме разработки проксируются в FastAPI).

## Требования

Node.js 20.19+ или 22.12+ (Vite 8). Команды ниже запускайте из `frontend/`.

## Скрипты

```bash
npm install
npm run dev        # http://127.0.0.1:5173 — proxies /api → :8000
npm run typecheck  # vue-tsc --build (strict)
npm run build      # typecheck + vite build → dist/ (served by FastAPI at /)
```

## Структура

- `src/types.ts` — типы запросов/ответов, повторяющие `app/schemas.py`.
- `src/api.ts` — типизированные обёртки над endpoint, форматирование ошибок (`ApiError` с FastAPI `detail`),
  необязательный auth token (`Authorization: Bearer <token>` из поля Настройки / localStorage
  или `VITE_ADROBOT_API_TOKEN` при сборке).

В режиме разработки сначала запустите бэкенд (`uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` из
корня репозитория); адрес прокси настраивается в `vite.config.ts`. Без `npm run build` FastAPI
возвращает 503 на `/` (используйте вместо этого dev-сервер Vite). После первой сборки перезапустите uvicorn, чтобы смонтировался `/assets`.
Если бэкенд задаёт `ADROBOT_API_TOKEN`, введите его в **Настройки** в шапке (или задайте
`VITE_ADROBOT_API_TOKEN` в `.env.local` и пересоберите).

Полные инструкции по запуску бэкенда и фронтенда смотрите в `README.md` в корне репозитория.
