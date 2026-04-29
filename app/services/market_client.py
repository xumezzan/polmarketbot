import argparse
import asyncio
import json
import logging
import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.analysis import Analysis
from app.repositories.analysis_repo import AnalysisRepository
from app.schemas.market import GammaMarket, MarketCandidate, MarketMatchResult
from app.services.retry_utils import retry_async


logger = logging.getLogger(__name__)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "before",
    "for",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "will",
}

QUERY_FILLER_TOKENS = {
    "approval",
    "approve",
    "approved",
    "action",
    "actions",
    "called",
    "every",
    "exchange",
    "forecast",
    "implications",
    "impact",
    "increase",
    "indicator",
    "operations",
    "prediction",
    "price",
    "reach",
    "resistance",
    "signal",
    "simple",
    "since",
    "status",
    "target",
    "transaction",
    "upgrades",
    "volume",
}

ASSET_DOMAIN_ALIASES = {
    "bitcoin": {"bitcoin", "btc"},
    "ethereum": {"ethereum", "eth", "ether"},
    "xrp": {"xrp", "ripple"},
    "solana": {"solana", "sol"},
    "dogecoin": {"dogecoin", "doge"},
    "cardano": {"cardano", "ada"},
}

MACRO_DOMAIN_TOKENS = {
    "fed",
    "federal",
    "reserve",
    "powell",
    "rate",
    "rates",
    "fomc",
    "inflation",
    "cpi",
    "recession",
    "tariff",
    "economy",
}

CRYPTO_DOMAIN_TOKENS = {
    "crypto",
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "ether",
    "xrp",
    "ripple",
    "solana",
    "sol",
    "doge",
    "dogecoin",
    "cardano",
    "ada",
    "stablecoin",
    "stablecoins",
    "etf",
    "etfs",
    "token",
    "tokens",
    "blockchain",
    "exchange",
    "sec",
    "cftc",
    "clarity",
}

GENERIC_DOMAIN_TOKENS = {
    "all",
    "time",
    "high",
    "low",
    "new",
    "year",
    "month",
    "quarter",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "january",
    "february",
    "march",
    "april",
    "may",
    "status",
    "operations",
    "approval",
    "target",
    "market",
    "markets",
    "regulation",
    "lawsuit",
    "legal",
    "court",
    "policy",
    "adoption",
    "payment",
    "payments",
    "banks",
    "polymarket",
}

PRICE_TARGET_MARKET_TOKENS = {
    "above",
    "below",
    "hit",
    "reach",
    "reaches",
    "reaching",
}

RELATIVE_PERFORMANCE_TOKENS = {
    "beat",
    "beats",
    "best",
    "outperform",
    "outperforms",
    "performance",
    "perform",
}

ASSET_PURCHASE_TOKENS = {
    "add",
    "adds",
    "acquire",
    "acquires",
    "buy",
    "buys",
    "bought",
    "purchase",
    "purchases",
}

ASSET_SALE_TOKENS = {
    "sell",
    "sells",
    "sold",
    "sale",
    "sales",
}


class MarketClientError(Exception):
    """Raised when market fetching or matching fails."""


class MarketClientProtocol(Protocol):
    """Contract for a market data provider."""

    async def fetch_markets(self) -> list[GammaMarket]:
        """Return a list of normalized markets."""

    async def fetch_market(self, market_id: str) -> GammaMarket | None:
        """Return one normalized market snapshot by id."""


class StubPolymarketClient:
    """Fake market provider for local end-to-end tests."""

    async def fetch_markets(self) -> list[GammaMarket]:
        stub_payloads = [
            {
                "id": "stub-btc-100k",
                "question": "Will Bitcoin reach $100,000 by December 31, 2026?",
                "slug": "bitcoin-100k-by-end-of-2026",
                "conditionId": "cond-btc-100k",
                "liquidity": "245000.5",
                "volume": "925000.2",
                "bestBid": 0.58,
                "bestAsk": 0.60,
                "lastTradePrice": 0.59,
                "active": True,
                "closed": False,
                "archived": False,
                "enableOrderBook": True,
                "outcomes": "[\"Yes\", \"No\"]",
                "outcomePrices": "[\"0.59\", \"0.41\"]",
                "clobTokenIds": "[\"btc100k-yes\", \"btc100k-no\"]",
                "events": [
                    {
                        "id": "event-btc-price",
                        "slug": "bitcoin-price-targets",
                        "title": "Bitcoin price targets",
                    }
                ],
            },
            {
                "id": "stub-btc-90k-apr",
                "question": "Will Bitcoin be above $90,000 on April 30, 2026?",
                "slug": "bitcoin-above-90k-april-30-2026",
                "conditionId": "cond-btc-90k",
                "liquidity": "178000.0",
                "volume": "410000.0",
                "bestBid": 0.63,
                "bestAsk": 0.65,
                "lastTradePrice": 0.64,
                "active": True,
                "closed": False,
                "archived": False,
                "enableOrderBook": True,
                "outcomes": "[\"Yes\", \"No\"]",
                "outcomePrices": "[\"0.64\", \"0.36\"]",
                "clobTokenIds": "[\"btc90k-yes\", \"btc90k-no\"]",
                "events": [
                    {
                        "id": "event-btc-price",
                        "slug": "bitcoin-price-targets",
                        "title": "Bitcoin price targets",
                    }
                ],
            },
            {
                "id": "stub-btc-ath",
                "question": "Will Bitcoin hit a new all-time high this quarter?",
                "slug": "bitcoin-new-all-time-high-this-quarter",
                "conditionId": "cond-btc-ath",
                "liquidity": "132000.0",
                "volume": "310000.0",
                "bestBid": 0.47,
                "bestAsk": 0.49,
                "lastTradePrice": 0.48,
                "active": True,
                "closed": False,
                "archived": False,
                "enableOrderBook": True,
                "outcomes": "[\"Yes\", \"No\"]",
                "outcomePrices": "[\"0.48\", \"0.52\"]",
                "clobTokenIds": "[\"btcath-yes\", \"btcath-no\"]",
                "events": [
                    {
                        "id": "event-btc-quarterly",
                        "slug": "bitcoin-quarterly-outlook",
                        "title": "Bitcoin quarterly outlook",
                    }
                ],
            },
            {
                "id": "stub-eth-ratio",
                "question": "Will Ethereum outperform Bitcoin this month?",
                "slug": "ethereum-outperform-bitcoin-this-month",
                "conditionId": "cond-eth-btc",
                "liquidity": "121000.0",
                "volume": "280000.0",
                "bestBid": 0.42,
                "bestAsk": 0.44,
                "lastTradePrice": 0.43,
                "active": True,
                "closed": False,
                "archived": False,
                "enableOrderBook": True,
                "outcomes": "[\"Yes\", \"No\"]",
                "outcomePrices": "[\"0.43\", \"0.57\"]",
                "clobTokenIds": "[\"ethbtc-yes\", \"ethbtc-no\"]",
                "events": [
                    {
                        "id": "event-altcoins",
                        "slug": "crypto-relative-performance",
                        "title": "Crypto relative performance",
                    }
                ],
            },
            {
                "id": "stub-fed-cut",
                "question": "Will the Fed cut rates in June 2026?",
                "slug": "fed-cut-rates-june-2026",
                "conditionId": "cond-fed-cut",
                "liquidity": "201000.0",
                "volume": "550000.0",
                "bestBid": 0.32,
                "bestAsk": 0.34,
                "lastTradePrice": 0.33,
                "active": True,
                "closed": False,
                "archived": False,
                "enableOrderBook": True,
                "outcomes": "[\"Yes\", \"No\"]",
                "outcomePrices": "[\"0.33\", \"0.67\"]",
                "clobTokenIds": "[\"fedcut-yes\", \"fedcut-no\"]",
                "events": [
                    {
                        "id": "event-fed",
                        "slug": "fed-rates",
                        "title": "Federal Reserve rates",
                    }
                ],
            },
        ]

        return [self._normalize_market(payload) for payload in stub_payloads]

    async def fetch_market(self, market_id: str) -> GammaMarket | None:
        for market in await self.fetch_markets():
            if market.id == market_id:
                return market
        return None

    def _normalize_market(self, payload: dict[str, object]) -> GammaMarket:
        market = GammaMarket.model_validate(payload)
        market.raw_payload = payload
        return market


class GammaPolymarketClient:
    """Adapter over the official Polymarket Gamma API `/markets` endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch_markets(self) -> list[GammaMarket]:
        try:
            markets = await self._fetch_markets_live()
        except MarketClientError:
            cached_markets = self._load_cached_markets()
            if cached_markets:
                log_event(
                    logger,
                    "gamma_market_fetch_using_cache",
                    provider="polymarket_gamma",
                    fetched_count=len(cached_markets),
                    cache_path=self.settings.gamma_market_cache_path,
                )
                return cached_markets
            raise

        self._save_cached_markets(markets)
        return markets

    async def _fetch_markets_live(self) -> list[GammaMarket]:
        markets: list[GammaMarket] = []
        base_url = f"{self.settings.gamma_api_base_url.rstrip('/')}/markets"

        async with httpx.AsyncClient(timeout=self.settings.gamma_request_timeout_seconds) as client:
            for page_index in range(self.settings.gamma_markets_max_pages):
                offset = page_index * self.settings.gamma_markets_page_size
                params = {
                    "limit": self.settings.gamma_markets_page_size,
                    "offset": offset,
                }

                # These parameters are confirmed by Polymarket docs and live API.
                if self.settings.gamma_fetch_active_only:
                    params["active"] = "true"
                params["closed"] = "true" if self.settings.gamma_fetch_closed else "false"

                async def _request_once() -> httpx.Response:
                    response = await client.get(base_url, params=params)
                    response.raise_for_status()
                    return response

                try:
                    response = await retry_async(
                        _request_once,
                        logger=logger,
                        provider="polymarket_gamma",
                        operation_name="fetch_markets_page",
                        max_attempts=self.settings.gamma_retry_max_attempts,
                        base_delay_seconds=self.settings.gamma_retry_base_delay_seconds,
                        is_retryable=_is_retryable_gamma_exception,
                        context={"offset": offset},
                    )
                except httpx.HTTPError as exc:
                    log_event(
                        logger,
                        "gamma_market_fetch_failed",
                        provider="polymarket_gamma",
                        error=str(exc),
                        offset=offset,
                    )
                    raise MarketClientError(f"Gamma market fetch failed: {exc}") from exc

                payload = response.json()
                if not isinstance(payload, list):
                    raise MarketClientError("Gamma /markets returned a non-list payload.")

                if not payload:
                    break

                normalized = [self._normalize_market(item) for item in payload]
                markets.extend(normalized)

        log_event(
            logger,
            "gamma_market_fetch_completed",
            provider="polymarket_gamma",
            fetched_count=len(markets),
            page_size=self.settings.gamma_markets_page_size,
            max_pages=self.settings.gamma_markets_max_pages,
        )
        return markets

    def _save_cached_markets(self, markets: list[GammaMarket]) -> None:
        if not self.settings.gamma_market_cache_enabled or not markets:
            return

        cache_path = Path(self.settings.gamma_market_cache_path)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "generated_at": datetime.now(UTC).isoformat(),
                "markets": [
                    market.raw_payload
                    if market.raw_payload
                    else market.model_dump(mode="json", by_alias=True)
                    for market in markets
                ],
            }
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            log_event(
                logger,
                "gamma_market_cache_write_failed",
                provider="polymarket_gamma",
                cache_path=str(cache_path),
                error=str(exc),
            )

    def _load_cached_markets(self) -> list[GammaMarket]:
        if not self.settings.gamma_market_cache_enabled:
            return []

        cache_path = Path(self.settings.gamma_market_cache_path)
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log_event(
                logger,
                "gamma_market_cache_read_failed",
                provider="polymarket_gamma",
                cache_path=str(cache_path),
                error=str(exc),
            )
            return []

        generated_at = _parse_cache_datetime(payload.get("generated_at"))
        if generated_at is None:
            return []

        age_minutes = (datetime.now(UTC) - generated_at).total_seconds() / 60
        if age_minutes > self.settings.gamma_market_cache_max_age_minutes:
            log_event(
                logger,
                "gamma_market_cache_stale",
                provider="polymarket_gamma",
                cache_path=str(cache_path),
                age_minutes=round(age_minutes, 2),
                max_age_minutes=self.settings.gamma_market_cache_max_age_minutes,
            )
            return []

        raw_markets = payload.get("markets")
        if not isinstance(raw_markets, list):
            return []

        markets: list[GammaMarket] = []
        for item in raw_markets:
            if not isinstance(item, dict):
                continue
            market = self._normalize_market(item)
            markets.append(market)
        return markets

    async def fetch_market(self, market_id: str) -> GammaMarket | None:
        base_url = f"{self.settings.gamma_api_base_url.rstrip('/')}/markets/{market_id}"

        async with httpx.AsyncClient(timeout=self.settings.gamma_request_timeout_seconds) as client:
            async def _request_once() -> httpx.Response:
                response = await client.get(base_url)
                response.raise_for_status()
                return response

            try:
                response = await retry_async(
                    _request_once,
                    logger=logger,
                    provider="polymarket_gamma",
                    operation_name="fetch_market_by_id",
                    max_attempts=self.settings.gamma_retry_max_attempts,
                    base_delay_seconds=self.settings.gamma_retry_base_delay_seconds,
                    is_retryable=_is_retryable_gamma_exception,
                    context={"market_id": market_id},
                )
            except httpx.HTTPError as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                    return None
                log_event(
                    logger,
                    "gamma_single_market_fetch_failed",
                    provider="polymarket_gamma",
                    market_id=market_id,
                    error=str(exc),
                )
                raise MarketClientError(f"Gamma market fetch failed: {exc}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise MarketClientError("Gamma /markets/{id} returned a non-object payload.")

        market = self._normalize_market(payload)
        log_event(
            logger,
            "gamma_single_market_fetch_completed",
            provider="polymarket_gamma",
            market_id=market_id,
            closed=market.closed,
        )
        return market

    def _normalize_market(self, payload: dict[str, object]) -> GammaMarket:
        market = GammaMarket.model_validate(payload)
        market.raw_payload = payload
        return market


def _is_retryable_gamma_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code <= 599

    return isinstance(exc, httpx.TransportError)


def _parse_cache_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class MarketRankerProtocol(Protocol):
    """Contract for ranking candidate markets."""

    def rank(
        self,
        *,
        analysis: Analysis,
        markets: list[GammaMarket],
    ) -> list[MarketCandidate]:
        """Return ranked candidates sorted by best score first."""


class KeywordMarketRanker:
    """Simple explainable keyword ranker. Easy to swap later for vector search."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def rank(
        self,
        *,
        analysis: Analysis,
        markets: list[GammaMarket],
    ) -> list[MarketCandidate]:
        query_text = normalize_market_query(analysis.market_query)
        query_tokens = _tokenize(query_text)
        query_phrase = query_text.strip().lower()

        ranked: list[MarketCandidate] = []
        for market in markets:
            if not is_market_domain_compatible(query_text=query_phrase, market=market):
                continue

            contract_compatibility = market_contract_compatibility(
                query_text=query_phrase,
                market=market,
            )
            if contract_compatibility <= 0:
                continue

            candidate = self._score_market(
                analysis=analysis,
                market=market,
                query_tokens=query_tokens,
                query_phrase=query_phrase,
                contract_compatibility=contract_compatibility,
            )
            if candidate is not None:
                ranked.append(candidate)

        ranked.sort(key=lambda item: item.match_score, reverse=True)
        return ranked

    def _score_market(
        self,
        *,
        analysis: Analysis,
        market: GammaMarket,
        query_tokens: set[str],
        query_phrase: str,
        contract_compatibility: float,
    ) -> MarketCandidate | None:
        question_tokens = _tokenize(market.question)
        slug_tokens = _tokenize(market.slug or "")
        event_tokens = _tokenize(f"{market.event_title or ''} {market.event_slug or ''}")

        exact_match = (
            1.0
            if len(query_tokens) >= 2
            and query_phrase
            and query_phrase in market.question.lower()
            else 0.0
        )
        question_overlap = _token_overlap(query_tokens, question_tokens)
        slug_overlap = _token_overlap(query_tokens, slug_tokens)
        event_overlap = _token_overlap(query_tokens, event_tokens)
        liquidity_bonus = _scaled_liquidity(market.liquidity)

        score_breakdown = {
            "exact_match": exact_match * self.settings.market_match_exact_weight,
            "question_overlap": question_overlap * self.settings.market_match_question_weight,
            "slug_overlap": slug_overlap * self.settings.market_match_slug_weight,
            "event_overlap": event_overlap * self.settings.market_match_event_weight,
            "liquidity_bonus": liquidity_bonus * self.settings.market_match_liquidity_weight,
        }
        raw_score = sum(score_breakdown.values())
        total_score = round(raw_score * contract_compatibility, 6)

        if total_score < self.settings.market_match_min_score:
            return None

        reasons: list[str] = []
        if exact_match:
            reasons.append("market question contains the full market_query phrase")
        if question_overlap:
            reasons.append(f"question token overlap={question_overlap:.2f}")
        if slug_overlap:
            reasons.append(f"slug token overlap={slug_overlap:.2f}")
        if event_overlap:
            reasons.append(f"event token overlap={event_overlap:.2f}")
        if liquidity_bonus:
            reasons.append(f"liquidity bonus={liquidity_bonus:.2f}")
        if contract_compatibility < 1:
            reasons.append(f"contract type compatibility={contract_compatibility:.2f}")

        return MarketCandidate(
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            condition_id=market.condition_id,
            event_id=market.event_id,
            event_slug=market.event_slug,
            event_title=market.event_title,
            yes_price=market.yes_price,
            no_price=market.no_price,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            best_bid=market.best_bid,
            best_ask=market.best_ask,
            last_trade_price=market.last_trade_price,
            liquidity=market.liquidity,
            volume=market.volume,
            fees_enabled=market.fees_enabled,
            effective_taker_fee_rate=market.effective_taker_fee_rate,
            match_score=total_score,
            match_reasons=reasons,
            score_breakdown=score_breakdown,
            correlation_key=market.event_slug or market.slug or market.condition_id or market.id,
            raw_market=market.raw_payload,
        )


class CorrelationFilter:
    """Drop overly similar candidate markets to keep top-N diverse."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def apply(self, candidates: list[MarketCandidate]) -> list[MarketCandidate]:
        if not self.settings.market_correlation_filter_enabled:
            return candidates

        accepted: list[MarketCandidate] = []
        for candidate in candidates:
            if self._is_correlated(candidate, accepted):
                continue
            accepted.append(candidate)
        return accepted

    def _is_correlated(
        self,
        candidate: MarketCandidate,
        accepted: list[MarketCandidate],
    ) -> bool:
        candidate_tokens = _tokenize(candidate.question)

        for existing in accepted:
            if (
                self.settings.market_correlation_block_same_event
                and candidate.event_slug
                and existing.event_slug
                and candidate.event_slug == existing.event_slug
            ):
                return True

            similarity = _jaccard_similarity(candidate_tokens, _tokenize(existing.question))
            if similarity >= self.settings.market_correlation_jaccard_threshold:
                return True

        return False


class MarketMatchingService:
    """Fetch markets and return top-N candidates for one analysis."""

    def __init__(
        self,
        *,
        client: MarketClientProtocol,
        ranker: MarketRankerProtocol,
        correlation_filter: CorrelationFilter,
        analysis_repository: AnalysisRepository,
        settings: Settings,
    ) -> None:
        self.client = client
        self.ranker = ranker
        self.correlation_filter = correlation_filter
        self.analysis_repository = analysis_repository
        self.settings = settings

    async def match_analysis(self, analysis_id: int | None = None) -> MarketMatchResult:
        analysis = await self._load_analysis(analysis_id)
        if analysis is None:
            raise MarketClientError("No analysis found to match against Polymarket.")

        markets = await self.client.fetch_markets()
        normalized_query = normalize_market_query(analysis.market_query)
        domain_anchor_tokens = extract_market_domain_anchor_tokens(normalized_query)
        filtered_markets = filter_markets_by_query_domain(
            markets=markets,
            query_text=normalized_query,
        )
        ranked_candidates = self.ranker.rank(analysis=analysis, markets=filtered_markets)
        filtered_candidates = self.correlation_filter.apply(ranked_candidates)
        top_candidates = filtered_candidates[: self.settings.market_top_n]

        result = MarketMatchResult(
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            market_query=normalized_query,
            fetch_mode=self.settings.market_fetch_mode.lower(),
            match_strategy=self.settings.market_match_strategy.lower(),
            fetched_count=len(markets),
            candidate_count=len(top_candidates),
            candidates=top_candidates,
        )

        await self.analysis_repository.save_market_matching_snapshot(
            analysis_id=analysis.id,
            snapshot={
                "generated_at": datetime.now(UTC).isoformat(),
                "fetch_mode": result.fetch_mode,
                "match_strategy": result.match_strategy,
                "raw_market_query": analysis.market_query,
                "normalized_market_query": result.market_query,
                "fetched_count": result.fetched_count,
                "domain_filter_applied": bool(domain_anchor_tokens),
                "domain_anchor_tokens": sorted(domain_anchor_tokens),
                "domain_filtered_count": len(filtered_markets),
                "candidate_count": result.candidate_count,
                "candidates": [candidate.model_dump(mode="json") for candidate in top_candidates],
            },
        )

        log_event(
            logger,
            "market_matching_completed",
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            fetched_count=result.fetched_count,
            domain_filtered_count=len(filtered_markets),
            candidate_count=result.candidate_count,
            market_query=result.market_query,
        )
        return result

    async def _load_analysis(self, analysis_id: int | None) -> Analysis | None:
        if analysis_id is not None:
            return await self.analysis_repository.get_by_id(analysis_id)
        return await self.analysis_repository.get_latest()


def build_market_client(settings: Settings) -> MarketClientProtocol:
    """Return either the fake provider or the real Gamma adapter."""
    mode = settings.market_fetch_mode.lower()

    if mode == "stub":
        return StubPolymarketClient()

    if mode == "gamma":
        return GammaPolymarketClient(settings)

    raise ValueError("Unsupported MARKET_FETCH_MODE. Expected 'stub' or 'gamma'.")


def build_market_ranker(settings: Settings) -> MarketRankerProtocol:
    """Return the selected matching strategy."""
    strategy = settings.market_match_strategy.lower()

    if strategy == "keyword":
        return KeywordMarketRanker(settings)

    raise ValueError(
        "Unsupported MARKET_MATCH_STRATEGY. Expected 'keyword'. "
        "Vector search / embeddings can be added later behind this adapter."
    )


async def run_market_matching(
    session: AsyncSession,
    settings: Settings,
    *,
    analysis_id: int | None = None,
) -> MarketMatchResult:
    """Convenience entrypoint for one market-matching run."""
    service = MarketMatchingService(
        client=build_market_client(settings),
        ranker=build_market_ranker(settings),
        correlation_filter=CorrelationFilter(settings),
        analysis_repository=AnalysisRepository(session),
        settings=settings,
    )
    return await service.match_analysis(analysis_id=analysis_id)


def _tokenize(value: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    return {token for token in tokens if token not in STOPWORDS and len(token) > 1}


def filter_markets_by_query_domain(
    *,
    markets: list[GammaMarket],
    query_text: str,
) -> list[GammaMarket]:
    """Reduce the candidate universe to markets in the same domain as the query."""
    lowered = (query_text or "").strip().lower()
    query_tokens = set(_normalized_query_tokens(lowered))
    if _is_generic_market_legal_query(query_tokens):
        return []

    anchor_tokens = extract_market_domain_anchor_tokens(query_text)
    if not anchor_tokens:
        fallback_tokens = [
            token
            for token in query_tokens
            if token not in GENERIC_DOMAIN_TOKENS
        ]
        if lowered and lowered != "general news" and not fallback_tokens:
            return []
        return markets

    filtered = [
        market
        for market in markets
        if _market_domain_tokens(market) & anchor_tokens
        and is_market_domain_compatible(query_text=lowered, market=market)
    ]
    return filtered


def is_market_domain_compatible(*, query_text: str, market: GammaMarket) -> bool:
    """Return False for high-confidence false friends between news queries and markets."""
    query = (query_text or "").lower()
    query_tokens = set(_normalized_query_tokens(query))
    market_text = _market_text(market)
    market_tokens = _tokenize(market_text)

    if not query_tokens:
        return True

    if {"data", "breach"} <= query_tokens or query_tokens & {"breach", "hack", "hacked"}:
        if _contains_phrase(market_text, "data center") and not _contains_phrase(query, "data center"):
            return False
        if market_tokens & {
            "baseball",
            "basketball",
            "boston",
            "cdl",
            "championship",
            "esports",
            "football",
            "game",
            "hockey",
            "league",
            "season",
            "soccer",
            "team",
        }:
            return False
        return bool(
            market_tokens
            & {
                "breach",
                "breaches",
                "cyber",
                "cyberattack",
                "cybersecurity",
                "data",
                "hack",
                "hacked",
                "hacker",
                "leak",
                "leaked",
                "privacy",
                "polymarket",
            }
        )

    if query_tokens & {"atm", "atms", "kiosk", "kiosks"}:
        return bool(market_tokens & {"atm", "atms", "kiosk", "kiosks"})

    if "clarity" in query_tokens:
        return bool(
            "clarity" in market_tokens
            or (
                "act" in market_tokens
                and market_tokens & {"crypto", "cryptocurrency", "senate", "stablecoin"}
            )
        )

    if "ipo" in query_tokens:
        ipo_subjects = query_tokens & {
            "openai",
            "spacex",
            "stripe",
            "anthropic",
            "xai",
            "databricks",
        }
        if ipo_subjects and not ipo_subjects <= market_tokens:
            return False
        return "ipo" in market_tokens

    geography_tokens = query_tokens & {
        "argentina",
        "brazil",
        "canada",
        "canadian",
        "china",
        "europe",
        "eu",
        "france",
        "germany",
        "india",
        "iran",
        "israel",
        "japan",
        "mexico",
        "russia",
        "taiwan",
        "ukraine",
        "us",
        "usa",
    }
    if geography_tokens and query_tokens & {
        "ban",
        "bans",
        "bill",
        "court",
        "law",
        "lawsuit",
        "policy",
        "regulation",
        "sanction",
        "sanctions",
        "tariff",
        "tariffs",
    }:
        return bool(geography_tokens & market_tokens)

    return True


def market_contract_compatibility(*, query_text: str, market: GammaMarket) -> float:
    """Return 0 when query and market describe incompatible contract types."""
    query_type = infer_market_contract_type(query_text)
    market_type = infer_market_contract_type(
        " ".join(
            part
            for part in (
                market.question,
                market.slug or "",
                market.event_title or "",
                market.event_slug or "",
            )
            if part
        )
    )

    if query_type == "generic" or market_type == "generic":
        return 1.0
    if query_type == market_type:
        return 1.0
    return 0.0


def infer_market_contract_type(value: str) -> str:
    """Classify the kind of binary market implied by text."""
    lowered = (value or "").lower()
    tokens = _tokenize(lowered)

    if "all-time high" in lowered or "all time high" in lowered:
        return "all_time_high"
    if tokens & RELATIVE_PERFORMANCE_TOKENS:
        return "relative_performance"
    if _extract_price_target(lowered) is not None and (
        tokens & PRICE_TARGET_MARKET_TOKENS or _detect_primary_asset(lowered) is not None
    ):
        return "price_target"
    if (
        _contains_phrase(lowered, "rate cut")
        or _contains_phrase(lowered, "rate cuts")
        or tokens & {"fomc", "fed"}
        and tokens & {"cut", "cuts", "rate", "rates"}
    ):
        return "rate_cut"
    if tokens & ASSET_SALE_TOKENS:
        return "asset_sale"
    if tokens & ASSET_PURCHASE_TOKENS:
        return "asset_purchase"

    return "generic"


def extract_market_domain_anchor_tokens(query_text: str) -> set[str]:
    """Return high-signal domain tokens used to prefilter Polymarket markets."""
    lowered = (query_text or "").strip().lower()
    if not lowered or lowered == "general news":
        return set()
    query_tokens = set(_normalized_query_tokens(lowered))

    anchor_tokens: set[str] = set()
    asset = _detect_primary_asset(lowered)
    if asset is not None:
        anchor_tokens |= ASSET_DOMAIN_ALIASES.get(asset, {asset})

    if _contains_phrase(lowered, "federal reserve") or query_tokens & {"fed", "powell", "fomc"}:
        anchor_tokens |= MACRO_DOMAIN_TOKENS
    elif _contains_phrase(lowered, "rate cut") or _contains_phrase(lowered, "rate cuts"):
        anchor_tokens |= {"fed", "federal", "reserve", "rate", "rates", "fomc"}

    if query_tokens & {"stablecoin", "stablecoins"}:
        anchor_tokens |= {"stablecoin", "stablecoins"}
    elif query_tokens & {"etf", "etfs"}:
        anchor_tokens |= {"etf", "etfs", "bitcoin", "btc", "ethereum", "eth", "ether"}
    elif query_tokens & {"clarity"}:
        anchor_tokens |= {"clarity", "crypto", "sec", "cftc"}
    elif query_tokens & {"cftc", "sec"}:
        anchor_tokens |= query_tokens & {"cftc", "sec"}
    elif query_tokens & {"crypto", "blockchain", "token", "tokens", "exchange"}:
        anchor_tokens |= CRYPTO_DOMAIN_TOKENS

    if anchor_tokens:
        return anchor_tokens

    fallback_tokens = [
        token
        for token in _normalized_query_tokens(lowered)
        if token not in GENERIC_DOMAIN_TOKENS
    ]
    return set(fallback_tokens[:2])


def _contains_phrase(value: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", value) is not None


def _is_generic_market_legal_query(query_tokens: set[str]) -> bool:
    return bool(
        query_tokens & {"market", "markets"}
        and query_tokens & {"lawsuit", "regulation", "legal", "court", "policy"}
        and not query_tokens & {"cftc", "sec", "clarity", "crypto", "bitcoin", "ethereum", "xrp"}
    )


def normalize_market_query(value: str) -> str:
    """Reduce free-form LLM output to a tighter market-search query."""
    raw = (value or "").strip()
    if not raw:
        return raw

    lowered = _normalize_query_typos(raw.lower())
    asset = _detect_primary_asset(lowered)
    price_target = _extract_price_target(lowered)

    if asset and ("all-time high" in lowered or "all time high" in lowered):
        return f"{asset} all time high"

    if asset and price_target is not None:
        return f"{asset} {price_target}"

    if asset and "government" in lowered and ("seizure" in lowered or "moves" in lowered):
        return f"{asset} government transfer"

    if asset and "tax" in lowered:
        return f"{asset} tax"

    if asset and "quantum" in lowered:
        return f"{asset} quantum"

    if asset == "bitcoin" and (
        "microstrategy" in lowered
        or "mstr" in lowered
        or (
            re.search(r"\bstrategy\b", lowered) is not None
            and re.search(r"\b(saylor|hold|holds|holding|holdings|buy|buys|bought|purchase|purchases)\b", lowered)
            is not None
        )
    ):
        if re.search(r"\b(sell|sells|sold|sale|sales)\b", lowered) is not None:
            return "microstrategy bitcoin sell"
        if (
            re.search(r"\b(add|adds|acquire|acquires|buy|buys|bought|purchase|purchases)\b", lowered)
            is not None
        ):
            return "microstrategy bitcoin buy"
        return "microstrategy bitcoin holdings"

    if asset and ("transaction volume" in lowered or "volume" in lowered):
        return f"{asset} volume"

    if "clarity act" in lowered:
        return "clarity act crypto"

    if "grinex" in lowered:
        return "grinex exchange"

    tokens = _normalized_query_tokens(lowered)
    if asset and asset not in tokens:
        tokens.insert(0, asset)

    if not tokens:
        return raw

    return " ".join(tokens[:4])


def _detect_primary_asset(value: str) -> str | None:
    if any(token in value for token in ("bitcoin", " btc", "btc ", "btc-", "btc/")):
        return "bitcoin"
    if any(token in value for token in ("ethereum", " ether", "eth ", "eth-", "eth/")):
        return "ethereum"
    if "xrp" in value:
        return "xrp"
    return None


def _extract_price_target(value: str) -> str | None:
    million_match = re.search(r"(\d+(?:\.\d+)?)\s*m\b", value)
    if million_match:
        number = million_match.group(1).rstrip("0").rstrip(".")
        return f"{number}m"

    thousand_match = re.search(r"\$?\s*(\d{2,3})(?:[,\s]?(\d{3}))\b", value)
    if thousand_match and thousand_match.group(2):
        return f"{thousand_match.group(1)}k"

    compact_thousand_match = re.search(r"(\d{2,3})\s*k\b", value)
    if compact_thousand_match:
        return f"{compact_thousand_match.group(1)}k"

    return None


def _normalized_query_tokens(value: str) -> list[str]:
    alias_map = {
        "btc": "bitcoin",
        "eth": "ethereum",
    }
    raw_tokens = re.findall(r"[a-z0-9]+", value.lower())
    tokens: list[str] = []

    for token in raw_tokens:
        mapped = alias_map.get(token, token)
        if mapped in STOPWORDS or mapped in QUERY_FILLER_TOKENS:
            continue
        if re.fullmatch(r"20\d{2}", mapped):
            continue
        if len(mapped) <= 1:
            continue
        if mapped not in tokens:
            tokens.append(mapped)

    return tokens


def _normalize_query_typos(value: str) -> str:
    replacements = {
        "claritiy": "clarity",
        "clarty": "clarity",
        "clariy": "clarity",
    }
    normalized = value
    for typo, replacement in replacements.items():
        normalized = re.sub(rf"\b{typo}\b", replacement, normalized)
    return normalized


def _market_domain_tokens(market: GammaMarket) -> set[str]:
    return _tokenize(_market_text(market))


def _market_text(market: GammaMarket) -> str:
    return " ".join(
        part
        for part in (
            market.question,
            market.slug or "",
            market.event_title or "",
            market.event_slug or "",
        )
        if part
    ).lower()


def _token_overlap(query_tokens: set[str], document_tokens: set[str]) -> float:
    if not query_tokens or not document_tokens:
        return 0.0
    return len(query_tokens & document_tokens) / len(query_tokens)


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _scaled_liquidity(liquidity: float | None) -> float:
    if not liquidity or liquidity <= 0:
        return 0.0
    return min(math.log10(liquidity + 1) / 6, 1.0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match one analysis to top-N Polymarket markets.")
    parser.add_argument(
        "--analysis-id",
        type=int,
        default=None,
        help="Match a specific analyses.id. Defaults to the latest analysis.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        result = await run_market_matching(session, settings, analysis_id=args.analysis_id)
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
