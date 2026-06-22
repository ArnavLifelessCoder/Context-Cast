from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Event, TOPICS, UserProfile
from .recommender import extract_domain, update_interests
from .seed import DEFAULT_PROFILE, SEED_EVENTS, now_iso
from .summarizer import summarize_text


class ManagedConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Store:
    def __init__(self, path: str | Path = "contextcast.db") -> None:
        self.path = Path(path)
        # Ensure the parent directory exists (e.g. a mounted disk path like
        # /var/data) so SQLite can create the file on first boot.
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, factory=ManagedConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    city TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    topic TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    url TEXT DEFAULT '',
                    kind TEXT DEFAULT 'event',
                    summary TEXT DEFAULT '',
                    published_at TEXT
                );

                CREATE TABLE IF NOT EXISTS profiles (
                    user_id TEXT PRIMARY KEY,
                    city TEXT NOT NULL,
                    radius_km INTEGER NOT NULL,
                    interests_json TEXT NOT NULL,
                    context_json TEXT DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._migrate(conn)
            self._seed(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "url" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN url TEXT DEFAULT ''")
        if "kind" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN kind TEXT DEFAULT 'event'")
        if "summary" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN summary TEXT DEFAULT ''")
        if "published_at" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN published_at TEXT")
        if "image_url" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN image_url TEXT DEFAULT ''")
        if "source_domain" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN source_domain TEXT DEFAULT ''")
        conn.execute("UPDATE events SET published_at = event_date WHERE published_at IS NULL")
        profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        if "context_json" not in profile_columns:
            conn.execute("ALTER TABLE profiles ADD COLUMN context_json TEXT DEFAULT '{}'")

    def _seed(self, conn: sqlite3.Connection) -> None:
        fetched_at = now_iso()
        for event in SEED_EVENTS:
            conn.execute(
                """
                INSERT INTO events (
                    id, source, title, description, city, venue, lat, lon,
                    topic, event_date, fetched_at, url, kind, summary, published_at,
                    image_url, source_domain
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    event["id"],
                    event["source"],
                    event["title"],
                    event["description"],
                    event["city"],
                    event["venue"],
                    event["lat"],
                    event["lon"],
                    event["topic"],
                    event["event_date"],
                    fetched_at,
                    event.get("url", ""),
                    event.get("kind", "event"),
                    event.get(
                        "summary",
                        summarize_text(
                            event["title"],
                            event["description"],
                            topic=event["topic"],
                            city=event["city"],
                        ),
                    ),
                    event.get("published_at", fetched_at),
                    event.get("image_url", ""),
                    event.get("source_domain", ""),
                ),
            )
        conn.execute(
            """
            INSERT INTO profiles (user_id, city, radius_km, interests_json, context_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (
                DEFAULT_PROFILE["user_id"],
                DEFAULT_PROFILE["city"],
                DEFAULT_PROFILE["radius_km"],
                json.dumps(DEFAULT_PROFILE["interests"]),
                json.dumps(DEFAULT_PROFILE["context"]),
                fetched_at,
            ),
        )

    def list_events(self) -> list[Event]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY event_date DESC").fetchall()
        return [row_to_event(row) for row in rows]

    def upsert_events(self, events: list[dict[str, object]]) -> int:
        inserted = 0
        fetched_at = now_iso()
        with self.connect() as conn:
            for event in events:
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT INTO events (
                        id, source, title, description, city, venue, lat, lon,
                        topic, event_date, fetched_at, url, kind, summary, published_at,
                        image_url, source_domain
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        topic = excluded.topic,
                        event_date = excluded.event_date,
                        fetched_at = excluded.fetched_at,
                        url = excluded.url,
                        kind = excluded.kind,
                        summary = excluded.summary,
                        published_at = excluded.published_at,
                        image_url = excluded.image_url,
                        source_domain = excluded.source_domain
                    """,
                    (
                        event["id"],
                        event["source"],
                        event["title"],
                        event["description"],
                        event["city"],
                        event["venue"],
                        event["lat"],
                        event["lon"],
                        event["topic"],
                        event["event_date"],
                        fetched_at,
                        event.get("url", ""),
                        event.get("kind", "event"),
                        event.get(
                            "summary",
                            summarize_text(
                                str(event["title"]),
                                str(event["description"]),
                                topic=str(event["topic"]),
                                city=str(event["city"]),
                            ),
                        ),
                        event.get("published_at", event["event_date"]),
                        event.get("image_url", ""),
                        event.get("source_domain", ""),
                    ),
                )
                if conn.total_changes > before:
                    inserted += 1
        return inserted

    def get_event(self, event_id: str) -> Event | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return row_to_event(row) if row else None

    def get_profile(self, user_id: str = "demo") -> UserProfile:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO profiles (user_id, city, radius_km, interests_json, context_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        DEFAULT_PROFILE["city"],
                        DEFAULT_PROFILE["radius_km"],
                        json.dumps(DEFAULT_PROFILE["interests"]),
                        json.dumps(DEFAULT_PROFILE["context"]),
                        now_iso(),
                    ),
                )
                row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        context = DEFAULT_PROFILE["context"] | json.loads(row["context_json"] or "{}")
        return UserProfile(
            user_id=row["user_id"],
            city=row["city"],
            radius_km=int(row["radius_km"]),
            interests=json.loads(row["interests_json"]),
            context=context,
        )

    def save_profile(
        self,
        user_id: str,
        city: str | None = None,
        radius_km: int | None = None,
        interests: dict[str, float] | None = None,
        context: dict[str, object] | None = None,
    ) -> UserProfile:
        current = self.get_profile(user_id)
        clean_interests = interests or current.interests
        clean_interests = {
            topic: float(value)
            for topic, value in clean_interests.items()
            if topic in TOPICS and float(value) > 0
        }
        clean_context = current.context | sanitize_context(context or {})
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE profiles
                SET city = ?, radius_km = ?, interests_json = ?, context_json = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    city or current.city,
                    int(radius_km or current.radius_km),
                    json.dumps(clean_interests),
                    json.dumps(clean_context),
                    now_iso(),
                    user_id,
                ),
            )
        return self.get_profile(user_id)

    def add_interaction(self, user_id: str, event_id: str, action: str) -> UserProfile:
        event = self.get_event(event_id)
        if event is None:
            raise ValueError("Unknown event_id")
        if action not in {"click", "save", "attend", "not_interested"}:
            raise ValueError("Unknown action")

        profile = self.get_profile(user_id)
        interests = update_interests(profile.interests, event.topic, action)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO interactions (user_id, event_id, action, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, event_id, action, now_iso()),
            )
            conn.execute(
                """
                UPDATE profiles
                SET interests_json = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (json.dumps(interests), now_iso(), user_id),
            )
        return self.get_profile(user_id)

    def saved_events(self, user_id: str = "demo") -> list[Event]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*
                FROM events e
                JOIN interactions i ON i.event_id = e.id
                WHERE i.user_id = ? AND i.action IN ('save', 'attend')
                GROUP BY e.id
                ORDER BY MAX(i.created_at) DESC
                """,
                (user_id,),
            ).fetchall()
        return [row_to_event(row) for row in rows]

    def recent_interactions(self, user_id: str = "demo", limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT i.action, e.topic, i.created_at
                FROM interactions i
                JOIN events e ON e.id = i.event_id
                WHERE i.user_id = ?
                ORDER BY i.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [{"action": r["action"], "topic": r["topic"], "created_at": r["created_at"]} for r in rows]

    def article_detail(self, event_id: str) -> dict[str, object] | None:
        event = self.get_event(event_id)
        if event is None:
            return None
        return {
            "event": event,
            "source_domain": event.source_domain or extract_domain(event.url),
            "image_url": event.image_url,
            "url": event.url,
            "description": event.description,
            "summary": event.summary,
            "topic": event.topic,
            "city": event.city,
            "kind": event.kind,
            "published_at": event.published_at,
        }

    def remove_interaction(self,user_id: str, event_id: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM interactions WHERE user_id = ? AND event_id = ? AND action IN ('save', 'attend')",
                (user_id, event_id),
            )
        return cursor.rowcount

    def admin_stats(self) -> dict[str, object]:
        with self.connect() as conn:
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            interactions = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
            topic_rows = conn.execute(
                "SELECT topic, COUNT(*) AS count FROM events GROUP BY topic ORDER BY count DESC"
            ).fetchall()
            source_rows = conn.execute(
                "SELECT source, COUNT(*) AS count FROM events GROUP BY source ORDER BY count DESC LIMIT 12"
            ).fetchall()
            city_rows = conn.execute(
                "SELECT city, COUNT(*) AS count FROM events GROUP BY city ORDER BY count DESC"
            ).fetchall()
            last_fetch = conn.execute("SELECT MAX(fetched_at) FROM events").fetchone()[0]
        return {
            "pipeline": "local-plus-live",
            "cost_usd": 0,
            "events_indexed": events,
            "interactions": interactions,
            "sources": {row["source"]: row["count"] for row in source_rows},
            "cities": {row["city"]: row["count"] for row in city_rows},
            "last_ingest": last_fetch,
            "topic_counts": {row["topic"]: row["count"] for row in topic_rows},
            "health": "ready",
        }

    def portfolio_report(self, user_id: str = "demo") -> str:
        profile = self.get_profile(user_id)
        events = self.list_events()
        saved = self.saved_events(user_id)
        stats = self.admin_stats()
        top_topics = sorted(stats["topic_counts"].items(), key=lambda item: item[1], reverse=True)[:6]
        lines = [
            "# ContextCast Portfolio Report",
            "",
            "## Product",
            "Zero-cost hyperlocal intelligence platform for events, news, and city discussions.",
            "",
            "## Current User Context",
            f"- City: {profile.city}",
            f"- Radius: {profile.radius_km} km",
            f"- Domain: {profile.context.get('domain', 'builder')}",
            f"- Goal: {profile.context.get('goal', '')}",
            f"- Interests: {', '.join(profile.interests)}",
            "",
            "## System Metrics",
            f"- Indexed signals: {len(events)}",
            f"- Free sources: {len(stats['sources'])}",
            f"- Saved signals: {len(saved)}",
            f"- Cost: ${stats['cost_usd']}",
            f"- Auto-refresh: every 5 minutes",
            "",
            "## Top Local Topics",
            *[f"- {topic}: {count}" for topic, count in top_topics],
            "",
            "## CV Bullets",
            f"- Built a zero-cost local intelligence engine indexing {len(events)} public signals across {len(stats['sources'])} sources with auto-refresh, summarization, clustering, and hybrid ranking.",
            "- Implemented local extractive AI summarization, trend detection, semantic clustering, and explainable recommendations without paid LLM APIs.",
            "- Designed a production-style dashboard with source health, personalized context intake, saved intelligence workflow, and exportable portfolio metrics.",
        ]
        return "\n".join(lines)


def row_to_event(row: sqlite3.Row) -> Event:
    published_at = row["published_at"] if "published_at" in row.keys() else None
    summary = row["summary"] if "summary" in row.keys() else ""
    image_url = row["image_url"] if "image_url" in row.keys() else ""
    source_domain = row["source_domain"] if "source_domain" in row.keys() else ""
    return Event(
        id=row["id"],
        source=row["source"],
        title=row["title"],
        description=row["description"],
        city=row["city"],
        venue=row["venue"],
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        topic=row["topic"],
        event_date=datetime.fromisoformat(row["event_date"]),
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        url=row["url"] or "",
        kind=row["kind"] or "event",
        summary=summary
        or summarize_text(row["title"], row["description"], topic=row["topic"], city=row["city"]),
        published_at=datetime.fromisoformat(published_at) if published_at else None,
        image_url=image_url or "",
        source_domain=source_domain or extract_domain(row["url"] or ""),
    )


def sanitize_context(context: dict[str, object]) -> dict[str, object]:
    signal_types = context.get("signal_types", [])
    if not isinstance(signal_types, list):
        signal_types = []
    clean_signal_types = [
        str(value)
        for value in signal_types
        if str(value) in {"event", "news", "signal", "discussion"}
    ]
    return {
        "domain": str(context.get("domain", "builder"))[:40],
        "goal": str(context.get("goal", ""))[:180],
        "signal_types": clean_signal_types or ["event", "news", "signal", "discussion"],
        "freshness": str(context.get("freshness", "balanced"))[:24],
    }
