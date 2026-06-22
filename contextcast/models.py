from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


TOPICS = (
    "tech",
    "startups",
    "news",
    "music",
    "art",
    "sports",
    "community",
    "food",
    "film",
    "comedy",
    "education",
    "career",
    "gaming",
    "wellness",
    "finance",
    "other",
)


TOPIC_KEYWORDS = {
    "tech": ("python", "api", "cloud", "startup", "ai", "data", "hack", "code"),
    "startups": ("startup", "founder", "pitch", "vc", "funding", "saas", "product"),
    "news": ("news", "policy", "election", "metro", "civic", "update", "breaking"),
    "music": ("music", "gig", "jazz", "band", "acoustic", "concert", "dj"),
    "art": ("art", "gallery", "painting", "design", "craft", "illustration"),
    "sports": ("run", "yoga", "football", "cricket", "cycling", "fitness"),
    "community": ("volunteer", "cleanup", "neighborhood", "civic", "club"),
    "food": ("food", "coffee", "baking", "tasting", "brew", "culinary"),
    "film": ("film", "screening", "cinema", "documentary", "shorts"),
    "comedy": ("comedy", "standup", "improv", "open mic", "mic"),
    "education": ("workshop", "seminar", "lecture", "class", "bootcamp"),
    "career": ("job", "career", "hiring", "internship", "resume", "interview"),
    "gaming": ("gaming", "esports", "game", "tournament", "valorant", "board game"),
    "wellness": ("wellness", "mindfulness", "health", "meditation", "mobility", "yoga"),
    "finance": ("finance", "investing", "stock", "market", "crypto", "fintech"),
    "other": (),
}


# Words that strongly imply a real, attendable event (vs. a news article).
EVENT_SIGNALS = (
    "workshop", "meetup", "meet-up", "conference", "summit", "hackathon",
    "webinar", "bootcamp", "seminar", "festival", "fest", "concert", "gig",
    "exhibition", "expo", "screening", "open mic", "standup", "stand-up",
    "tournament", "marathon", "walkathon", "fair", "popup", "pop-up",
    "tickets", "rsvp", "register now", "registration", "book now", "venue",
    "doors open", "lineup", "line-up", "performance", "showcase", "jam",
    "masterclass", "networking", "mixer", "demo day", "career fair",
)

# Words that imply hard news / reporting (helps demote false-positive events).
NEWS_SIGNALS = (
    "says", "announces", "report", "reported", "according to", "police",
    "government", "minister", "court", "verdict", "arrested", "killed",
    "dies", "death", "election", "poll", "verdict", "bill", "policy",
    "stocks", "market", "shares", "gdp", "inflation", "weather", "rain",
    "breaking", "update", "alert", "crisis",
)


CITY_CENTER = {
    "Bangalore": (12.9716, 77.5946),
    "Chennai": (13.0827, 80.2707),
    "Delhi": (28.6139, 77.2090),
    "Mumbai": (19.0760, 72.8777),
    "Pune": (18.5204, 73.8567),
    "Hyderabad": (17.3850, 78.4867),
    "Kolkata": (22.5726, 88.3639),
    "Ahmedabad": (23.0225, 72.5714),
    "Kochi": (9.9312, 76.2673),
    "Jaipur": (26.9124, 75.7873),
    "Remote": (20.5937, 78.9629),
}


@dataclass(frozen=True)
class Event:
    id: str
    source: str
    title: str
    description: str
    city: str
    venue: str
    lat: float
    lon: float
    topic: str
    event_date: datetime
    fetched_at: datetime
    url: str = ""
    kind: str = "event"
    summary: str = ""
    published_at: datetime | None = None
    image_url: str = ""
    source_domain: str = ""


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    city: str
    radius_km: int
    interests: dict[str, float]
    context: dict[str, object]


@dataclass(frozen=True)
class ScoredEvent:
    event: Event
    score: float
    semantic_score: float
    proximity_score: float
    recency_score: float
    graph_score: float
    explanation: str
    content_score: float = 0.0
    diversity_score: float = 0.0
    novelty_score: float = 0.0
    momentum_score: float = 0.0
