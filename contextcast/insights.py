from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import log1p

from .models import Event, UserProfile
from .summarizer import tokenize


def build_insights(events: list[Event], profile: UserProfile) -> dict[str, object]:
    city_events = [event for event in events if event.city == profile.city]
    scoped = city_events or events
    now = datetime.now(timezone.utc)

    recent = [
        event
        for event in scoped
        if abs((event_time(event) - now).total_seconds()) <= 7 * 86400
    ]
    older = [event for event in scoped if event not in recent]

    topic_counts = Counter(event.topic for event in scoped)
    kind_counts = Counter(event.kind for event in scoped)
    recent_topics = Counter(event.topic for event in recent)
    older_topics = Counter(event.topic for event in older)

    trend_scores = []
    for topic, count in topic_counts.items():
        recent_count = recent_topics[topic]
        older_count = older_topics[topic]
        lift = (recent_count + 1) / (older_count + 1)
        score = round(log1p(count) * lift, 3)
        trend_scores.append(
            {
                "topic": topic,
                "score": score,
                "recent": recent_count,
                "total": count,
                "label": trend_label(lift, recent_count),
            }
        )
    trend_scores.sort(key=lambda item: item["score"], reverse=True)

    clusters = build_clusters(scoped)
    briefing = build_briefing(profile, scoped, trend_scores, kind_counts)
    opportunities = build_opportunities(profile, scoped, trend_scores)

    return {
        "city": profile.city,
        "briefing": briefing,
        "coverage": {
            "signals": len(scoped),
            "city_signals": len(city_events),
            "sources": len({event.source for event in scoped}),
            "kinds": dict(kind_counts),
            "topics": dict(topic_counts),
        },
        "trends": trend_scores[:8],
        "clusters": clusters[:6],
        "opportunities": opportunities[:5],
        "model_card": build_model_card(events, scoped, profile),
        "diagnostics": build_diagnostics(scoped, topic_counts, kind_counts),
        "recommendation_strategy": [
            "Personal interest weights",
            "City proximity",
            "Freshness decay",
            "Signal-type preference",
            "Local graph affinity",
        ],
    }


def build_diagnostics(
    events: list[Event], topic_counts: Counter[str], kind_counts: Counter[str]
) -> list[dict[str, str]]:
    diagnostics = []
    if len(events) < 12:
        diagnostics.append(
            {"level": "warn", "message": "Low city coverage. Pull live sources or widen the city context."}
        )
    if topic_counts:
        top_topic, top_count = topic_counts.most_common(1)[0]
        if top_count / max(sum(topic_counts.values()), 1) > 0.55:
            diagnostics.append(
                {
                    "level": "watch",
                    "message": f"{top_topic} dominates the current city feed; recommendations may feel narrow.",
                }
            )
    if "discussion" not in kind_counts:
        diagnostics.append(
            {"level": "watch", "message": "No discussion signals indexed for this city yet."}
        )
    if not diagnostics:
        diagnostics.append({"level": "ok", "message": "Coverage looks healthy for a local demo."})
    return diagnostics


def build_clusters(events: list[Event]) -> list[dict[str, object]]:
    buckets: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        buckets[(event.city, event.topic)].append(event)

    clusters = []
    for (city, topic), items in buckets.items():
        terms = Counter()
        for event in items:
            terms.update(tokenize(f"{event.title} {event.summary or event.description}"))
        keywords = [term for term, _ in terms.most_common(5)]
        clusters.append(
            {
                "name": f"{city} / {topic}",
                "city": city,
                "topic": topic,
                "size": len(items),
                "keywords": keywords,
                "lead": items[0].summary or items[0].title,
            }
        )
    clusters.sort(key=lambda item: item["size"], reverse=True)
    return clusters


def build_opportunities(
    profile: UserProfile, events: list[Event], trends: list[dict[str, object]]
) -> list[dict[str, object]]:
    preferred = set(profile.interests)
    trend_topics = {str(item["topic"]) for item in trends[:4]}
    opportunities = []
    for event in sorted(events, key=event_time, reverse=True):
        if event.topic in preferred or event.topic in trend_topics:
            opportunities.append(
                {
                    "title": event.title,
                    "topic": event.topic,
                    "kind": event.kind,
                    "why": opportunity_reason(event, profile, trend_topics),
                    "url": event.url,
                }
            )
    return opportunities


def opportunity_reason(event: Event, profile: UserProfile, trend_topics: set[str]) -> str:
    if event.topic in profile.interests:
        return f"Matches your {event.topic} interest and is active in {event.city}."
    if event.topic in trend_topics:
        return f"{event.topic} is trending locally, useful for exploration."
    return "Relevant local signal for your current context."


def build_briefing(
    profile: UserProfile,
    events: list[Event],
    trends: list[dict[str, object]],
    kind_counts: Counter[str],
) -> list[str]:
    top_trend = trends[0]["topic"] if trends else "local activity"
    top_kind = kind_counts.most_common(1)[0][0] if kind_counts else "signals"
    top_items = sorted(events, key=event_time, reverse=True)[:3]
    lines = [
        f"{profile.city} is currently strongest in {top_trend}, with {top_kind} making up the biggest signal type.",
    ]
    for event in top_items:
        summary = event.summary or event.title
        lines.append(f"{event.kind.title()}: {summary}")
    return lines


def build_model_card(
    all_events: list[Event], scoped_events: list[Event], profile: UserProfile
) -> dict[str, object]:
    total_sources = len({event.source for event in all_events})
    scoped_sources = len({event.source for event in scoped_events})
    kinds = Counter(event.kind for event in all_events)
    freshness_values = [freshness_hours(event) for event in all_events]
    median_freshness = sorted(freshness_values)[len(freshness_values) // 2] if freshness_values else 0
    return {
        "name": "ContextCast Local Hybrid Ranker",
        "cost_usd": 0,
        "signals_indexed": len(all_events),
        "city_signals": len(scoped_events),
        "source_coverage": total_sources,
        "city_source_coverage": scoped_sources,
        "median_freshness_hours": round(median_freshness, 1),
        "signal_mix": dict(kinds),
        "personalization_inputs": {
            "city": profile.city,
            "radius_km": profile.radius_km,
            "topics": list(profile.interests),
            "signal_types": profile.context.get("signal_types", []),
        },
        "cv_metrics": [
            f"Indexes {len(all_events)} local signals across {total_sources} free public sources",
            "Runs zero-cost extractive summarization, trend scoring, clustering, and hybrid ranking",
            f"Personalizes using {len(profile.interests)} topic weights plus city/source/freshness context",
        ],
    }


def freshness_hours(event: Event) -> float:
    age = datetime.now(timezone.utc) - event_time(event)
    return abs(age.total_seconds()) / 3600


def trend_label(lift: float, recent_count: int) -> str:
    if recent_count == 0:
        return "cooling"
    if lift >= 2.0:
        return "spiking"
    if lift >= 1.15:
        return "rising"
    return "steady"


def event_time(event: Event) -> datetime:
    return event.published_at or event.event_date
