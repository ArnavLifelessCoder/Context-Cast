from __future__ import annotations

import unittest

from contextcast.ingest import FeedSource, classify_topic, parse_feed


RSS = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <item>
      <title>Startup workshop in Mumbai</title>
      <link>https://example.com/startup</link>
      <description>Founders discuss SaaS hiring and product growth.</description>
      <pubDate>Fri, 29 May 2026 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


class IngestTests(unittest.TestCase):
    def test_parse_feed_classifies_and_shapes_event(self) -> None:
        source = FeedSource("Example", "https://example.com/rss", "Mumbai", "news")
        events = parse_feed(source, RSS, 4)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["city"], "Mumbai")
        self.assertEqual(events[0]["topic"], "startups")
        self.assertEqual(events[0]["cost_usd"] if "cost_usd" in events[0] else 0, 0)

    def test_short_keywords_use_word_boundaries(self) -> None:
        self.assertEqual(classify_topic("Am I missing something about firecracker noise?"), "news")


if __name__ == "__main__":
    unittest.main()
