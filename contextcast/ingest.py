from __future__ import annotations

import hashlib
import html
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from .models import (
    CITY_CENTER,
    EVENT_SIGNALS,
    NEWS_SIGNALS,
    TOPIC_KEYWORDS,
    TOPICS,
)
from .summarizer import summarize_text


USER_AGENT = "ContextCastZeroCost/1.0 (+local demo; no paid API keys)"

# Some hosts (notably Reddit) rate-limit unauthenticated clients hard and
# return HTTP 429 if we fire many requests back-to-back. We space out requests
# per host so every feed gets a fair shot instead of only the first one.
HOST_MIN_INTERVAL = {
    "reddit.com": 1.5,
    "www.reddit.com": 1.5,
}
DEFAULT_MIN_INTERVAL = 0.0
_LAST_FETCH: dict[str, float] = {}
_FETCH_LOCK = threading.Lock()

RSS_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str
    city: str
    kind: str


def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def gnews(query: str) -> str:
    encoded = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"


# Every city in CITY_CENTER (except Remote) gets full local coverage:
# general news, an events/happenings query, and a civic/local-issues query.
CITY_SUBREDDITS = {
    "Bangalore": "bangalore",
    "Chennai": "chennai",
    "Delhi": "delhi",
    "Mumbai": "mumbai",
    "Pune": "pune",
    "Hyderabad": "hyderabad",
    "Kolkata": "kolkata",
    "Ahmedabad": "ahmedabad",
    "Kochi": "Kochi",
    "Jaipur": "jaipur",
}


def free_sources() -> list[FeedSource]:
    sources: list[FeedSource] = []
    cities = [city for city in CITY_CENTER if city != "Remote"]

    # ── Per-city coverage: news, events, and civic context ───
    for city in cities:
        sources.append(
            FeedSource(
                name=f"{city} News",
                url=gnews(f"{city} latest news"),
                city=city,
                kind="news",
            )
        )
        sources.append(
            FeedSource(
                name=f"{city} Events",
                url=gnews(
                    f"{city} events OR meetup OR workshop OR concert OR festival OR exhibition"
                ),
                city=city,
                kind="event",
            )
        )
        sources.append(
            FeedSource(
                name=f"{city} Civic",
                url=gnews(f"{city} traffic OR metro OR civic OR weather OR local issues"),
                city=city,
                kind="discussion",
            )
        )

    # ── Reddit: one general feed per city (events surface via detect_kind) ──
    # Kept to one request per subreddit: Reddit rate-limits unauthenticated
    # clients, and fetch_text throttles reddit.com to stay under the limit.
    for city, subreddit in CITY_SUBREDDITS.items():
        sources.append(
            FeedSource(
                name=f"r/{subreddit}",
                url=f"https://www.reddit.com/r/{subreddit}/new.rss",
                city=city,
                kind="discussion",
            )
        )

    # ── Indian national news (real news feeds, no API key) ───
    sources.extend([
        FeedSource("The Hindu", "https://www.thehindu.com/feeder/default.rss", "Remote", "news"),
        FeedSource("Indian Express", "https://indianexpress.com/feed/", "Remote", "news"),
        FeedSource("NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories", "Remote", "news"),
        FeedSource("Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "Remote", "news"),
        FeedSource("Hindustan Times", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml", "Remote", "news"),
        FeedSource("News18", "https://www.news18.com/commonfeeds/v1/eng/rss/india.xml", "Remote", "news"),
        FeedSource("Firstpost", "https://www.firstpost.com/commonfeeds/v1/mfp/rss/india.xml", "Remote", "news"),
        FeedSource("Scroll.in", "https://feeds.feedburner.com/ScrollinArticles.rss", "Remote", "news"),
        FeedSource("The Wire", "https://thewire.in/rss", "Remote", "news"),
    ])

    # ── Topical Indian news (business, sports, entertainment) ─
    sources.extend([
        FeedSource("ToI Business", "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms", "Remote", "news"),
        FeedSource("ToI Sports", "https://timesofindia.indiatimes.com/rssfeeds/4719148.cms", "Remote", "news"),
        FeedSource("ToI Entertainment", "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms", "Remote", "news"),
        FeedSource("The Hindu Sport", "https://www.thehindu.com/sport/feeder/default.rss", "Remote", "news"),
        FeedSource("Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml", "Remote", "news"),
        FeedSource("LiveMint", "https://www.livemint.com/rss/news", "Remote", "news"),
    ])

    # ── International tech/startup news ──────────────────────
    sources.extend([
        FeedSource("Hacker News", "https://news.ycombinator.com/rss", "Remote", "news"),
        FeedSource("TechCrunch", "https://techcrunch.com/feed/", "Remote", "news"),
        FeedSource("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "Remote", "news"),
        FeedSource("The Verge", "https://www.theverge.com/rss/index.xml", "Remote", "news"),
        FeedSource("Wired", "https://www.wired.com/feed/rss", "Remote", "news"),
        FeedSource("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "Remote", "news"),
        FeedSource("Reuters Tech", "https://www.reutersagency.com/feed/?best-topics=tech&post_type=best", "Remote", "news"),
        FeedSource("Dev.to", "https://dev.to/feed/", "Remote", "signal"),
        FeedSource("Dev.to Events", "https://dev.to/feed/tag/events", "Remote", "event"),
        FeedSource("Product Hunt", "https://www.producthunt.com/feed", "Remote", "signal"),
        FeedSource("Lobsters", "https://lobste.rs/rss", "Remote", "news"),
        FeedSource("Reddit r/technology", "https://www.reddit.com/r/technology/new.rss", "Remote", "news"),
        FeedSource("Reddit r/programming", "https://www.reddit.com/r/programming/new.rss", "Remote", "signal"),
        FeedSource("Reddit r/startups", "https://www.reddit.com/r/startups/new.rss", "Remote", "signal"),
    ])

    # ── Indian tech/startup news ─────────────────────────────
    sources.extend([
        FeedSource("YourStory", "https://yourstory.com/feed", "Remote", "news"),
        FeedSource("Inc42", "https://inc42.com/feed/", "Remote", "news"),
        FeedSource("Entrackr", "https://entrackr.com/feed", "Remote", "news"),
        FeedSource("The Ken", "https://the-ken.com/feed/", "Remote", "news"),
    ])

    # ── Events and culture ───────────────────────────────────
    sources.extend([
        FeedSource("Insider.in", "https://insider.in/api/v2/events/rss", "Remote", "event"),
        FeedSource("Meetup Tech", "https://www.meetup.com/find/?source=GROUP&keywords=tech&sort=best_match&rss=1", "Remote", "event"),
    ])

    return sources


def ingest_free_feeds(limit_per_source: int = 6) -> dict[str, object]:
    events: list[dict[str, object]] = []
    statuses: list[dict[str, object]] = []
    for source in free_sources():
        try:
            text = fetch_with_fallback(source.url)
            parsed = parse_feed(source, text, limit_per_source)
            events.extend(parsed)
            statuses.append({"source": source.name, "ok": True, "count": len(parsed), "error": ""})
        except Exception as exc:
            statuses.append({"source": source.name, "ok": False, "count": 0, "error": str(exc)[:140]})
    return {"events": events, "statuses": statuses}


def fetch_with_fallback(url: str) -> str:
    """Fetch a feed, retrying Reddit on the old.reddit.com host on 429.

    old.reddit.com occasionally serves when www.reddit.com is rate-limited.
    """
    try:
        return fetch_text(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and "://www.reddit.com" in url:
            return fetch_text(url.replace("://www.reddit.com", "://old.reddit.com"))
        raise


def throttle_host(url: str) -> None:
    """Block until the per-host minimum interval has elapsed since the last hit."""
    host = urlparse(url).netloc.lower()
    interval = HOST_MIN_INTERVAL.get(host, DEFAULT_MIN_INTERVAL)
    if interval <= 0:
        return
    with _FETCH_LOCK:
        now = time.monotonic()
        wait = interval - (now - _LAST_FETCH.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST_FETCH[host] = time.monotonic()


def fetch_text(url: str, timeout: int = 8) -> str:
    throttle_host(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_feed(source: FeedSource, text: str, limit: int) -> list[dict[str, object]]:
    root = ET.fromstring(text)
    if root.tag.endswith("feed"):
        items = root.findall("atom:entry", RSS_NS)
        return [atom_entry_to_event(source, item) for item in items[:limit]]
    channel = root.find("channel")
    if channel is None:
        return []
    items = channel.findall("item")
    return [rss_item_to_event(source, item) for item in items[:limit]]


def extract_image_from_item(item: ET.Element) -> str:
    # Check media:content or media:thumbnail
    for tag in ["media:content", "media:thumbnail"]:
        el = item.find(tag, RSS_NS)
        if el is not None:
            url = el.attrib.get("url", "")
            if url:
                return url
    # Check enclosure
    enc = item.find("enclosure")
    if enc is not None:
        enc_type = enc.attrib.get("type", "")
        if "image" in enc_type:
            return enc.attrib.get("url", "")
    # Check for image in description HTML
    desc = find_text(item, "description") or find_text(item, "{http://purl.org/rss/1.0/modules/content/}encoded")
    if desc:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
        if img_match:
            return img_match.group(1)
    return ""


def extract_image_from_atom(item: ET.Element) -> str:
    # Check media:content
    el = item.find("media:content", RSS_NS)
    if el is not None:
        url = el.attrib.get("url", "")
        if url:
            return url
    # Check content for img tags
    content = find_text(item, "atom:content") or find_text(item, "atom:summary")
    if content:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content)
        if img_match:
            return img_match.group(1)
    return ""


def rss_item_to_event(source: FeedSource, item: ET.Element) -> dict[str, object]:
    title = clean_text(find_text(item, "title") or "Untitled signal")
    description = clean_text(
        find_text(item, "description")
        or find_text(item, "{http://purl.org/rss/1.0/modules/content/}encoded")
        or title
    )
    url = find_text(item, "link") or source.url
    published = parse_date(find_text(item, "pubDate") or find_text(item, "published"))
    image_url = extract_image_from_item(item)
    return build_event(source, title, description, url, published, image_url)


def atom_entry_to_event(source: FeedSource, item: ET.Element) -> dict[str, object]:
    title = clean_text(find_text(item, "atom:title") or "Untitled signal")
    description = clean_text(find_text(item, "atom:summary") or find_text(item, "atom:content") or title)
    link = item.find("atom:link", RSS_NS)
    url = link.attrib.get("href", source.url) if link is not None else source.url
    published = parse_date(find_text(item, "atom:published") or find_text(item, "atom:updated"))
    image_url = extract_image_from_atom(item)
    return build_event(source, title, description, url, published, image_url)


def build_event(
    source: FeedSource, title: str, description: str, url: str, published: datetime, image_url: str = ""
) -> dict[str, object]:
    text = f"{title} {description}"
    topic = classify_topic(text)
    kind = detect_kind(text, source)
    lat, lon = CITY_CENTER.get(source.city, CITY_CENTER["Remote"])
    fingerprint = hashlib.sha256(f"{source.name}|{title}|{url}".encode("utf-8")).hexdigest()[:18]
    clean_description = description[:800] or title
    summary = summarize_text(title, clean_description, topic=topic, city=source.city)
    source_domain = extract_domain(url) or extract_domain(source.url)
    return {
        "id": f"live-{fingerprint}",
        "source": source.name,
        "title": title[:200],
        "description": clean_description,
        "city": source.city,
        "venue": source.name,
        "lat": lat,
        "lon": lon,
        "topic": topic,
        "event_date": published.isoformat(),
        "published_at": published.isoformat(),
        "url": url,
        "kind": kind,
        "summary": summary,
        "image_url": image_url,
        "source_domain": source_domain,
    }


def detect_kind(text: str, source: FeedSource) -> str:
    """Classify each item as event / news / discussion / signal.

    Source kind is the prior, but strong per-item signals can override it so a
    "Bangalore Tech Summit this Saturday" article from a news feed is correctly
    surfaced as an event rather than buried as generic news.
    """
    lowered = text.lower()
    event_hits = sum(1 for term in EVENT_SIGNALS if term in lowered)
    news_hits = sum(1 for term in NEWS_SIGNALS if term in lowered)

    # Strong, unambiguous event language promotes anything to an event.
    if event_hits >= 2 and event_hits > news_hits:
        return "event"

    # Respect explicit event/discussion sources unless they read like hard news.
    if source.kind == "event":
        return "event" if news_hits < 2 else "news"
    if source.kind == "discussion":
        return "discussion"
    if source.kind == "signal":
        return "event" if event_hits >= 2 else "signal"

    # News sources: a single clear event cue is enough to reclassify.
    if event_hits >= 1 and news_hits == 0:
        return "event"
    return "news"


def classify_topic(text: str) -> str:
    lowered = text.lower()
    scores: dict[str, int] = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        scores[topic] = sum(keyword_hits(lowered, keyword) for keyword in keywords)
    topic, score = max(scores.items(), key=lambda item: item[1])
    return topic if score > 0 and topic in TOPICS else "news"


def keyword_hits(text: str, keyword: str) -> int:
    escaped = re.escape(keyword.lower())
    if " " in keyword:
        return len(re.findall(escaped, text))
    return len(re.findall(rf"\b{escaped}\b", text))


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def find_text(item: ET.Element, path: str) -> str:
    found = item.find(path, RSS_NS)
    return found.text.strip() if found is not None and found.text else ""


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()
