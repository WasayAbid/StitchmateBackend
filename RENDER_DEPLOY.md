# StitchMate Backend — Render Deployment Guide

Frontend (Vercel): **https://stitch-mate-ten.vercel.app/**  
Backend target: **Render Web Service**

---

## Step 1 — Push backend to GitHub

Render deploys from Git. If you only have local code:

```bash
cd stichmate-backend
git init
git add .
git commit -m "Prepare StitchMate backend for Render"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/stitchmate-backend.git
git push -u origin main
```

> Push **only** the `stichmate-backend` folder as its own repo (recommended).  
> Do **not** commit `.env` — it contains secrets.

---

## Step 2 — Create Render Web Service

1. Go to [https://dashboard.render.com](https://dashboard.render.com) and sign in.
2. Click **New +** → **Web Service**.
3. Connect your GitHub account and select the `stitchmate-backend` repository.
4. Use these settings:

| Setting | Value |
|---------|--------|
| **Name** | `stitchmate-backend` |
| **Region** | Singapore (closest to Pakistan) |
| **Branch** | `main` |
| **Root Directory** | *(leave empty if backend is repo root)* |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| **Plan** | Free |

5. Click **Advanced** → set **Health Check Path** to `/health`.

---

## Step 3 — Environment Variables (Render Dashboard)

In **Environment** tab, add these (copy values from your local `.env`):

### Required

| Key | Example / Notes |
|-----|-----------------|
| `SUPABASE_URL` | `https://xxxxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Service role key from Supabase → Settings → API |
| `SUPABASE_KEY` | Same as anon key (optional fallback) |
| `GEMINI_API_KEY` | Google AI Studio key |
| `GROQ_API_KEY` | Groq console key |
| `FRONTEND_URL` | `https://stitch-mate-ten.vercel.app` |
| `GOOGLE_REDIRECT_URL` | `https://stitch-mate-ten.vercel.app/auth/callback` |
| `CORS_ORIGINS` | `https://stitch-mate-ten.vercel.app,http://localhost:8080` |
| `PUBLIC_BASE_URL` | Your Render URL, e.g. `https://stitchmate-backend.onrender.com` |

### Payments (if using Stripe)

| Key | Notes |
|-----|-------|
| `STRIPE_SECRET_KEY` | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | From Stripe webhook pointing to Render |
| `STRIPE_CURRENCY` | `usd` |
| `STRIPE_PRICE_ONE_DAY` | etc. |

### Optional

| Key | Notes |
|-----|-------|
| `APIFY_API_TOKEN` | Accessory catalog sync |
| `APIFY_ACTOR_ID` | Daraz scraper actor |
| `SYNC_SECRET` | Admin sync header secret |
| `DATABASE_URL` | `sqlite:///./data/fabric.db` |
| `UPLOAD_DIR` | `uploads` |

6. Click **Create Web Service** and wait for deploy (~3–5 min).

7. Copy your live URL, e.g. `https://stitchmate-backend.onrender.com`.

8. Test: open `https://YOUR-RENDER-URL/health` — should return `{"status":"healthy"}`.

---

## Step 4 — Update Vercel (Frontend)

1. Vercel Dashboard → your project → **Settings** → **Environment Variables**.
2. Add or update:

```
VITE_API_BASE_URL=https://stitchmate-backend.onrender.com
VITE_FABRIC_API_BASE_URL=https://stitchmate-backend.onrender.com
```

3. **Redeploy** the frontend (Deployments → ⋯ → Redeploy).

---

## Step 5 — Supabase Auth URLs

Supabase Dashboard → **Authentication** → **URL Configuration**:

| Field | Value |
|-------|--------|
| **Site URL** | `https://stitch-mate-ten.vercel.app` |
| **Redirect URLs** | Add `https://stitch-mate-ten.vercel.app/auth/callback` |

For Google OAuth: **Authentication** → **Providers** → Google — ensure redirect URL matches.

---

## Step 6 — Stripe Webhook (optional)

If using payments:

1. Stripe Dashboard → **Developers** → **Webhooks** → Add endpoint.
2. URL: `https://stitchmate-backend.onrender.com/api/payments/webhook`
3. Copy signing secret → set `STRIPE_WEBHOOK_SECRET` on Render.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| CORS error in browser | Add Vercel URL to `CORS_ORIGINS` on Render; redeploy |
| 502 / service sleeping | Free tier sleeps after 15 min — first request takes ~30–50s |
| Auth works locally, not live | Check `VITE_API_BASE_URL` on Vercel; hard refresh |
| Fabric upload fails on Render | SQLite/uploads are ephemeral on free tier — restarts clear local files. Auth, orders, riders (Supabase) still work. |
| Build fails | Check Render logs; ensure `requirements.txt` is at repo root |

---

## Quick test after deploy

```bash
curl https://YOUR-RENDER-URL/health
curl https://YOUR-RENDER-URL/
```

Then open https://stitch-mate-ten.vercel.app and try login + rider/tailor APIs.
