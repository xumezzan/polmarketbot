from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VerdictStrategyMetadata:
    causality_score: float
    event_category: str
    news_quality: str


_EVENT_KEYWORDS: dict[str, set[str]] = {
    "health": {
        "health",
        "hospital",
        "hospitalized",
        "ill",
        "illness",
        "sick",
        "dies",
        "death",
        "medical",
    },
    "court": {
        "court",
        "trial",
        "convict",
        "conviction",
        "convicted",
        "indict",
        "indictment",
        "lawsuit",
        "sentenc",
        "ruling",
    },
    "election": {
        "election",
        "elect",
        "win",
        "nominee",
        "primary",
        "president",
        "senate",
        "house",
        "governor",
        "campaign",
    },
    "war_conflict": {
        "war",
        "ceasefire",
        "conflict",
        "attack",
        "strike",
        "invade",
        "invasion",
        "peace",
        "missile",
        "military",
    },
    "price_target": {
        "price",
        "hit",
        "reach",
        "reaches",
        "reached",
        "high",
        "ath",
        "target",
    },
}


def parse_csv_setting(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def extract_verdict_strategy_metadata(raw_response: dict[str, Any] | None) -> VerdictStrategyMetadata:
    verdict = {}
    if isinstance(raw_response, dict):
        raw_verdict = raw_response.get("verdict")
        if isinstance(raw_verdict, dict):
            verdict = raw_verdict

    return VerdictStrategyMetadata(
        causality_score=_coerce_score(verdict.get("causality_score")),
        event_category=str(verdict.get("event_category") or "OTHER").upper(),
        news_quality=str(verdict.get("news_quality") or "LOW").upper(),
    )


def has_direct_market_event_match(*, query_text: str, market_question: str) -> bool:
    query_groups = _event_groups(query_text)
    market_groups = _event_groups(market_question)

    if query_groups and market_groups and query_groups.isdisjoint(market_groups):
        return False

    if query_groups or market_groups:
        return bool(query_groups & market_groups)

    return False


def _coerce_score(value: object) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _event_groups(text: str) -> set[str]:
    normalized = text.lower()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    groups: set[str] = set()
    for group, keywords in _EVENT_KEYWORDS.items():
        if any(keyword in tokens or keyword in normalized for keyword in keywords):
            groups.add(group)
    return groups
