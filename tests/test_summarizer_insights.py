from __future__ import annotations

import unittest
from datetime import datetime, timezone

from contextcast.insights import build_insights
from contextcast.models import Event, UserProfile
from contextcast.summarizer import summarize_text


class SummarizerInsightsTests(unittest.TestCase):
    def test_summary_is_shorter_than_noisy_body(self) -> None:
        body = (
            "Mumbai has a new civic discussion about traffic near the metro. "
            "Residents are comparing commute delays and safety concerns. "
            "submitted by /u/example [link] [comments]"
        )
        summary = summarize_text("Mumbai traffic thread", body, topic="news", city="Mumbai")
        self.assertLessEqual(len(summary), 210)
        self.assertNotIn("submitted by", summary)

    def test_insights_returns_trends_and_clusters(self) -> None:
        event = Event(
            id="x",
            source="test",
            title="Mumbai startup discussion",
            description="Founders discuss SaaS hiring and funding.",
            city="Mumbai",
            venue="Reddit",
            lat=19.076,
            lon=72.8777,
            topic="startups",
            event_date=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
            kind="discussion",
            summary="Founders discuss SaaS hiring and funding.",
            published_at=datetime.now(timezone.utc),
        )
        profile = UserProfile(
            user_id="demo",
            city="Mumbai",
            radius_km=35,
            interests={"startups": 1.0},
            context={"signal_types": ["discussion"], "domain": "founder", "goal": "Find startup context"},
        )
        insights = build_insights([event], profile)
        self.assertGreaterEqual(len(insights["trends"]), 1)
        self.assertGreaterEqual(len(insights["clusters"]), 1)


if __name__ == "__main__":
    unittest.main()
