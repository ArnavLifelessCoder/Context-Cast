# ContextCast — Architecture

This document explains how ContextCast is built, end to end: the design
philosophy, every module and what it does, how data flows through the system,
the recommendation math, the HTTP/API surface, the persistence layer, the
front end, and where to extend it.

If you just want to run or deploy it, see [README.md](README.md). This file is
about *how it works inside*.

---

## Table of contents

1. [Design philosophy](#1-design-philosophy)
2. [High-level shape](#2-high-level-shape)
3. [Repository map](#3-repository-map)
4. [Runtime & process model](#4-runtime--process-model)
5. [Data model](#5-data-model)
6. [Ingestion pipeline](#6-ingestion-pipeline)
7. [Classification: topic vs. kind](#7-classification-topic-vs-kind)
8. [Summarization](#8-summarization)
9. [The recommendation engine](#9-the-recommendation-engine)
10. [Insights engine](#10-insights-engine)
11. [Persistence layer](#11-persistence-layer)
12. [HTTP server & API surface](#12-http-server--api-surface)
13. [Front end](#13-front-end)
14. [End-to-end request walkthroughs](#14-end-to-end-request-walkthroughs)
15. [Configuration](#15-configuration)
16. [Testing](#16-testing)
17. [Extension points](#17-extension-points)
18. [Known limitations](#18-known-limitations)

---

## 1. Design philosophy

ContextCast is a **hyperlocal events + news intelligence** app. The original
project plan called for a production stack (Kafka, Redis, PostgreSQL + pgvector,
hosted LLMs, GPU fine-tuning). This implementation keeps the *product* intact
while obeying three hard constraints:

- **Zero cost** — no paid APIs, no cloud services, no API keys.
- **Zero dependencies** — Python **standard library only**. There is no
  `requirements.txt`; everything (HTTP server, RSS parsing, SQLite, math) ships
  with CPython.
- **Single process** — one script you can run locally or on a tiny VM.

Every "AI" capability is therefore a **deterministic local algorithm** rather
than a model behind an API:

| Production-grade idea | Local zero-cost replacement |
| --- | --- |
| LLM explanations | Template explanations derived from score components |
| Zero-shot topic classification | Keyword heuristic (`classify_topic`) |
| Sentence-transformer embeddings | TF-IDF vectors + cosine similarity |
| pgvector similarity search | In-process cosine over the event corpus |
| Abstractive summarization | Extractive sentence ranking (`summarizer.py`) |
| Kafka/Celery/Redis | A background thread + SQLite |

The benefit: it's transparent, debuggable, and free. The trade-off: heuristics
are simpler than learned models. See [§18](#18-known-limitations).

---

## 2. High-level shape

```
                         ┌──────────────────────────────────────────┐
   Public RSS/Atom feeds │              server.py (process)          │
   (Google News, Reddit, │                                           │
   The Hindu, TechCrunch,│   ┌───────────────┐    ┌───────────────┐  │
   BBC, YourStory, ...)  │   │ auto-ingest    │    │ HTTP Handler  │  │
        │                │   │ thread (5 min) │    │ (API + static)│  │
        │  fetch + parse │   └──────┬─────────┘    └──────┬────────┘  │
        ▼                │          │ writes              │ reads     │
   contextcast.ingest ───┼──────────┤                     │           │
   (classify, summarize) │          ▼                     ▼           │
                         │   ┌──────────────────────────────────┐    │
                         │   │   contextcast.store (SQLite)      │    │
                         │   │   events · profiles · interactions│    │
                         │   └──────────────────────────────────┘    │
                         │          │ list_events / profile           │
                         │          ▼                                 │
                         │   contextcast.recommender (scoring)        │
                         │   contextcast.insights   (trends/clusters) │
                         └───────────────┬──────────────────────────-─┘
                                         │ JSON
                                         ▼
                              static/ (index.html, app.js, styles.css)
                              browser SPA: feed, pulse, saved, graph, ops
```

There are **two independent loops** touching the database:

- a **writer** — the background ingest thread (and manual `Pull Live`), which
  fetches feeds and upserts events;
- **readers** — HTTP requests that score and serve the stored events.

They are decoupled through SQLite, which is the single source of truth.

---

## 3. Repository map

```
server.py            Std-lib HTTP server, routing, background ingest loop, env config
contextcast/
  models.py          Dataclasses (Event, UserProfile, ScoredEvent) + constants
                     (TOPICS, TOPIC_KEYWORDS, EVENT_SIGNALS, NEWS_SIGNALS, CITY_CENTER)
  ingest.py          Feed sources, fetching (+ throttle/fallback), parsing,
                     per-item topic & kind classification
  summarizer.py      Extractive summarization + tokenizer
  recommender.py     Hybrid scoring, diversity re-rank, interest updates, graph data
  insights.py        Trends, clusters, briefing, opportunities, model card, diagnostics
  store.py           SQLite schema, migrations, seeding, CRUD, reporting
  seed.py            Sample events + default demo profile
static/
  index.html         App shell (rail, views, modals, onboarding)
  app.js             SPA: state, rendering, API calls, keyboard nav, canvas graph
  styles.css         Design system (neutral + indigo) and light/dark themes
tests/               unittest/pytest suites for ingest, recommender, store, summarizer+insights
```

Dependency direction (no cycles):

```
server.py → store.py → recommender.py, summarizer.py, seed.py, models.py
          → ingest.py → summarizer.py, models.py
          → insights.py → summarizer.py, models.py
          → recommender.py, models.py
```

---

## 4. Runtime & process model

Everything lives in one process started by `server.py`:

- **`ThreadingHTTPServer`** handles each request on its own thread. Handlers are
  short-lived; there is no shared mutable in-memory app state besides
  `INGEST_STATE`.
- **`start_auto_ingest()`** spawns a daemon thread running `auto_ingest_loop()`:
  it sleeps ~3s, then every `AUTO_REFRESH_SECONDS` (300s) calls
  `run_live_ingest()`. Each cycle is wrapped in `try/except` so one bad pass
  (network blip, DB hiccup) can't kill the loop.
- **`run_live_ingest()`** is guarded by a non-blocking `INGEST_LOCK`. If a pull
  is already running (auto *or* manual), a second call returns immediately with
  `reason: "already-running"` instead of piling up. It records timing/status in
  the module-level `INGEST_STATE` dict (`last_run`, `next_run`, `running`,
  `last_result` — including `duration_ms`), which the UI surfaces in the Ops
  tab and the auto-refresh countdown. After upserting it calls
  `Store.prune_events()` to drop stale, never-interacted live items.
- **`start_background_ingest()`** wraps `run_live_ingest()` in a one-shot daemon
  thread. This is what the manual `POST /api/ingest/live` uses, so the HTTP
  request returns instantly and the browser polls for completion instead of
  hanging for the duration of the pull.
- **Concurrency & SQLite:** each DB operation opens its own connection
  (`Store.connect()` → `ManagedConnection`, which auto-closes on context exit)
  with **WAL journal mode** and a 10s busy timeout, so feed reads proceed while
  the ingest thread writes. Because connections are per-operation and short,
  the threaded server and the ingest threads coexist safely without an ORM or
  connection pool.

---

## 5. Data model

Defined as **frozen dataclasses** in `contextcast/models.py`.

### `Event`
The atomic unit — a single news article, event, post, or signal.

| Field | Meaning |
| --- | --- |
| `id` | Stable fingerprint (`live-<sha256[:18]>` for live items, `evt-*` for seeds) |
| `source` | Human feed name (e.g. `"Bangalore Events"`, `"Hacker News"`) |
| `title`, `description`, `summary` | Raw text + local extractive summary |
| `city`, `venue`, `lat`, `lon` | Location (from the feed's city; coords from `CITY_CENTER`) |
| `topic` | One of `TOPICS` (tech, music, finance, …) — *what it's about* |
| `kind` | `event` / `news` / `discussion` / `signal` — *what type it is* |
| `event_date`, `published_at`, `fetched_at` | Timestamps (UTC) |
| `url`, `image_url`, `source_domain` | Link metadata |

**`topic` vs `kind` is the core distinction** (see [§7](#7-classification-topic-vs-kind)):
topic = subject, kind = format. A `tech` item can be a `news` article or an
`event` (a workshop).

### `UserProfile`
`user_id`, `city`, `radius_km`, `interests` (`{topic: weight 0–1}`), and a
`context` dict (`domain`, `goal`, `signal_types`, `freshness`). The MVP uses a
single demo profile (`user_id="demo"`).

### `ScoredEvent`
An `Event` wrapped with its score breakdown (`semantic_score`,
`proximity_score`, `recency_score`, `graph_score`, `content_score`,
`novelty_score`, `momentum_score`, `diversity_score`) plus a human
`explanation`. This is what the feed API returns.

### Constants
- `TOPICS` — the 16 allowed topics.
- `TOPIC_KEYWORDS` — keyword sets per topic (drives `classify_topic`).
- `EVENT_SIGNALS` / `NEWS_SIGNALS` — phrase sets that drive `detect_kind`.
- `CITY_CENTER` — lat/lon per supported city (used for proximity).

---

## 6. Ingestion pipeline

Implemented in `contextcast/ingest.py`. One pass = `ingest_free_feeds()`:

```
free_sources()  →  ThreadPoolExecutor(max_workers=16):
  for each FeedSource (concurrently):
    fetch_with_fallback(url)        # HTTP GET, throttled, with reddit fallback
      → fetch_text(url)             #   throttle_host() + urllib request (6s timeout)
    parse_feed(source, text, limit) # RSS <item> or Atom <entry>
      → rss_item_to_event / atom_entry_to_event
        → build_event(...)          # classify topic + kind, summarize, fingerprint
  → {"events": [...], "statuses": [...]}   # statuses power the source strip
```

All ~75 sources are fetched **in parallel** through a bounded thread pool, so a
full pass takes roughly as long as the slowest single feed (typically 15–25s)
instead of the sum of all of them (minutes). Statuses keep the original source
order and include per-feed timing (`ms`), shown as tooltips in the source strip.

### Sources (`free_sources`, ~75 feeds)
Built programmatically:
- **Per city** (all 10 in `CITY_CENTER` minus `Remote`): a News query, an Events
  query, and a Civic query via `gnews()` (Google News RSS search), plus one
  Reddit subreddit feed from `CITY_SUBREDDITS`.
- **National** (The Hindu, Indian Express, NDTV, ToI, HT, News18, Firstpost,
  Scroll, The Wire), **topical** (business/sports/entertainment, Moneycontrol,
  LiveMint), **international tech** (TechCrunch, The Verge, Wired, BBC, Hacker
  News, Ars Technica, Lobsters), **Indian startup** (YourStory, Inc42, Entrackr,
  The Ken), and **event/culture** feeds (Insider.in, Meetup, Dev.to).

Each source carries a *prior* `kind` that `detect_kind` can override per item.

### Fetching, throttling & resilience
- `fetch_text()` sends a descriptive `User-Agent` + `Accept` header and decodes
  defensively (`errors="replace"`).
- `throttle_host()` enforces a per-host minimum interval (`HOST_MIN_INTERVAL`,
  e.g. `reddit.com → 1.5s`). Each thread *reserves* its slot under a lock but
  sleeps outside it, so a throttled host (Reddit) never stalls the concurrent
  fetches of every other host.
- `fetch_with_fallback()` retries on `old.reddit.com` when `www.reddit.com`
  returns **HTTP 429**.
- Failures are **isolated**: a feed that errors is recorded in `statuses`
  (`ok=False`, truncated error) and the pass continues. One broken feed never
  breaks ingestion.

### Parsing
`parse_feed()` sniffs Atom (`<feed>/<entry>`) vs RSS (`<channel>/<item>`).
Item→event conversion extracts title/description/link/date and an **image**
(`media:content`, `media:thumbnail`, `enclosure`, or the first `<img>` in the
HTML body). `clean_text()` strips tags and unescapes entities; `parse_date()`
handles RFC-822 and ISO-8601, normalizing everything to UTC.

### Identity / dedup
`build_event()` computes `id = "live-" + sha256("source|title|url")[:18]`. Upsert
on this id means the same article re-seen across refreshes **updates in place**
instead of duplicating (see `Store.upsert_events`).

---

## 7. Classification: topic vs. kind

Two orthogonal classifiers run on every item in `build_event()`.

### Topic — `classify_topic(text)`
Scores the title+description against `TOPIC_KEYWORDS`. `keyword_hits()` uses
**word-boundary** matching for single words (so "art" doesn't match "start") and
substring matching for multi-word phrases. The highest-scoring topic wins;
ties/empties fall back to `"news"`.

### Kind — `detect_kind(text, source)`
Decides **Event / News / Discussion / Signal** per item — the feature behind the
UI's segmented filter. The source's declared kind is a *prior*, but strong
per-item language overrides it:

```
event_hits = # of EVENT_SIGNALS present   (workshop, tickets, rsvp, lineup, …)
news_hits  = # of NEWS_SIGNALS present    (announces, minister, stocks, verdict, …)

if event_hits ≥ 2 and event_hits > news_hits → "event"      # strong event language wins
if source.kind == "event"      → "event" unless it reads like hard news
if source.kind == "discussion" → "discussion"
if source.kind == "signal"     → "event" if event_hits ≥ 2 else "signal"
# news sources: a single clear event cue with no news cue → "event", else "news"
```

This is what lets "Bangalore Tech Summit this Saturday — tickets open" from a
*news* feed surface as an **Event** instead of being buried as generic news.

---

## 8. Summarization

`summarizer.py` does **extractive** summarization (no model):

1. `clean_summary_input()` strips URLs, Reddit boilerplate ("submitted by /u/…",
   `[link]`/`[comments]`), and collapses whitespace.
2. `split_sentences()` splits on sentence punctuation, keeping sentences > 24
   chars.
3. Each of the first 8 sentences is scored by: global term frequency + boosts for
   **topic keywords (×3)**, **title terms (×2)**, and **city terms (×2)**, minus a
   small position penalty, normalized by length.
4. The top 2 sentences are taken, **re-ordered to their original order**, and
   trimmed to ~210 chars at a word boundary.

Summaries are computed once at ingest/seed time and stored on the row, so reads
don't recompute them.

---

## 9. The recommendation engine

`recommender.py` turns stored events + a profile into a ranked feed.
`score_events(events, profile, limit, interactions)` is the entry point.

### Per-event score components (each ~0–1)

| Component | Function | Idea |
| --- | --- | --- |
| **semantic** | `semantic_score` | Direct interest in the topic + spillover via `TOPIC_NEIGHBORS` (e.g. liking `tech` lifts `education`) |
| **proximity** | `proximity_score` | `exp(-distance / radius)` using the **haversine** distance from the profile city to the event |
| **recency** | `recency_score` | `exp(-age_days / 14)` — exponential freshness decay |
| **graph** | `graph_score` | Topic's share of the profile's total interest mass |
| **content** | `content_similarity_score` | **TF-IDF cosine** between the profile (goal+domain+interests) and the event text, scaled ×3 |
| **novelty** | `novelty_score` | Higher for topics the user barely follows (encourages discovery) |
| **authority** | `source_authority_score` | Trust weight per source (`SOURCE_AUTHORITY`, default 0.55) |
| **context** | `context_score` | Match on preferred `signal_types`, `domain` mention, and `freshness` preference |
| **momentum** | from `interactions` | Recent saves/clicks/attends boost their topics (decaying recency-of-behavior signal) |

`build_corpus_idf()` computes IDF across the whole event corpus once per request
so the content vectors are properly weighted.

### Ensemble
A fixed weighted sum (weights live in `score_events`):

```
final = 0.22·semantic + 0.18·content + 0.12·proximity + 0.12·recency
      + 0.10·context  + 0.08·momentum + 0.07·authority + 0.06·graph + 0.05·novelty
```

### Diversity re-rank — `diversity_rerank()`
A greedy MMR-style pass prevents one topic from dominating: as items are picked,
later items of an over-represented topic get a penalty
(`penalty · max(0, count − max_same_topic + 1)`), and each pick records a
`diversity_score`. This keeps the top of the feed varied even if the raw scores
are topic-clustered.

### Explanations — `explain()`
Builds a human "why this?" string from the score components ("Strong tech match",
"nearby in Bangalore", "from trusted source", "explores a new area for you"),
with a graceful fallback so every card has a reason.

### Learning from feedback — `update_interests()`
Interactions adjust interest weights with an **exponential moving average**
(`alpha = 0.18`). Positive actions move the topic toward an action-specific target
(`click 0.55`, `save 0.85`, `attend 1.0`); `not_interested` subtracts. Weights are
clamped to 1.0 and pruned below 0.01. Over time the feed personalizes.

### Graph data — `graph_payload()`
Emits nodes/edges (you → topics → events) consumed by the canvas visualization in
the Graph tab.

---

## 10. Insights engine

`insights.py` (`build_insights`) powers the **Pulse** and **Ops** tabs. It scopes
to the profile's city (falling back to all events) and computes:

- **Trends** — per topic, a lift score comparing the last 7 days vs older
  (`(recent+1)/(older+1)` × `log1p(count)`), labeled *spiking / rising / steady /
  cooling*.
- **Clusters** — events bucketed by `(city, topic)`, each with its top keywords
  and a lead summary.
- **Briefing** — a short natural-language "city pulse" from the strongest trend
  and most recent items.
- **Opportunities** — recent events matching the user's interests or trending
  topics, each with a reason.
- **Model card** — corpus stats (signals indexed, source coverage, median
  freshness hours, signal mix) and CV-style metric bullets.
- **Diagnostics** — health checks (low coverage, topic over-concentration,
  missing discussion signals).

These are pure functions over the event list — no extra storage.

---

## 11. Persistence layer

`store.py` wraps a single SQLite file. Three tables:

```sql
events       (id PK, source, title, description, city, venue, lat, lon, topic,
              event_date, fetched_at, url, kind, summary, published_at,
              image_url, source_domain)
profiles     (user_id PK, city, radius_km, interests_json, context_json, updated_at)
interactions (id PK, user_id, event_id, action, created_at)

-- indexes: events(event_date DESC), interactions(user_id, created_at DESC),
--          interactions(event_id)
```

Connections run in **WAL mode** with `synchronous=NORMAL` and a 10s busy
timeout, so the reader threads (feed API) and writer threads (ingest) don't
block each other.

Key behaviors:
- **`init_db()`** creates tables, runs `_migrate()` (additive `ALTER TABLE`s that
  make old DBs forward-compatible — e.g. adding `image_url`, `source_domain`,
  `context_json`), then `_seed()` (inserts `SEED_EVENTS` and the default profile
  with `ON CONFLICT DO NOTHING`, so seeding is idempotent).
- **`upsert_events()`** — `INSERT … ON CONFLICT(id) DO UPDATE`, counting genuine
  inserts via `total_changes`.
- **`add_interaction()`** records the event and recomputes interest weights via
  `update_interests()` in one transaction.
- **`saved_events()`** returns items with a `save`/`attend` interaction;
  **`remove_interaction()`** deletes those.
- **`admin_stats()`** aggregates counts by source/topic/city (plus a distinct
  `source_count`) for the Ops tab; **`portfolio_report()`** renders a Markdown
  summary for the Export feature.
- **`prune_events(max_age_days=21)`** — run after every ingest — deletes
  `live-*` events older than the cutoff that have **no interactions**, so the
  DB stops growing unboundedly while saved items and interest history survive.
- **`row_to_event()`** rebuilds an `Event` (recomputing a summary/domain if a row
  predates those columns).
- **`sanitize_context()`** validates the profile `context` (whitelists
  `signal_types`, length-caps strings).

`ManagedConnection` (a `sqlite3.Connection` subclass) closes itself on `__exit__`,
so every `with self.connect() as conn:` is leak-free.

---

## 12. HTTP server & API surface

`server.py` uses `BaseHTTPRequestHandler`. Anything under `/api/` is routed to
JSON handlers; everything else is served from `static/` (with a path-traversal
guard that resolves the target and checks it stays inside `STATIC`). Datetimes
and dataclasses serialize via `ApiEncoder`.

Transport-level behavior:

- **Gzip** — JSON responses over 1 KB and text-like static files are compressed
  when the client sends `Accept-Encoding: gzip` (the 30-item feed payload drops
  from ~42 KB to ~13 KB).
- **Static caching** — files are cached in memory (with pre-compressed gzip
  copies), invalidated by mtime, and served with `Cache-Control: public,
  max-age=60`; API responses are `no-store`.
- **Defensive parsing** — numeric query/body params go through `safe_int()`
  (default + clamping), so a malformed `limit` or `radius_km` can't 500 a
  request.

### GET

| Endpoint | Returns |
| --- | --- |
| `/api/feed?user_id=&limit=&sort=` | Profile + ranked `ScoredEvent`s (`sort`: relevance/newest/nearest) |
| `/api/events` | All stored events |
| `/api/saved` | Saved/attended events |
| `/api/graph` | Nodes/edges for the taste graph |
| `/api/insights` | Pulse/Ops payload (`build_insights`) |
| `/api/admin/pipeline` | Corpus stats + `INGEST_STATE` + refresh interval |
| `/api/report` | Markdown portfolio report |
| `/api/meta` | Topic list + city list (drives the controls) |
| `/api/article/<id>` | Single-item detail |
| `/api/health` | Liveness + `ingest_running` flag (used as the Render health check and by the UI's pull polling) |

### POST (JSON body)

| Endpoint | Effect |
| --- | --- |
| `/api/interact` | Record `click`/`save`/`attend`/`not_interested`; update interests |
| `/api/interact/remove` | Un-save an item |
| `/api/onboarding` | Upsert the profile (city, radius, interests, context) |
| `/api/ingest/live` | Start a live pull **in the background** and return immediately (the `Pull Live` button); pass `"wait": true` for the legacy synchronous behavior |

Errors return JSON `{"error": …}` with an appropriate HTTP status; bad JSON →
`400`.

---

## 13. Front end

A dependency-free SPA in three files — no framework, no build step.

- **`index.html`** — the shell: a left **rail** (brand, SVG nav, profile
  controls), the **workspace** with five views (`feed`, `pulse`, `saved`,
  `graph`, `admin`/Ops), plus the onboarding modal, detail modal, toast
  container, and keyboard-hints bar.
- **`app.js`** — a single `state` object + render functions. `load()` fetches all
  endpoints in parallel (`Promise.all`) and repaints. Highlights:
  - **Segmented kind filter** with live counts (`updateKindCounts`) — the
    News/Events/Talk/Signals distinction; cards get a `kind-*` class for the
    colored accent bar and a `kindLabel()` badge.
  - Client-side **search, sort, and filter** (`filteredFeed`), search-term
    highlighting, and a redundant-chip suppressor (`topicChip`).
  - **Interactions** (save/track/mute) → POST → `refreshQuiet()`, a light
    refresh that updates only the saved list and stats — the feed is *not*
    re-ranked mid-read, so scroll position is preserved; **toasts** for
    feedback.
  - **Pull Live** starts the background ingest, then polls
    `/api/admin/pipeline` with a button spinner and a "Refreshing…" status chip
    until `ingest.running` clears, then toasts the result with its duration.
  - **Keyboard navigation** (`j/k` move, `Enter` open, `s` save, `m` mute, `/`
    focus search, `?` hints).
  - **Dark mode** (token swap via `body.dark`, persisted in `localStorage`),
    **daily digest**, **loading skeletons**, and a **canvas** force-style graph
    (`renderGraph`).
  - First-run **onboarding** (gated by `localStorage`; re-openable via `?intro=1`).
- **`styles.css`** — a tokenized design system: neutral base + a single indigo
  accent, four muted **semantic kind colors** (event=indigo, news=teal,
  discussion=amber, signal=violet), a type scale, subtle shadows, and a fully
  tokenized **dark theme**. Icons are inline SVG, not emoji.

The front end talks only to the JSON API; it holds no business logic beyond
presentation, filtering, and sorting.

---

## 14. End-to-end request walkthroughs

### A. Background refresh (write path)
```
auto_ingest_loop (every 300s)
  → run_live_ingest()            # acquires INGEST_LOCK (skips if busy)
    → ingest_free_feeds()        # fetch ~75 feeds concurrently; parse/classify/summarize
    → STORE.upsert_events()      # insert/update by fingerprint id
    → STORE.prune_events()       # drop stale, never-interacted live items
  → updates INGEST_STATE (last_run/next_run/last_result incl. duration_ms)
```

The manual path is identical except it enters through
`POST /api/ingest/live → start_background_ingest()` (returns immediately) and
the browser polls `/api/admin/pipeline` until `running` clears.

### B. Loading the feed (read path)
```
GET /api/feed?limit=30
  → STORE.get_profile("demo")
  → STORE.list_events()
  → STORE.recent_interactions("demo")
  → score_events(...)            # ensemble + diversity re-rank
  → JSON {profile, events[], generated_at}
app.js load() → renderFeed() / digest / counts / source strip
```

### C. Saving an item (feedback loop)
```
click "Save" → POST /api/interact {event_id, action:"save"}
  → STORE.add_interaction()      # insert interaction + update_interests() (EMA)
  → app.js refreshQuiet()        # saved list + stats update; feed order (and
                                 # scroll position) stay put — the boosted topic
                                 # shows up on the next full load / auto-refresh
```

---

## 15. Configuration

| Variable / flag | Default | Purpose |
| --- | --- | --- |
| `CONTEXTCAST_DB` (env) | `./contextcast.db` | SQLite file location (point at a persistent disk in prod) |
| `HOST` (env) / `--host` | `127.0.0.1` | Bind address (`0.0.0.0` to expose) |
| `PORT` (env) / `--port` | `8000` | Listen port (PaaS platforms inject `$PORT`) |
| `AUTO_REFRESH_SECONDS` | `300` | Background ingest interval (in `server.py`) |
| `HOST_MIN_INTERVAL` | `reddit.com: 1.5s` | Per-host fetch throttle (in `ingest.py`) |
| `max_workers` | `16` | Concurrent feed fetches per ingest pass (`ingest_free_feeds`) |
| `fetch_text` timeout | `6s` | Per-feed HTTP timeout (in `ingest.py`) |
| `prune_events` max age | `21` days | Retention for un-interacted live events (in `store.py`) |

---

## 16. Testing

`tests/` covers the deterministic core (run with `python -m pytest` or
`python -m unittest discover -s tests`):

- `test_ingest.py` — feed parsing + topic classification (incl. word-boundary
  matching).
- `test_recommender.py` — scoring/ranking behavior.
- `test_store_workflows.py` — DB CRUD, interactions, saved flow.
- `test_summarizer_insights.py` — summarization + insights output.

Because there are no network calls in the tested paths (parsing takes feed text
directly), the suite is fast and offline.

---

## 17. Extension points

- **Add a feed:** append a `FeedSource(name, url, city, kind)` in `free_sources()`.
  If it's rate-limited, add a `HOST_MIN_INTERVAL` entry; trusted? add a
  `SOURCE_AUTHORITY` weight.
- **Add a topic:** extend `TOPICS` + `TOPIC_KEYWORDS` (and optionally
  `TOPIC_NEIGHBORS`).
- **Tune ranking:** edit the weights in `score_events` or the component functions.
- **Tune event detection:** extend `EVENT_SIGNALS` / `NEWS_SIGNALS` or the rules
  in `detect_kind`.
- **Real users:** the API already takes `user_id`; replacing the single demo
  profile means adding auth and per-user reads (the schema already keys profiles
  and interactions by `user_id`).
- **Swap heuristics for models:** any local function (classifier, summarizer,
  embeddings) can be replaced by a hosted model without touching the rest — they
  are isolated behind plain function signatures.

---

## 18. Known limitations

- **Heuristics, not learned models** — topic/kind classification and similarity
  are keyword/TF-IDF based; good enough to demo, weaker than embeddings on
  nuance.
- **Single-process std-lib server** — fine for demos and light traffic; for heavy
  concurrency, front it with a reverse proxy and/or scale out (each instance
  needs its own SQLite unless state moves to a shared store).
- **No auth** — one shared `demo` profile; add authentication before exposing
  per-user data.
- **Best-effort feeds** — public RSS can rate-limit or block (Reddit especially,
  see README). Failures are surfaced, not fatal.
- **Geo is city-centroid** — proximity uses the city center, not precise venue
  geocoding.
