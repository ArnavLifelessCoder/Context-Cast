from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from contextcast.models import Event, UserProfile
from contextcast.recommender import score_events, update_interests


def profile(city: str = "Bangalore", interests: dict[str, float] | None = None) -> UserProfile:
    return UserProfile(
        user_id="demo",
        city=city,
        radius_km=25,
        interests=interests or {"tech": 1.0},
        context={
            "domain": "builder",
            "goal": "Find useful signals",
            "signal_types": ["event", "news", "signal"],
            "freshness": "latest",
        },
    )


def event(event_id: str, topic: str, days: int = 2) -> Event:
    return Event(
        id=event_id,
        source="test",
        title=f"{topic} event",
        description="test event",
        city="Bangalore",
        venue="Test Hall",
        lat=12.9716,
        lon=77.5946,
        topic=topic,
        event_date=datetime.now(timezone.utc) + timedelta(days=days),
        fetched_at=datetime.now(timezone.utc),
    )


class RecommenderTests(unittest.TestCase):
    def test_scores_prefer_matching_interest(self) -> None:
        scored = score_events([event("music", "music"), event("tech", "tech")], profile())
        self.assertEqual(scored[0].event.topic, "tech")

    def test_negative_feedback_reduces_topic_interest(self) -> None:
        updated = update_interests({"tech": 1.0, "music": 0.5}, "tech", "not_interested")
        self.assertLess(updated["tech"], 1.0)

    def test_positive_feedback_adds_topic(self) -> None:
        updated = update_interests({"tech": 1.0}, "music", "save")
        self.assertIn("music", updated)

    def test_new_topic_taxonomy_ranks_news(self) -> None:
        scored = score_events(
            [event("music", "music"), event("news", "news")],
            profile(city="Kolkata", interests={"news": 1.0}),
        )
        self.assertEqual(scored[0].event.topic, "news")

    def test_context_prefers_selected_signal_type(self) -> None:
        event_item = event("event", "tech")
        news_item = Event(**{**event("news", "tech").__dict__, "kind": "news"})
        event_only = UserProfile(
            user_id="demo",
            city="Bangalore",
            radius_km=25,
            interests={"tech": 1.0},
            context={
                "domain": "builder",
                "goal": "Find meetups",
                "signal_types": ["event"],
                "freshness": "latest",
            },
        )
        scored = score_events([news_item, event_item], event_only)
        self.assertEqual(scored[0].event.kind, "event")


if __name__ == "__main__":
    unittest.main()
