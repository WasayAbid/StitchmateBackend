# StitchMate Backend API

Unified FastAPI service for auth, tailor onboarding, accessories marketplace, and fabric/design workflow.

## Setup

```bash
cd stichmate-backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Copy env vars (see `.env` in repo or create from list below).

## Run (single server — port **8000**)

```bash
uvicorn main:app --reload --port 8000
```

- Health: `GET http://localhost:8000/health`
- Docs: `http://localhost:8000/docs`

## Environment variables

| Variable | Used by |
|----------|---------|
| `SUPABASE_URL` | Auth, accessories |
| `SUPABASE_KEY` / `SUPABASE_ANON_KEY` | Auth, accessories reads |
| `SUPABASE_SERVICE_KEY` | Sync, overlay writes |
| `GEMINI_API_KEY` | Accessories AI, fabric workflow |
| `JWT_SECRET` | Fabric/workflow Bearer auth |
| `APIFY_API_TOKEN`, `APIFY_ACTOR_ID` | Daraz scrape sync |
| `SYNC_SECRET` | `POST /admin/sync-accessories` header |
| `DATABASE_URL` | SQLite fabric DB (default `sqlite:///./data/fabric.db`) |

## Frontend

In `bolt-stichmate/.env`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_FABRIC_API_BASE_URL=http://localhost:8000
```

## Accessories endpoints

- `GET /accessories` — list (search, category, pagination)
- `GET /accessories/{id}` — detail
- `POST /admin/sync-accessories` — Apify ingest (header `X-Sync-Secret`)
- `POST /ai/recommend-accessories` — body `{ "dress_image_url": "..." }`
- `POST /ai/overlay-accessory` — body `{ "dress_image_url", "accessory_id", "placement_mode" }`

Run `001_accessories.sql` in Supabase before sync.
