# Adrobot — Keitaro campaign helper

FastAPI + SQLAlchemy helper for [Keitaro](https://docs.keitaro.io/en/development/admin-api.html), with a **Vue 3** UI:

1. **Campaign creator** — name + geo ISO code + offer ID → campaign with 2 streams (geo → Google, fallback → offer), with domain / group / traffic source from API.
2. **Campaign editor** — fetch streams, add/remove/bring-back offers, pin weights, push/cancel unpublished changes (logic from the TZ video).

## Setup

```bash
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Requires Python 3.10+ (developed on 3.12). Edit `.env` and set `KEITARO_URL` and `KEITARO_API_KEY`
(Account → API keys in Keitaro admin: https://tlgk.host/admin). `ADROBOT_API_TOKEN` and `DATABASE_URL`
are optional (see [Configuration](#configuration-env)).

Run every backend command (`uvicorn`, `pytest`) **from the repo root**: `.env` and the default
SQLite path `./data/adrobot.db` are resolved relative to the current working directory.
The backend start command is in [Run](#run).

Frontend (Vue 3 + TypeScript + Vite; Node.js 20.19+ or 22.12+ required by Vite 8):

```bash
cd frontend
npm install
npm run typecheck   # vue-tsc (strict) — also runs as part of npm run build
npm run build       # typecheck + vite build → frontend/dist (served by FastAPI at /)
```

Optional API token: if the backend is configured with a token, the UI sends it as
`Authorization: Bearer <token>`. Either paste it in the UI (**Settings** button in the header;
stored in the browser's localStorage) or bake it in at build time with
`VITE_ADROBOT_API_TOKEN=<token>` (e.g. in `frontend/.env.local`; requires rebuild — note it is then
embedded in the public JS bundle). A token saved in the UI wins.
The header name/format lives in one place: `frontend/src/api.ts`.

Defaults used for FORTESTS sandbox (overridable in UI / `.env`):

| Setting | Value |
|---------|-------|
| Domain | `1813` (`kylovoa.com`) |
| Group | `10332` (`FORTESTS`) |
| Traffic source | `836` (`FORTESTS`) |
| Name prefix | `test_timovey_` |

### Configuration (`.env`)

| Variable | Default | Notes |
|----------|---------|-------|
| `KEITARO_URL` / `KEITARO_API_KEY` | — | Keitaro Admin API; the key never leaves the server and is never logged |
| `KEITARO_TIMEOUT_SECONDS` | `60` | Per-request timeout to Keitaro |
| `ADROBOT_API_TOKEN` | empty (auth off) | When set, see [Authentication](#authentication) |
| `DATABASE_URL` | `sqlite:///./data/adrobot.db` | Any SQLAlchemy URL (see [Database](#database)) |
| `LOG_LEVEL` | `INFO` | `DEBUG` logs every Keitaro call (method, path, status, duration) |
| `DEFAULT_DOMAIN_ID` / `DEFAULT_GROUP_ID` / `DEFAULT_TRAFFIC_SOURCE_ID` | unset in code; `.env.example` sets the FORTESTS values | Pre-selected defaults for the campaign creator |
| `CAMPAIGN_NAME_PREFIX` | `test_timovey_` | Prefix for created campaign names |
| `GOOGLE_REDIRECT_URL` | `https://www.google.com/` | Redirect target of the geo → Google stream |

`KEITARO_URL` defaults to `https://tlgk.host` and `KEITARO_API_KEY` to empty; without a key every
route that talks to Keitaro returns 500 `KEITARO_API_KEY is not set in .env`.
Settings are read once at startup — restart uvicorn after editing `.env`.

### Authentication

The API holds a live Keitaro key, so it binds to `127.0.0.1` by default. To protect it further
(or before exposing it beyond localhost), set a token in `.env`:

```
ADROBOT_API_TOKEN=some-long-random-string
```

When set, every `/api/*` route except `GET /api/health` requires **one** of:

- `Authorization: Bearer <token>`
- `X-API-Key: <token>`

Missing/wrong token → `401 {"detail": "Missing or invalid API token"}` with `WWW-Authenticate: Bearer`.
`GET /api/health` stays public and reports `"auth_required": true|false` so the UI can prompt for a token.
The SPA (`/`, `/assets`) is not protected.

### Database

The local DB is only a mirror of Keitaro plus unpublished drafts. SQLite is the default;
Postgres works too via `DATABASE_URL` (the driver `psycopg` is **optional** and not in
`requirements.txt`; install it only if you switch):

```bash
pip install "psycopg[binary]"
# .env
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/adrobot
```

- **Location (SQLite):** `data/adrobot.db` relative to the directory uvicorn is started from
  (start it from the repo root). The `data/` folder is created automatically; `*.db` is git-ignored.
- **Tables:** created automatically on every startup (`init_db()` in the FastAPI lifespan).
- **Migrations:** **Alembic is not used and there is no manual migration step.** The only migration
  is a tiny built-in one in `app/database.py` that runs at startup, before table creation: if
  `streams` has a `schema` column and no `stream_schema` column, it runs
  `ALTER TABLE streams RENAME COLUMN "schema" TO stream_schema` once (idempotent; works on SQLite
  and Postgres). Just upgrade the code and restart.
- **Limits:** `create_all` only creates *missing tables*; it never adds columns to existing ones. Any
  future model change other than the rename above needs a new hand-written step in `_migrate()`
  or a fresh database.
- **Resetting:** the DB is only a local mirror of Keitaro plus unpublished drafts, so deleting
  `data/adrobot.db` (with the server stopped) is safe — campaigns are re-mirrored from Keitaro via
  *Fetch streams*. Only unpublished local drafts are lost.

Note: `pytest` never touches your real database. `tests/conftest.py` points `DATABASE_URL` at a
throwaway temp SQLite file (deleted after the run) before the app is imported, so the app lifespan's
`init_db()` runs against that file, and aborts if the engine is not the test DB. This also applies to live tests.

### Logging

App logs go to the `adrobot.*` loggers (stderr when nothing else is configured): campaign create /
push / fetch / cancel, upstream errors, and **ORPHANED** errors with Keitaro campaign/stream IDs
if a failed create could not be rolled back remotely.

## Run

### Production-style (UI served by FastAPI)

Build the Vue app (`npm run build` type-checks with `vue-tsc` first, then runs `vite build`), then start uvicorn. FastAPI serves `frontend/dist` at `/` and `/assets`.

```bash
cd frontend
npm run build
cd ..
.\.venv\Scripts\activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

If the UI was not built yet, `/` returns HTTP 503 with build instructions (`/api/*` still works, and
`/favicon.svg` returns 404). `/assets` is only mounted if `frontend/dist/assets` exists when uvicorn starts, so after
the first `npm run build` restart uvicorn once (later rebuilds are picked up without a restart).
`--reload` only watches Python files; drop it for a non-development run.

### Dev (Vite + API proxy)

Run API and Vite separately. Vite proxies `/api` → `http://127.0.0.1:8000`.

Terminal 1 — backend:

```bash
.\.venv\Scripts\activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2 — frontend:

```bash
cd frontend
npm install   # first time only
npm run dev
```

The proxy target (`http://127.0.0.1:8000`) is set in `frontend/vite.config.ts`; change it there if the API runs on another port.
If `ADROBOT_API_TOKEN` is set on the backend, paste it via **Settings** in the UI (or set `VITE_ADROBOT_API_TOKEN` in `frontend/.env.local` and restart `npm run dev`).

Open http://127.0.0.1:5173

## API (main)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/campaigns` | Create campaign (2 streams) |
| GET | `/api/campaigns` | Local campaigns |
| GET | `/api/campaigns/{keitaro_id}` | Campaign detail |
| POST | `/api/campaigns/{keitaro_id}/fetch-streams` | Sync streams from Keitaro |
| POST | `/api/streams/{local_stream_id}/offers` | Add offer (recalculate shares) |
| DELETE | `/api/streams/{local_stream_id}/offers/{offer_id}` | Soft-remove offer |
| POST | `/api/streams/{local_stream_id}/offers/{offer_id}/bring-back` | Restore removed offer |
| POST | `/api/streams/{local_stream_id}/offers/{offer_id}/pin` | Pin/unpin weight |
| POST | `/api/streams/{local_stream_id}/push` | Publish offers to Keitaro |
| POST | `/api/streams/{local_stream_id}/cancel` | Discard draft changes |

Errors are JSON `{"detail": "..."}`. Local errors (unknown stream/offer, invalid input) use
400/404; Keitaro errors pass through 400/404, while Keitaro 401/403/5xx and network errors or
timeouts become **502**.

## Tests

Install deps (includes pytest), then from the repo root with the venv active:

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

The commands above are PowerShell. In cmd use `set RUN_LIVE_TESTS=1` and in bash `RUN_LIVE_TESTS=1 pytest -m live`.
Without `RUN_LIVE_TESTS=1`, live tests are skipped even with `-m live`.

Live tests archive (soft-delete) every campaign they create and print the IDs and cleanup state
at the end of the run. The multi-offer editor flow is skipped if Keitaro has fewer than 2 active offers.
Unit tests ignore `.env` (including `ADROBOT_API_TOKEN`); live tests send the token if it is set.

| Suite | Path | Network |
|-------|------|---------|
| Weight / naming unit tests | `tests/test_weights.py`, `tests/test_naming.py` | none |
| FastAPI TestClient + fake Keitaro | `tests/test_api.py`, `tests/test_sync.py`, `tests/test_create_compensation.py` | none |
| Error mapping, auth, DB migration | `tests/test_errors_auth.py`, `tests/test_database.py` | none |
| Live create / sync / compensation | `tests/test_live_keitaro.py` (`@pytest.mark.live`) | real Keitaro |

## Project layout

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

## Creator stream rules

1. **Geo → Google** — `type=regular`, `schema=redirect`, country filter `accept` for given ISO code, HTTP redirect to Google.
2. **Offers** — `type=default`, `schema=landings`, selected offer at 100% share.

Creation validates input (geo, offer, defaults) first, then creates the campaign and both streams
in Keitaro, then saves locally. If any step fails, created streams are deleted and the campaign is
archived (or disabled as fallback); if that also fails the leftover IDs are logged as `ORPHANED`.
If Keitaro drops the connection without answering the create call, the campaign is looked up by
its unique alias and rolled back too.

## Editor rules (from TZ video)

- Fetch streams; streams without offers are shown as plain streams.
- Add offer → equal shares among active offers; stream marked unpublished (yellow).
- Remove → offer goes to local archive at 0% (gray) with Bring back.
- Push → write offers/shares to Keitaro (always integers summing to exactly 100); Cancel → discard draft vs Keitaro.
- Pin keeps a fixed share while others redistribute to 100%.

### Fetch / draft semantics

A stream has a **real draft** when the user edited it locally (`is_dirty`, set only by add / remove /
bring back / pin) **and** its active offers or integer shares differ from Keitaro. On *Fetch streams*:

- Streams with a real draft keep their local offers untouched (still `is_dirty=true`).
- All other streams mirror Keitaro: shares are synced, offers gone from Keitaro become `removed`
  (0%, can be brought back), new remote offers are added, and `is_dirty` is `false`.
- Streams deleted in Keitaro are deleted locally (including any draft on them).
