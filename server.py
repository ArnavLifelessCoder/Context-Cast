from __future__ import annotations

import argparse
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
        result = ingest_free_feeds(limit_per_source=limit_per_source)
        upserted = STORE.upsert_events(result["events"])
        payload = {
            "ok": True,
            "cost_usd": 0,
            "fetched": len(result["events"]),
            "upserted": upserted,
            "statuses": result["statuses"],
            "reason": reason,
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


def auto_ingest_loop() -> None:
    time.sleep(3)
    while True:
        run_live_ingest(limit_per_source=3, reason="auto")
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
            self.handle_api_post(parsed.path, payload)
        except ValueError as exc:
            self.write_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except json.JSONDecodeError:
            self.write_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)

    def handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        user_id = query.get("user_id", ["demo"])[0]
        if path == "/api/feed":
            profile = STORE.get_profile(user_id)
            limit = int(query.get("limit", ["24"])[0])
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
            radius_km = int(payload.get("radius_km", 25))
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
            result = run_live_ingest(
                limit_per_source=int(payload.get("limit_per_source", 5)),
                reason="manual",
            )
            self.write_json(result)
            return
        if path == "/api/interact/remove":
            event_id = str(payload.get("event_id", ""))
            removed = STORE.remove_interaction(user_id, event_id)
            self.write_json({"ok": True, "removed": removed})
            return
        self.write_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, cls=ApiEncoder).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
