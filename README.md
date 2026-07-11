# ContextCast

Zero-cost local MVP for a hyperlocal event and news intelligence platform.

Link - https://context-cast.onrender.com

The original project plan describes a production-grade stack with Kafka, Redis,
PostgreSQL + pgvector, hosted observability, paid LLM explainability, and
optional GPU fine-tuning. This implementation keeps the product idea intact
while making it runnable at a cost of `0`: no paid APIs, no cloud services, and
no external package install. Live ingest is available on demand through public
free RSS feeds only.

> For a detailed, code-level explanation of how the whole system works — modules,
> data flow, the recommendation math, the API, persistence, and the front end —
> see [ARCHITECTURE.md](ARCHITECTURE.md).

## What This Builds

- Personalized local signal feed with match scores and "why this?" explanations
- **News vs. Events distinction**: every item is classified per-item (not just by
  source) into Event / News / Discussion / Signal, with a segmented filter that
  shows live counts and color-codes each card
- First-run context intake: city, domain, goals, genres, and signal types
- Topic/city/radius controls for cold-start users
- **~75 free public sources** across all 10 cities (per-city News + Events + Civic
  feeds), national outlets (The Hindu, Indian Express, NDTV, ToI, HT, News18,
  Firstpost, Scroll, The Wire, LiveMint, Moneycontrol), business/sports/
  entertainment sections, international tech (TechCrunch, The Verge, Wired, BBC,
  Hacker News, Ars Technica), Indian startup media (YourStory, Inc42, The Ken),
  Reddit city subreddits, and event feeds (Insider.in, Meetup, Dev.to)
- Automatic background refresh every 5 minutes while the server is running
- Per-host request throttling so rate-limited hosts (e.g. Reddit) get a fair shot
- Local extractive summaries for noisy posts and articles
- Pulse tab with city briefing, trend radar, and semantic clusters
- Interaction feedback that updates the user's local interest profile
- Canvas-based interest graph visualization
- Pipeline/admin dashboard with deterministic local metrics
- Modern UI: cohesive neutral + indigo design system, light/dark themes, SVG
  icons, keyboard navigation, loading skeletons, and a daily digest
- SQLite persistence in `contextcast.db`

## Run Locally

```powershell
python server.py
```

Then open this link in your browser:

```text
http://localhost:8000
```

(equivalently `http://127.0.0.1:8000`). On first launch the background ingest
starts after ~3 seconds and refreshes every 5 minutes; click **Pull Live** in the
header to fetch immediately.

Use a different port if needed (open the matching URL, e.g. `http://localhost:8010`):

```powershell
python server.py --port 8010
```

## Test

```powershell
python -m unittest discover -s tests
```

## Live Ingest

All sources are fetched **concurrently** (a full ~75-source pull completes in
roughly the time of the slowest single feed, typically 15–25 seconds, instead
of minutes sequentially). The `POST /api/ingest/live` endpoint starts the pull
in the background and returns immediately; poll `/api/admin/pipeline` (or
`/api/health`) to see when it finishes. Pass `"wait": true` for the old
synchronous behavior.

Click `Pull Live` in the UI, or call:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/ingest/live -Body '{"limit_per_source":4}' -ContentType 'application/json'
```

Sources are best-effort because public feeds can rate-limit or block. Failures
are shown in the source strip and do not break the app.

### Reddit feeds

Reddit RSS is included (one feed per city subreddit) but is the flakiest source:
Reddit rate-limits unauthenticated clients and returns **HTTP 429** when hit too
often from one IP. To handle this the ingest:

- throttles requests to `reddit.com` (see `HOST_MIN_INTERVAL` in `ingest.py`) so a
  refresh doesn't burst-hit Reddit and trigger a block, and
- retries on `old.reddit.com` when `www.reddit.com` returns 429.

If you still see `429 Too Many Requests` for the `r/<city>` sources in the source
strip, the IP is temporarily throttled by Reddit (often after rapid manual pulls).
It clears on its own — usually within minutes to about an hour — and the feeds
populate on the next refresh. Every other source works independently in the
meantime. Reddit does not offer a no-key, no-rate-limit RSS option, so this is a
platform limitation rather than a bug.

The server also runs a background refresh loop every 5 minutes. The UI refreshes
its feed/status view on the same cadence and shows the next refresh countdown.
To reopen the context intake manually, visit:

```text
http://localhost:8000/?intro=1
```

## Deployment

ContextCast is a single, **dependency-free** Python process (standard library
only) with **SQLite** for storage. There is nothing to `pip install` and no
external services, so deployment is mostly "run the script and persist one file".

### Why not Vercel / Netlify?

They don't fit this app. Vercel and Netlify host **static files + short-lived
serverless functions**, but ContextCast is the opposite:

- it's a **long-running server** with a background thread that re-fetches feeds
  every 5 minutes — serverless functions die after each request, so that loop
  can't run; and
- it **writes to a SQLite file**, while those platforms give you an ephemeral /
  read-only filesystem, so the DB would reset constantly.

Making it work there would mean rewriting it into serverless handlers plus an
external hosted database — which defeats the zero-cost, zero-dependency design.
Instead, use a host that runs a **persistent process with a small disk**. The
options below deploy straight from your Git repo, **no Docker needed**.

### Deploy on Render (recommended — one full-stack service)

ContextCast is **one process that serves both the API and the frontend**, so you
deploy a *single* Render Web Service — there is no separate frontend/backend to
wire up. The repo ships a `requirements.txt` (empty — std-lib only, so Render
detects Python) and a `render.yaml` Blueprint.

**Option 1 — Blueprint (one click):**

1. Push this repo to GitHub.
2. In Render: **New +** → **Blueprint** → select the repo. Render reads
   `render.yaml` and creates the service.
3. Deploy. You get a public `https://contextcast.onrender.com`-style URL.

**Option 2 — manual dashboard setup:**

1. Push to GitHub. In Render: **New +** → **Web Service** → connect the repo.
2. **Language:** Python. **Build command:** `pip install -r requirements.txt`.
3. **Start command:** `python server.py --host 0.0.0.0`
   — Render injects `$PORT` (the app reads it), but you **must** bind `0.0.0.0`
   or Render can't route traffic to the container.
4. **For persistence (paid Starter+):** add a **Disk** mounted at `/var/data`,
   then set env var `CONTEXTCAST_DB=/var/data/contextcast.db`. The DB then
   survives restarts and redeploys.
5. Create the service. The DB is created and seeded on first boot.

**Free plan caveat:** Render's free tier has **no persistent disk**, so omit the
disk + `CONTEXTCAST_DB` (the app falls back to an ephemeral `contextcast.db` and
re-seeds + re-ingests on each restart — fully functional, but saved items don't
survive restarts). Free services also **sleep after inactivity**, which pauses the
5-minute background ingest until the next request wakes them. For an always-on DB
and continuous ingest, use a paid instance with a disk.

No secrets or API keys are required. Railway works the same way (use a Volume
instead of a Disk); Fly.io too (persistent volume + `python server.py`).

### The database

- The DB is a single SQLite file. It is **created and seeded automatically** on
  first start — you do not run any migrations by hand (`store.py` handles schema
  creation and lightweight in-place migrations).
- Default location: `contextcast.db` next to `server.py`.
- Override the path with the `CONTEXTCAST_DB` environment variable. Point it at a
  **persistent volume** in production so data survives restarts and redeploys:

  ```bash
  CONTEXTCAST_DB=/data/contextcast.db python server.py --host 0.0.0.0 --port 8000
  ```

- **Back up** by copying the file while the server is stopped (or use
  `sqlite3 contextcast.db ".backup backup.db"` for a hot copy).
- **Reset** by deleting the file; it will be recreated and reseeded on next start.

`--host`/`--port` also read the `HOST`/`PORT` environment variables, so platforms
that inject `$PORT` (Railway, Render, Fly.io, Heroku) work without extra flags.

### Run as a service (bare VM / VPS)

Bind to all interfaces and keep it alive with `systemd`:

```ini
# /etc/systemd/system/contextcast.service
[Unit]
Description=ContextCast
After=network.target

[Service]
WorkingDirectory=/opt/contextcast
Environment=CONTEXTCAST_DB=/var/lib/contextcast/contextcast.db
Environment=HOST=127.0.0.1
Environment=PORT=8000
ExecStart=/usr/bin/python3 server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/lib/contextcast        # persistent DB dir
sudo systemctl enable --now contextcast
```

The built-in `ThreadingHTTPServer` serves no TLS and is not hardened for direct
public exposure, so keep it on `127.0.0.1` and put a reverse proxy in front:

```nginx
# nginx: terminate HTTPS and proxy to the local app
server {
  listen 443 ssl;
  server_name contextcast.example.com;
  # ssl_certificate / ssl_certificate_key ... (e.g. from certbot)
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

### Production caveats

- Single-process stdlib server: fine for demos and light traffic; for heavy
  concurrent load, front it with a proxy and/or run multiple instances behind a
  load balancer (note that each instance needs its own DB unless you move state to
  a shared store).
- The profile is a local **demo user** (`user_id="demo"`); there is no auth. Add
  authentication before exposing per-user data publicly.

## Cost-Free Architecture

| Original plan item | Zero-cost replacement |
| --- | --- |
| Claude/GPT explainability | Local template explanations from scoring signals |
| BART zero-shot classification | Keyword/topic heuristic classifier |
| sentence-transformer embeddings | Deterministic topic and keyword vectors |
| PostgreSQL + pgvector | SQLite + in-process cosine scoring |
| Kafka/Celery/Redis | Single local process with SQLite state |
| Mapbox/Grafana/D3 CDN | Native browser UI, canvas graph, local dashboard |
| Modal/Colab GPU pipeline | Deferred optional workflow, not required for MVP |
| Google OAuth | Local demo profile |

## Repository Layout

```text
contextcast/
  models.py        Shared dataclasses, topics, event/news signal keywords
  ingest.py        Public free RSS ingestion, kind detection, host throttling
  recommender.py   Local scoring, explanations, graph data
  summarizer.py    Local extractive summarization
  insights.py      Trends, clusters, briefing, model card, diagnostics
  seed.py          Sample events and default profile
  store.py         SQLite persistence and update helpers
static/
  index.html       App shell
  styles.css       Design system + light/dark themes
  app.js           Browser-side interaction and graph rendering
tests/
  test_ingest.py
  test_recommender.py
  test_store_workflows.py
  test_summarizer_insights.py
server.py          Standard-library HTTP server and API routes
```

## Notes

This is intentionally an MVP, not the full distributed system from the project
plan. It is designed to be cheap to run, easy to demo, and honest about scope.
The next free upgrade would be adding local RSS import from user-provided feeds,
still without paid APIs.
