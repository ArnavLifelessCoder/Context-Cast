from __future__ import annotations

import re
from collections import Counter

from .models import TOPIC_KEYWORDS


STOPWORDS = {
    "about",
    "after",
    "again",
    "all",
    "also",
    "and",
    "any",
    "are",
    "but",
    "can",
    "for",
    "from",
    "have",
    "how",
    "into",
    "just",
    "more",
    "not",
    "one",
    "our",
    "out",
    "that",
    "the",
    "their",
    "there",
    "this",
    "was",
    "with",
    "your",
    "will",
    "would",
    "https",
    "www",
    "submitted",
    "comments",
    "link",
}


def summarize_text(title: str, body: str, topic: str = "", city: str = "", max_chars: int = 210) -> str:
    text = clean_summary_input(body or title)
    if not text:
        return title[:max_chars]

    sentences = split_sentences(text)
    if len(sentences) == 1:
        return trim_sentence(sentences[0], max_chars)

    topic_terms = set(TOPIC_KEYWORDS.get(topic, ()))
    city_terms = {city.lower()} if city else set()
    title_terms = set(tokenize(title))
    global_counts = Counter(tokenize(text))

    scored: list[tuple[float, int, str]] = []
    for index, sentence in enumerate(sentences[:8]):
        terms = tokenize(sentence)
        if not terms:
            continue
        score = sum(global_counts[term] for term in terms)
        score += 3 * sum(1 for term in terms if term in topic_terms)
        score += 2 * sum(1 for term in terms if term in title_terms)
        score += 2 * sum(1 for term in terms if term in city_terms)
        score -= 0.25 * index
        scored.append((score / max(len(terms), 1), index, sentence))

    picked = sorted(scored, reverse=True)[:2]
    picked = sorted(picked, key=lambda item: item[1])
    summary = " ".join(item[2] for item in picked)
    return trim_sentence(summary, max_chars)


def clean_summary_input(value: str) -> str:
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"\bsubmitted by /u/\S+.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\[(?:link|comments)\]", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -")


def split_sentences(value: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", value)
    return [part.strip() for part in parts if len(part.strip()) > 24]


def tokenize(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in STOPWORDS
    ]


def trim_sentence(value: str, max_chars: int) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    trimmed = value[: max_chars - 1].rsplit(" ", 1)[0].strip()
    return f"{trimmed}."
