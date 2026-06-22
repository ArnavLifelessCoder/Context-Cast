from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import asin, cos, exp, radians, sin, sqrt
from urllib.parse import urlparse

from .models import CITY_CENTER, Event, ScoredEvent, TOPICS, UserProfile


TOPIC_NEIGHBORS = {
    "tech": {"education": 0.35, "startups": 0.28, "career": 0.18, "news": 0.12},
    "startups": {"tech": 0.28, "finance": 0.22, "career": 0.16, "news": 0.12},
    "news": {"community": 0.18, "finance": 0.14, "tech": 0.12},
    "education": {"tech": 0.35, "community": 0.18, "career": 0.16},
    "music": {"art": 0.18, "comedy": 0.12, "food": 0.1},
    "art": {"film": 0.22, "music": 0.18},
    "film": {"art": 0.22, "comedy": 0.12},
    "food": {"community": 0.16, "music": 0.1},
    "community": {"education": 0.18, "food": 0.16, "sports": 0.1, "news": 0.18},
    "sports": {"community": 0.1, "wellness": 0.2},
    "comedy": {"music": 0.12, "film": 0.12},
    "career": {"tech": 0.18, "education": 0.16, "startups": 0.16},
    "gaming": {"tech": 0.16, "community": 0.1},
    "wellness": {"sports": 0.2, "community": 0.12},
    "finance": {"startups": 0.22, "news": 0.14, "tech": 0.08},
    "other": {},
}

# Source authority weights (higher = more trusted)
SOURCE_AUTHORITY = {
    "Hacker News": 0.9,
    "TechCrunch": 0.88,
    "The Verge": 0.85,
    "Ars Technica": 0.87,
    "The Hindu": 0.82,
    "NDTV": 0.80,
    "Indian Express": 0.78,
    "Hindustan Times": 0.78,
    "Times of India": 0.72,
    "YourStory": 0.80,
    "Inc42": 0.78,
    "Entrackr": 0.74,
    "The Ken": 0.84,
    "Product Hunt": 0.75,
    "Dev.to": 0.70,
    "Lobsters": 0.82,
    "BBC World": 0.90,
    "Reuters Tech": 0.90,
    "Wired": 0.85,
    "Scroll.in": 0.80,
    "The Wire": 0.79,
    "News18": 0.72,
    "Firstpost": 0.72,
    "Moneycontrol": 0.80,
    "LiveMint": 0.82,
}
DEFAULT_AUTHORITY = 0.55

STOPWORDS = {
    "about", "after", "again", "all", "also", "and", "any", "are", "but",
    "can", "for", "from", "have", "how", "into", "just", "more", "not",
    "one", "our", "out", "that", "the", "their", "there", "this", "was",
    "with", "your", "will", "would", "https", "www", "com", "the", "and",
    "for", "are", "was", "were", "been", "has", "had", "his", "her", "its",
    "you", "she", "him", "our", "who", "did", "does", "any", "not", "but",
    "from", "with", "this", "that", "these", "those", "some", "what",
}


def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def tokenize_text(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 2 and t not in STOPWORDS]


def build_ngrams(text: str, n: int = 2) -> list[str]:
    tokens = tokenize_text(text)
    if len(tokens) < n:
        return tokens
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def tfidf_vector(text: str, idf: dict[str, float] | None = None) -> dict[str, float]:
    tokens = tokenize_text(text)
    if not tokens:
        return {}
    tf = Counter(tokens)
    total = len(tokens)
    vec: dict[str, float] = {}
    for term, count in tf.items():
        tf_val = count / total
        idf_val = idf.get(term, 1.0) if idf else 1.0
        vec[term] = tf_val * idf_val
    return vec


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_corpus_idf(events: list[Event]) -> dict[str, float]:
    n = len(events)
    if n == 0:
        return {}
    df: Counter[str] = Counter()
    for event in events:
        terms = set(tokenize_text(f"{event.title} {event.description}"))
        for term in terms:
            df[term] += 1
    idf = {}
    for term, freq in df.items():
        idf[term] = math.log(n / (1 + freq)) + 1
    return idf


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * radius * asin(sqrt(a))


def recency_score(event_date: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    days = abs((event_date - now).total_seconds() / 86400)
    return exp(-days / 14)


def semantic_score(profile: UserProfile, event: Event) -> float:
    direct = profile.interests.get(event.topic, 0.0)
    neighbor_bonus = 0.0
    for topic, weight in profile.interests.items():
        neighbor_bonus += weight * TOPIC_NEIGHBORS.get(topic, {}).get(event.topic, 0.0)
    return min(1.0, direct + neighbor_bonus)


def proximity_score(profile: UserProfile, event: Event) -> float:
    center = CITY_CENTER.get(profile.city, CITY_CENTER["Bangalore"])
    distance = haversine_km(center[0], center[1], event.lat, event.lon)
    radius = max(profile.radius_km, 1)
    return exp(-distance / radius)


def graph_score(profile: UserProfile, event: Event) -> float:
    total = sum(max(value, 0.0) for value in profile.interests.values()) or 1.0
    return max(profile.interests.get(event.topic, 0.0), 0.0) / total


def content_similarity_score(profile: UserProfile, event: Event, idf: dict[str, float] | None = None) -> float:
    goal = str(profile.context.get("goal", "")).strip()
    domain = str(profile.context.get("domain", "")).strip()
    if not goal and not domain:
        return 0.5
    profile_text = f"{goal} {domain} {' '.join(profile.interests.keys())}"
    event_text = f"{event.title} {event.description} {event.topic}"
    profile_vec = tfidf_vector(profile_text, idf)
    event_vec = tfidf_vector(event_text, idf)
    sim = cosine_similarity(profile_vec, event_vec)
    return min(1.0, sim * 3.0)  # scale up since cosine sim tends to be low


def novelty_score(profile: UserProfile, event: Event) -> float:
    interest_weight = profile.interests.get(event.topic, 0.0)
    if interest_weight < 0.1:
        return 0.85
    if interest_weight < 0.3:
        return 0.65
    return max(0.2, 1.0 - interest_weight)


def source_authority_score(event: Event) -> float:
    return SOURCE_AUTHORITY.get(event.source, DEFAULT_AUTHORITY)


def context_score(profile: UserProfile, event: Event) -> float:
    signal_types = profile.context.get("signal_types", ["event", "news", "signal", "discussion"])
    if not isinstance(signal_types, list):
        signal_types = ["event", "news", "signal", "discussion"]
    kind_score = 1.0 if event.kind in signal_types else 0.35
    domain = str(profile.context.get("domain", "")).lower()
    domain_score = 0.0
    text = f"{event.title} {event.description} {event.topic}".lower()
    if domain and domain != "builder":
        domain_score = 0.4 if domain in text else 0.0
    freshness = str(profile.context.get("freshness", "balanced"))
    fresh_score = recency_score(event.event_date) if freshness == "latest" else 0.65
    return min(1.0, 0.55 * kind_score + 0.25 * fresh_score + 0.20 * domain_score)


def explain(
    profile: UserProfile,
    event: Event,
    semantic: float,
    proximity: float,
    context: float,
    content: float,
    novelty: float,
) -> str:
    goal = str(profile.context.get("goal", "")).strip()
    parts: list[str] = []

    if semantic >= 0.75:
        parts.append(f"Strong {event.topic} match")
    elif semantic >= 0.4:
        parts.append(f"Good {event.topic} relevance")

    if content >= 0.6 and goal:
        parts.append(f"aligns with your goal")
    elif content >= 0.4:
        parts.append(f"content matches your profile")

    if proximity >= 0.82:
        parts.append(f"nearby in {event.city}")
    elif proximity >= 0.5:
        parts.append(f"in {event.city}")

    if novelty >= 0.7:
        parts.append("explores a new area for you")

    authority = source_authority_score(event)
    if authority >= 0.8:
        parts.append(f"from trusted source ({event.source})")

    if not parts:
        top_interest = max(profile.interests.items(), key=lambda item: item[1], default=(event.topic, 0.5))[0]
        parts.append(f"broadens your {top_interest} profile with a {event.topic} signal")

    return ". ".join(p.capitalize() if i == 0 else p for i, p in enumerate(parts)) + "."


def score_events(
    events: list[Event],
    profile: UserProfile,
    limit: int = 10,
    interactions: list[dict] | None = None,
) -> list[ScoredEvent]:
    idf = build_corpus_idf(events)

    # Build interaction momentum (recent saves/clicks boost related topics)
    momentum_topics: dict[str, float] = {}
    if interactions:
        for interaction in interactions[-50:]:  # last 50
            action = interaction.get("action", "")
            topic = interaction.get("topic", "")
            if action in {"save", "click", "attend"} and topic:
                momentum_topics[topic] = momentum_topics.get(topic, 0.0) + 0.15

    scored: list[ScoredEvent] = []
    for event in events:
        semantic = semantic_score(profile, event)
        proximity = proximity_score(profile, event)
        recency = recency_score(event.event_date)
        graph = graph_score(profile, event)
        context = context_score(profile, event)
        content = content_similarity_score(profile, event, idf)
        novelty = novelty_score(profile, event)
        authority = source_authority_score(event)
        momentum = min(1.0, momentum_topics.get(event.topic, 0.0))

        # Weighted ensemble
        final = (
            0.22 * semantic
            + 0.12 * proximity
            + 0.12 * recency
            + 0.06 * graph
            + 0.10 * context
            + 0.18 * content
            + 0.05 * novelty
            + 0.07 * authority
            + 0.08 * momentum
        )

        scored.append(
            ScoredEvent(
                event=event,
                score=round(final, 4),
                semantic_score=round(semantic, 4),
                proximity_score=round(proximity, 4),
                recency_score=round(recency, 4),
                graph_score=round(graph, 4),
                explanation=explain(profile, event, semantic, proximity, context, content, novelty),
                content_score=round(content, 4),
                novelty_score=round(novelty, 4),
                momentum_score=round(momentum, 4),
            )
        )

    # Sort by score
    scored.sort(key=lambda item: item.score, reverse=True)

    # Apply diversity re-ranking: penalize consecutive same-topic items
    reranked = diversity_rerank(scored, max_same_topic=3, penalty=0.15)

    return reranked[:limit]


def diversity_rerank(
    scored: list[ScoredEvent], max_same_topic: int = 3, penalty: float = 0.15
) -> list[ScoredEvent]:
    if len(scored) <= max_same_topic:
        return scored

    result: list[ScoredEvent] = []
    topic_count: dict[str, int] = defaultdict(int)

    remaining = list(scored)
    while remaining:
        best_idx = 0
        best_adjusted = -1.0
        for i, item in enumerate(remaining):
            topic = item.event.topic
            count = topic_count.get(topic, 0)
            div_penalty = penalty * max(0, count - max_same_topic + 1)
            adjusted = item.score - div_penalty
            diversity_bonus = item.diversity_score if item.diversity_score else 0
            adjusted += diversity_bonus * 0.05
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_idx = i

        picked = remaining.pop(best_idx)
        topic_count[picked.event.topic] = topic_count.get(picked.event.topic, 0) + 1

        # Compute diversity score for picked item
        count_in_result = topic_count[picked.event.topic]
        div_score = max(0.0, 1.0 - (count_in_result - 1) * 0.2)
        picked = ScoredEvent(
            event=picked.event,
            score=picked.score,
            semantic_score=picked.semantic_score,
            proximity_score=picked.proximity_score,
            recency_score=picked.recency_score,
            graph_score=picked.graph_score,
            explanation=picked.explanation,
            content_score=picked.content_score,
            diversity_score=round(div_score, 4),
            novelty_score=picked.novelty_score,
            momentum_score=picked.momentum_score,
        )
        result.append(picked)

    return result


def update_interests(
    interests: dict[str, float], topic: str, action: str, alpha: float = 0.18
) -> dict[str, float]:
    updated = {topic_name: float(interests.get(topic_name, 0.0)) for topic_name in TOPICS}
    if action in {"save", "click", "attend"}:
        boost = {"click": 0.55, "save": 0.85, "attend": 1.0}.get(action, 0.5)
        updated[topic] = (1 - alpha) * updated.get(topic, 0.0) + alpha * boost
    elif action == "not_interested":
        updated[topic] = max(0.0, updated.get(topic, 0.0) - alpha)

    return {key: round(min(value, 1.0), 4) for key, value in updated.items() if value > 0.01}


def graph_payload(profile: UserProfile, events: list[Event]) -> dict[str, list[dict[str, object]]]:
    nodes: list[dict[str, object]] = [
        {"id": profile.user_id, "label": "You", "type": "user", "weight": 1.0}
    ]
    edges: list[dict[str, object]] = []

    for topic, weight in sorted(profile.interests.items(), key=lambda item: -item[1]):
        nodes.append({"id": f"topic:{topic}", "label": topic.title(), "type": "topic", "weight": weight})
        edges.append({"source": profile.user_id, "target": f"topic:{topic}", "weight": weight})

    for event in events[:60]:
        if event.topic in profile.interests:
            nodes.append({"id": event.id, "label": event.title, "type": "event", "weight": 0.45})
            edges.append({"source": f"topic:{event.topic}", "target": event.id, "weight": 0.45})

    return {"nodes": nodes, "edges": edges}
