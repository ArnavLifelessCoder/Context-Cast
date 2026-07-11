from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import os
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from contextcast.ingest import ingest_free_feeds
from contextcast.insights import build_insights
from contextcast.models import CITY_CENTER, TOPICS
from contextcast.recommender import graph_payload, score_events
from contextcast.store import Store


ROOT = Path(__file__).parent
STATIC = ROOT / "static"
# DB path is overridable so deployments can point it at a persistent volume.
DB_PATH = Path(os.environ.get("CONTEXTCAST_DB", ROOT / "contextcast.db"))
STORE = Store(DB_PATH)
INGEST_LOCK = threading.Lock()
INGEST_STATE: dict[str, object] = {
    "last_run": None,
    "next_run": None,
    "running": False,
    "last_result": None,
}
AUTO_REFRESH_SECONDS = 300

# Static files are tiny; keep gzipped copies in memory instead of re-reading
# and re-sending full bytes on every request.
_STATIC_CACHE: dict[str, tuple[float, bytes, bytes | None]] = {}
_STATIC_CACHE_LOCK = threading.Lock()
COMPRESSIBLE_SUFFIXES = {".html", ".css", ".js", ".json", ".svg", ".txt", ".md"}


def load_static_cached(target: Path) -> tuple[bytes, bytes | None]:
    key = str(target)
    mtime = target.stat().st_mtime
    with _STATIC_CACHE_LOCK:
        cached = _STATIC_CACHE.get(key)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
    data = target.read_bytes()
    gz = gzip.compress(data, compresslevel=9) if target.suffix in COMPRESSIBLE_SUFFIXES else None
    with _STATIC_CACHE_LOCK:
        _STATIC_CACHE[key] = (mtime, data, gz)
    return data, gz


def safe_int(value: object, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


def run_live_ingest(limit_per_source: int = 4, reason: str = "manual") -> dict[str, object]:
    if not INGEST_LOCK.acquire(blocking=False):
        return {
            "ok": True,
            "cost_usd": 0,
            "fetched": 0,
            "upserted": 0,
            "statuses": [],
            "reason": "already-running",
        }
    try:
        INGEST_STATE["running"] = True
        started = time.monotonic()
        result = ingest_free_feeds(limit_per_source=limit_per_source)
        upserted = STORE.upsert_events(result["events"])
        pruned = STORE.prune_events()
        payload = {
            "ok": True,
            "cost_usd": 0,
            "fetched": len(result["events"]),
            "upserted": upserted,
            "pruned": pruned,
            "statuses": result["statuses"],
            "reason": reason,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "ran_at": datetime.now(timezone.utc).isoformat(),
        }
        INGEST_STATE["last_run"] = payload["ran_at"]
        INGEST_STATE["next_run"] = datetime.fromtimestamp(
            time.time() + AUTO_REFRESH_SECONDS, timezone.utc
        ).isoformat()
        INGEST_STATE["last_result"] = payload
        return payload
    finally:
        INGEST_STATE["running"] = False
        INGEST_LOCK.release()


def start_background_ingest(limit_per_source: int = 4, reason: str = "manual") -> bool:
    """Kick off an ingest without blocking the caller. Returns False if one is already running."""
    if INGEST_STATE.get("running"):
        return False

    def worker() -> None:
        try:
            run_live_ingest(limit_per_source=limit_per_source, reason=reason)
        except Exception as exc:
            INGEST_STATE["last_result"] = {"ok": False, "error": str(exc)[:200]}

    threading.Thread(target=worker, name="contextcast-ingest", daemon=True).start()
    return True


def auto_ingest_loop() -> None:
    time.sleep(3)
    while True:
        try:
            run_live_ingest(limit_per_source=3, reason="auto")
        except Exception as exc:
            # One bad cycle (network blip, DB hiccup) must not kill the loop.
            print(f"auto-ingest failed: {exc}")
        time.sleep(AUTO_REFRESH_SECONDS)


def start_auto_ingest() -> None:
    INGEST_STATE["next_run"] = datetime.fromtimestamp(
        time.time() + 3, timezone.utc
    ).isoformat()
    thread = threading.Thread(target=auto_ingest_loop, name="contextcast-auto-ingest", daemon=True)
    thread.start()


class ApiEncoder(json.JSONEncoder):
    def default(self, obj: object) -> object:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if is_dataclass(obj):
            return asdict(obj)
        return super().default(obj)


class Handler(BaseHTTPRequestHandler):
    server_version = "ContextCastLocal/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.write_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            self.handle_api_post(parsed.path, payload)
        except ValueError as exc:
            self.write_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        user_id = query.get("user_id", ["demo"])[0]
        if path == "/api/health":
            self.write_json({"ok": True, "ingest_running": INGEST_STATE.get("running", False)})
            return
        if path == "/api/feed":
            profile = STORE.get_profile(user_id)
            limit = safe_int(query.get("limit", ["24"])[0], 24, lo=1, hi=100)
            sort = query.get("sort", ["relevance"])[0]
            interactions = STORE.recent_interactions(user_id)
            feed = score_events(STORE.list_events(), profile, limit=limit, interactions=interactions)
            if sort == "newest":
                feed = sorted(feed, key=lambda item: item.event.published_at or item.event.event_date, reverse=True)
            elif sort == "nearest":
                feed = sorted(feed, key=lambda item: item.proximity_score, reverse=True)
            self.write_json(
                {
                    "profile": profile,
                    "events": feed,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return
        if path == "/api/events":
            self.write_json({"events": STORE.list_events()})
            return
        if path == "/api/saved":
            self.write_json({"events": STORE.saved_events(user_id)})
            return
        if path == "/api/report":
            report = STORE.portfolio_report(user_id)
            self.write_json({"markdown": report, "generated_at": datetime.now(timezone.utc).isoformat()})
            return
        if path == "/api/graph":
            profile = STORE.get_profile(user_id)
            self.write_json(graph_payload(profile, STORE.list_events()))
            return
        if path == "/api/admin/pipeline":
            stats = STORE.admin_stats()
            stats["ingest"] = INGEST_STATE
            stats["auto_refresh_seconds"] = AUTO_REFRESH_SECONDS
            self.write_json(stats)
            return
        if path == "/api/insights":
            profile = STORE.get_profile(user_id)
            self.write_json(build_insights(STORE.list_events(), profile))
            return
        if path == "/api/meta":
            self.write_json({"topics": TOPICS, "cities": list(CITY_CENTER.keys())})
            return
        if path.startswith("/api/article/"):
            event_id = path.split("/api/article/", 1)[1]
            detail = STORE.article_detail(event_id)
            if detail is None:
                self.write_json({"error": "Article not found"}, HTTPStatus.NOT_FOUND)
                return
            self.write_json(detail)
            return
        self.write_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def handle_api_post(self, path: str, payload: dict[str, object]) -> None:
        user_id = str(payload.get("user_id", "demo"))
        if path == "/api/interact":
            event_id = str(payload.get("event_id", ""))
            action = str(payload.get("action", "click"))
            profile = STORE.add_interaction(user_id, event_id, action)
            self.write_json({"ok": True, "profile": profile})
            return
        if path == "/api/onboarding":
            interests = payload.get("interests")
            if not isinstance(interests, dict):
                raise ValueError("interests must be an object")
            city = str(payload.get("city", "Bangalore"))
            radius_km = safe_int(payload.get("radius_km", 25), 25, lo=1, hi=500)
            context = payload.get("context")
            if context is not None and not isinstance(context, dict):
                raise ValueError("context must be an object")
            profile = STORE.save_profile(
                user_id,
                city=city,
                radius_km=radius_km,
                interests=interests,
                context=context,
            )
            self.write_json({"ok": True, "profile": profile})
            return
        if path == "/api/ingest/live":
            limit = safe_int(payload.get("limit_per_source", 5), 5, lo=1, hi=20)
            if bool(payload.get("wait")):
                # Legacy synchronous mode (used by curl examples/tests).
                self.write_json(run_live_ingest(limit_per_source=limit, reason="manual"))
                return
            started = start_background_ingest(limit_per_source=limit, reason="manual")
            self.write_json(
                {
                    "ok": True,
                    "started": started,
                    "running": True,
                    "reason": "manual" if started else "already-running",
                }
            )
            return
        if path == "/api/interact/remove":
            event_id = str(payload.get("event_id", ""))
            removed = STORE.remove_interaction(user_id, event_id)
            self.write_json({"ok": True, "removed": removed})
            return
        self.write_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def accepts_gzip(self) -> bool:
        return "gzip" in self.headers.get("Accept-Encoding", "")

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data, gz = load_static_cached(target)
        use_gzip = gz is not None and self.accepts_gzip()
        body = gz if use_gzip else data
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        # Short-lived cache: instant repeat loads, but edits show within a minute.
        self.send_header("Cache-Control", "public, max-age=60")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def write_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, cls=ApiEncoder).encode("utf-8")
        use_gzip = len(data) > 1024 and self.accepts_gzip()
        if use_gzip:
            data = gzip.compress(data, compresslevel=6)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the zero-cost ContextCast MVP.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.environ.get("PORT", "8000")), type=int)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    start_auto_ingest()
    print(f"ContextCast running at http://{args.host}:{args.port}")
    print("Cost profile: $0. Public free feeds auto-refresh every 5 minutes.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ContextCast.")


if __name__ == "__main__":
    main()
