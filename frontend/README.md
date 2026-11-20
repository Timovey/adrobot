# Adrobot frontend (Vue 3 + TypeScript + Vite)

SPA for campaign creator & stream offer editor. API calls go to `/api/*` (proxied to FastAPI in dev).

## Requirements

Node.js 20.19+ or 22.12+ (Vite 8). Run the commands below from `frontend/`.

## Scripts

```bash
npm install
npm run dev        # http://127.0.0.1:5173 — proxies /api → :8000
npm run typecheck  # vue-tsc --build (strict)
npm run build      # typecheck + vite build → dist/ (served by FastAPI at /)
```

## Layout

- `src/types.ts` — request/response types mirroring `app/schemas.py`.
- `src/api.ts` — typed endpoint wrappers, error formatting (`ApiError` with FastAPI `detail`),
  optional auth token (`Authorization: Bearer <token>` from the Settings input / localStorage,
  or `VITE_ADROBOT_API_TOKEN` at build time).

In dev, start the backend first (`uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` from the
repo root); the proxy target is configured in `vite.config.ts`. Without `npm run build`, FastAPI
returns 503 on `/` (use the Vite dev server instead). After the first build, restart uvicorn so `/assets` is mounted.
If the backend sets `ADROBOT_API_TOKEN`, enter it under **Settings** in the header (or set
`VITE_ADROBOT_API_TOKEN` in `.env.local` and rebuild).

See the repo root `README.md` for full backend + frontend run instructions.
