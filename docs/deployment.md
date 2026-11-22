# Deployment

The app is two services: a FastAPI backend (Docker) and a Next.js frontend. Both run on free
tiers. Nothing here needs a paid plan, and the default configuration needs no secrets.

| Piece | Host | Config in repo |
|---|---|---|
| Backend API | Render free web service (or any Docker host) | `render.yaml`, `backend/Dockerfile` |
| Frontend | Vercel Hobby | `frontend/` (framework auto-detected) |

## 1. Backend on Render

1. Sign in at <https://dashboard.render.com> with GitHub and allow access to this repository.
2. **New > Blueprint**, pick the repository and the branch to deploy. Render reads
   `render.yaml` and proposes one free web service, `stoop-api`.
3. When asked for `CORS_ORIGINS`, enter the frontend URL from step 2 below (you can set it
   after the frontend exists: **Environment** tab, then redeploy). Leave the optional keys blank.
4. Deploy. Check `https://<service>.onrender.com/health` returns `{"status":"ok",...}` and
   `https://<service>.onrender.com/config` shows `"data_mode":"demo"`.

Free Render services sleep when idle; the first request after a sleep takes about a minute.
The frontend's error message says the service may be starting up.

Any other Docker host works the same way:

```bash
docker build -t stoop-api backend
docker run -p 8000:8000 -e CORS_ORIGINS=https://your-frontend.example stoop-api
```

## 2. Frontend on Vercel

1. Sign in at <https://vercel.com> with GitHub. **Add New > Project**, import this repository.
2. Set **Root Directory** to `frontend`. Framework preset: Next.js (detected).
3. Add the environment variable `NEXT_PUBLIC_API_BASE_URL` = `https://<service>.onrender.com`.
   This is the only frontend variable, and it is public by design. Never put an API key in a
   `NEXT_PUBLIC_*` variable.
4. Deploy, then copy the production URL into the backend's `CORS_ORIGINS` and redeploy the
   backend.

## 3. Verify

```bash
curl -s https://<service>.onrender.com/health
curl -s -X POST https://<service>.onrender.com/investigate \
  -H 'content-type: application/json' \
  -d '{"address":"1134 West Farms Rd, Bronx, NY 10459"}' | head -c 300
```

Then open the Vercel URL, click the first example, and confirm the report loads and that the
Markdown export link downloads.

## Options

| Variable | Default | Effect |
|---|---|---|
| `DATA_MODE` | `demo` | `live` queries NYC GeoSearch and NYC Open Data for any address (about 10 s and 25 requests per report). |
| `SOCRATA_APP_TOKEN` | blank | Recommended in live mode to avoid throttling. Free. |
| `LLM_ENABLED` | `0` | `1` adds the optional checked AI explanation. Needs `ANTHROPIC_API_KEY` (or `LLM_PROVIDER=openai` and `OPENAI_API_KEY`). This is a paid API: each report makes at most 2 calls, and `LLM_MAX_CALLS_PER_PROCESS` caps the total. |
| `INVESTIGATE_RATE_PER_MINUTE` | `20` | Per-client limit on `POST /investigate`. |

Before a public live-mode launch, review the NYC Open Data terms of use (see
`THIRD_PARTY_NOTICES.md`).
