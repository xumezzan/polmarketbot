import argparse
import asyncio
import logging
import re
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.enums import SignalStatus, VerdictDirection
from app.models.signal import Signal
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.schemas.market import MarketCandidate
from app.schemas.risk import RiskCheckResult, RiskDecision


logger = logging.getLogger(__name__)

OVERLAP_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "by",
    "for",
    "from",
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

ANCHOR_GENERIC_TOKENS = {
    "price",
    "prediction",
    "outcome",
    "election",
    "presidential",
    "governor",
    "resign",
    "ceasefire",
    "performance",
    "impact",
    "news",
    "update",
    "market",
    "markets",
    "purchase",
    "adoption",
    "activity",
    "sales",
    "transfer",
}


class RiskEngineError(Exception):
    """Raised when risk evaluation cannot be completed."""


class RiskEngine:
    """Apply deterministic trade approval checks to one signal."""

    def __init__(
        self,
        *,
        settings: Settings,
        signal_repository: SignalRepository,
        analysis_repository: AnalysisRepository,
        trade_repository: TradeRepository,
    ) -> None:
        self.settings = settings
        self.signal_repository = signal_repository
        self.analysis_repository = analysis_repository
        self.trade_repository = trade_repository

    async def evaluate(self, signal_id: int | None = None) -> RiskDecision:
        signal = await self._load_signal(signal_id)
        if signal is None:
            raise RiskEngineError("No signal found for risk evaluation.")

        analysis = signal.analysis
        if analysis is None or analysis.news_item is None:
            raise RiskEngineError("Signal is missing linked analysis/news context.")

        candidate = self._load_candidate(signal)
        now = datetime.now(UTC)
        news_published_at = analysis.news_item.published_at
        news_age_minutes = self._news_age_minutes(news_published_at, now)
        liquidity = float(candidate.liquidity or 0.0)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        daily_exposure_used = await self.trade_repository.get_daily_exposure_used_usd(
            day_start=day_start
        )
        has_existing_position = await self.trade_repository.has_open_position_for_market(
            market_id=signal.market_id
        )
        entity_tokens = _extract_query_entity_tokens(
            query_text=analysis.market_query or "",
            max_tokens=self.settings.risk_anchor_entity_max_tokens,
        )
        entity_key = _build_entity_key(entity_tokens)
        entity_open_positions_count, entity_open_exposure_used_usd = (
            await self._entity_exposure_context(current_entity_tokens=entity_tokens)
        )
        analysis_trade_count = await self.trade_repository.count_trades_for_analysis(
            analysis_id=signal.analysis_id
        )
        top_match_score, second_match_score, top_candidate_score_delta = (
            self._load_market_match_score_context(signal)
        )
        overlap_count, max_overlap_token_length = _query_market_overlap_stats(
            query_text=analysis.market_query or "",
            market_question=candidate.question,
        )
        anchor_overlap_count, query_anchor_tokens = _query_market_anchor_stats(
            settings=self.settings,
            query_text=analysis.market_query or "",
            market_question=candidate.question,
        )
        bid_ask_spread = _resolve_bid_ask_spread(
            best_bid=candidate.best_bid,
            best_ask=candidate.best_ask,
        )
        yes_entry_slippage = _resolve_yes_entry_slippage(
            direction=analysis.direction.value,
            reference_market_price=float(signal.market_price),
            best_ask=candidate.best_ask,
        )

        risk_result = evaluate_risk_case(
            settings=self.settings,
            signal_status=signal.signal_status.value,
            confidence=float(analysis.confidence),
            relevance=float(analysis.relevance),
            news_age_minutes=news_age_minutes,
            liquidity=liquidity,
            edge=float(signal.edge),
            match_score=float(candidate.match_score),
            query_text=analysis.market_query or "",
            market_question=candidate.question,
            existing_open_position=has_existing_position,
            entity_key=entity_key,
            entity_open_positions_count=entity_open_positions_count,
            entity_open_exposure_used_usd=entity_open_exposure_used_usd,
            bid_ask_spread=bid_ask_spread,
            yes_entry_slippage=yes_entry_slippage,
            analysis_trade_count=analysis_trade_count,
            daily_exposure_used_usd=daily_exposure_used,
            top_candidate_score_delta=top_candidate_score_delta,
        )

        approved_size_usd = 0.0
        used_extended_news_age_window = should_use_extended_news_age_window(
            settings=self.settings,
            news_age_minutes=news_age_minutes,
        )
        effective_news_age_limit_minutes = resolve_news_age_limit_minutes(self.settings)
        approved_size_multiplier = resolve_news_age_size_multiplier(
            settings=self.settings,
            news_age_minutes=news_age_minutes,
        )
        if risk_result.allow:
            approved_size_usd = self._approved_size_usd(
                liquidity=liquidity,
                daily_exposure_used_usd=daily_exposure_used,
                entity_key=entity_key,
                entity_open_exposure_used_usd=entity_open_exposure_used_usd,
                size_multiplier=approved_size_multiplier,
            )

        decision = RiskDecision(
            signal_id=signal.id,
            analysis_id=signal.analysis_id,
            news_item_id=analysis.news_item_id,
            market_id=signal.market_id,
            allow=risk_result.allow,
            blockers=risk_result.blockers,
            approved_size_usd=round(approved_size_usd, 2),
            signal_status=signal.signal_status.value,
            edge=float(signal.edge),
            market_price=float(signal.market_price),
            fair_probability=float(signal.fair_probability),
            checks={
                "confidence": float(analysis.confidence),
                "relevance": float(analysis.relevance),
                "news_age_minutes": news_age_minutes,
                "base_news_age_limit_minutes": self.settings.risk_max_news_age_minutes,
                "effective_news_age_limit_minutes": effective_news_age_limit_minutes,
                "used_extended_news_age_window": used_extended_news_age_window,
                "news_age_size_multiplier": approved_size_multiplier,
                "liquidity": liquidity,
                "match_score": float(candidate.match_score),
                "min_match_score": self.settings.risk_min_match_score,
                "query_market_overlap_count": overlap_count,
                "min_query_market_overlap_count": self.settings.risk_min_query_market_token_overlap,
                "max_overlap_token_length": max_overlap_token_length,
                "min_overlap_token_length": self.settings.risk_min_query_market_overlap_token_length,
                "query_anchor_tokens": ",".join(sorted(query_anchor_tokens)) or None,
                "anchor_overlap_count": anchor_overlap_count,
                "min_anchor_overlap_count": self.settings.risk_min_anchor_entity_overlap,
                "entity_key": entity_key,
                "entity_open_positions_count": entity_open_positions_count,
                "max_open_positions_per_entity": self.settings.risk_max_open_positions_per_entity,
                "entity_open_exposure_used_usd": round(entity_open_exposure_used_usd, 2),
                "max_entity_open_exposure_usd": self.settings.risk_max_entity_open_exposure_usd,
                "best_bid": candidate.best_bid,
                "best_ask": candidate.best_ask,
                "bid_ask_spread": bid_ask_spread,
                "max_bid_ask_spread": self.settings.risk_max_bid_ask_spread,
                "yes_entry_slippage": yes_entry_slippage,
                "max_yes_entry_slippage": self.settings.risk_max_yes_entry_slippage,
                "daily_exposure_used_usd": round(daily_exposure_used, 2),
                "existing_open_position": has_existing_position,
                "analysis_trade_count": analysis_trade_count,
                "max_trades_per_analysis": self.settings.risk_max_trades_per_analysis,
                "top_match_score": top_match_score,
                "second_match_score": second_match_score,
                "top_candidate_score_delta": top_candidate_score_delta,
                "min_top_candidate_score_delta": self.settings.risk_min_top_candidate_score_delta,
                "priced_in_threshold": self.settings.risk_priced_in_edge_threshold,
            },
            evaluated_at=now.isoformat(),
        )

        await self.analysis_repository.save_risk_engine_decision(
            analysis_id=signal.analysis_id,
            decision=decision.model_dump(mode="json"),
        )

        log_event(
            logger,
            "risk_engine_completed",
            signal_id=signal.id,
            analysis_id=signal.analysis_id,
            news_item_id=analysis.news_item_id,
            market_id=signal.market_id,
            entity_key=entity_key,
            allow=decision.allow,
            blockers=decision.blockers,
            approved_size_usd=decision.approved_size_usd,
        )
        return decision

    async def _load_signal(self, signal_id: int | None) -> Signal | None:
        if signal_id is not None:
            return await self.signal_repository.get_by_id(signal_id)
        return await self.signal_repository.get_latest()

    def _load_candidate(self, signal: Signal) -> MarketCandidate:
        analysis = signal.analysis
        raw_response = analysis.raw_response or {}
        snapshots = raw_response.get("snapshots") or {}
        signal_snapshot = snapshots.get("signal_engine") or {}
        signal_items = signal_snapshot.get("signals") or []

        for item in signal_items:
            if item.get("market_id") == signal.market_id:
                return MarketCandidate.model_validate(item["candidate"])

        market_snapshot = snapshots.get("market_matching") or {}
        market_candidates = market_snapshot.get("candidates") or []
        for item in market_candidates:
            if item.get("market_id") == signal.market_id:
                return MarketCandidate.model_validate(item)

        raise RiskEngineError(
            f"Market candidate snapshot not found for signal_id={signal.id}, market_id={signal.market_id}."
        )

    def _news_age_minutes(
        self,
        news_published_at: datetime | None,
        now: datetime,
    ) -> int:
        if news_published_at is None:
            return self.settings.risk_max_news_age_minutes + 1

        published = news_published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)

        age_seconds = max((now - published).total_seconds(), 0.0)
        return int(age_seconds // 60)

    def _load_market_match_score_context(
        self,
        signal: Signal,
    ) -> tuple[float | None, float | None, float | None]:
        analysis = signal.analysis
        raw_response = analysis.raw_response or {}
        snapshots = raw_response.get("snapshots") or {}
        market_snapshot = snapshots.get("market_matching") or {}
        raw_candidates = market_snapshot.get("candidates") or []

        scored_candidates = sorted(
            (
                float(item.get("match_score"))
                for item in raw_candidates
                if item.get("match_score") is not None
            ),
            reverse=True,
        )

        if not scored_candidates:
            return None, None, None

        top_match_score = scored_candidates[0]
        if len(scored_candidates) < 2:
            return top_match_score, None, None

        second_match_score = scored_candidates[1]
        return (
            top_match_score,
            second_match_score,
            round(top_match_score - second_match_score, 6),
        )

    def _approved_size_usd(
        self,
        *,
        liquidity: float,
        daily_exposure_used_usd: float,
        entity_key: str | None,
        entity_open_exposure_used_usd: float,
        size_multiplier: float,
    ) -> float:
        daily_remaining = max(
            self.settings.risk_max_daily_exposure_usd - daily_exposure_used_usd,
            0.0,
        )
        entity_remaining = (
            max(
                self.settings.risk_max_entity_open_exposure_usd - entity_open_exposure_used_usd,
                0.0,
            )
            if entity_key is not None
            else self.settings.risk_max_trade_size_usd
        )
        liquidity_cap = liquidity * self.settings.risk_max_liquidity_share
        capped_size = min(
            self.settings.risk_max_trade_size_usd,
            daily_remaining,
            entity_remaining,
            liquidity_cap,
        )
        return capped_size * size_multiplier

    async def _entity_exposure_context(
        self,
        *,
        current_entity_tokens: set[str],
    ) -> tuple[int, float]:
        if not current_entity_tokens:
            return 0, 0.0

        open_positions = await self.trade_repository.list_open_positions()
        matched_positions = 0
        matched_exposure_usd = 0.0

        for position in open_positions:
            signal = position.signal
            analysis = signal.analysis if signal is not None else None
            if analysis is None:
                continue

            position_entity_tokens = _extract_query_entity_tokens(
                query_text=analysis.market_query or "",
                max_tokens=self.settings.risk_anchor_entity_max_tokens,
            )
            if not position_entity_tokens:
                continue

            if current_entity_tokens & position_entity_tokens:
                matched_positions += 1
                matched_exposure_usd += float(position.size_usd)

        return matched_positions, matched_exposure_usd


def resolve_news_age_limit_minutes(settings: Settings) -> int:
    """Return the currently effective freshness limit."""
    if not settings.risk_enable_extended_news_age_window:
        return settings.risk_max_news_age_minutes

    return max(
        settings.risk_max_news_age_minutes,
        settings.risk_extended_max_news_age_minutes,
    )


def should_use_extended_news_age_window(
    *,
    settings: Settings,
    news_age_minutes: int,
) -> bool:
    """Return True when an older signal is only allowed by the extended paper window."""
    if not settings.risk_enable_extended_news_age_window:
        return False

    return (
        news_age_minutes > settings.risk_max_news_age_minutes
        and news_age_minutes <= resolve_news_age_limit_minutes(settings)
    )


def resolve_news_age_size_multiplier(
    *,
    settings: Settings,
    news_age_minutes: int,
) -> float:
    """Reduce size for older-but-still-allowed paper trades."""
    if should_use_extended_news_age_window(
        settings=settings,
        news_age_minutes=news_age_minutes,
    ):
        return settings.risk_extended_news_age_size_multiplier

    return 1.0


def evaluate_risk_case(
    *,
    settings: Settings,
    signal_status: str,
    confidence: float,
    relevance: float,
    news_age_minutes: int,
    liquidity: float,
    edge: float,
    match_score: float,
    existing_open_position: bool,
    entity_key: str | None = None,
    entity_open_positions_count: int = 0,
    entity_open_exposure_used_usd: float = 0.0,
    bid_ask_spread: float | None = None,
    yes_entry_slippage: float | None = None,
    daily_exposure_used_usd: float,
    analysis_trade_count: int = 0,
    top_candidate_score_delta: float | None = None,
    query_text: str = "",
    market_question: str = "",
) -> RiskCheckResult:
    """
    Pure deterministic helper for local verification and unit tests.
    """
    blockers: list[str] = []
    effective_news_age_limit_minutes = resolve_news_age_limit_minutes(settings)
    approved_size_multiplier = resolve_news_age_size_multiplier(
        settings=settings,
        news_age_minutes=news_age_minutes,
    )

    if signal_status != SignalStatus.ACTIONABLE.value:
        blockers.append(f"signal_not_actionable:{signal_status}")

    if confidence < settings.risk_min_confidence:
        blockers.append(
            f"confidence_below_threshold:{confidence:.4f}<{settings.risk_min_confidence:.4f}"
        )

    if relevance < settings.risk_min_relevance:
        blockers.append(
            f"relevance_below_threshold:{relevance:.4f}<{settings.risk_min_relevance:.4f}"
        )

    if news_age_minutes > effective_news_age_limit_minutes:
        blockers.append(
            f"news_too_old:{news_age_minutes}>{effective_news_age_limit_minutes}"
        )

    if liquidity < settings.risk_min_market_liquidity:
        blockers.append(
            f"liquidity_too_low:{liquidity:.2f}<{settings.risk_min_market_liquidity:.2f}"
        )

    if edge <= settings.risk_priced_in_edge_threshold:
        blockers.append(
            f"priced_in_or_converged:{edge:.4f}<={settings.risk_priced_in_edge_threshold:.4f}"
        )

    if match_score < settings.risk_min_match_score:
        blockers.append(
            f"match_score_too_low:{match_score:.4f}<{settings.risk_min_match_score:.4f}"
        )

    if (
        bid_ask_spread is not None
        and bid_ask_spread > settings.risk_max_bid_ask_spread
    ):
        blockers.append(
            f"spread_too_wide:{bid_ask_spread:.4f}>{settings.risk_max_bid_ask_spread:.4f}"
        )

    if (
        yes_entry_slippage is not None
        and yes_entry_slippage > settings.risk_max_yes_entry_slippage
    ):
        blockers.append(
            "yes_entry_slippage_too_high:"
            f"{yes_entry_slippage:.4f}>{settings.risk_max_yes_entry_slippage:.4f}"
        )

    anchor_overlap_count, query_anchor_tokens = _query_market_anchor_stats(
        settings=settings,
        query_text=query_text,
        market_question=market_question,
    )
    overlap_count, max_overlap_token_length = _query_market_overlap_stats(
        query_text=query_text,
        market_question=market_question,
    )
    if (
        not _has_sufficient_query_market_overlap(
            settings=settings,
            overlap_count=overlap_count,
            max_overlap_token_length=max_overlap_token_length,
        )
        and anchor_overlap_count <= 0
    ):
        blockers.append(
            "query_market_overlap_too_low:"
            f"count={overlap_count},max_len={max_overlap_token_length}"
        )

    if query_anchor_tokens and anchor_overlap_count < settings.risk_min_anchor_entity_overlap:
        blockers.append(
            "anchor_entity_overlap_too_low:"
            f"anchors={','.join(sorted(query_anchor_tokens))},count={anchor_overlap_count}"
        )

    if (
        settings.risk_block_on_existing_position
        and existing_open_position
    ):
        blockers.append("duplicate_market_position_exists")

    if (
        entity_key is not None
        and entity_open_positions_count >= settings.risk_max_open_positions_per_entity
    ):
        blockers.append(
            "entity_open_position_limit_reached:"
            f"{entity_key}:{entity_open_positions_count}>="
            f"{settings.risk_max_open_positions_per_entity}"
        )

    if (
        entity_key is not None
        and entity_open_exposure_used_usd >= settings.risk_max_entity_open_exposure_usd
    ):
        blockers.append(
            "entity_open_exposure_limit_reached:"
            f"{entity_key}:{entity_open_exposure_used_usd:.2f}>="
            f"{settings.risk_max_entity_open_exposure_usd:.2f}"
        )

    if analysis_trade_count >= settings.risk_max_trades_per_analysis:
        blockers.append(
            "analysis_trade_limit_reached:"
            f"{analysis_trade_count}>={settings.risk_max_trades_per_analysis}"
        )

    if (
        top_candidate_score_delta is not None
        and top_candidate_score_delta < settings.risk_min_top_candidate_score_delta
    ):
        blockers.append(
            "ambiguous_market_match:"
            f"{top_candidate_score_delta:.4f}<{settings.risk_min_top_candidate_score_delta:.4f}"
        )

    if daily_exposure_used_usd >= settings.risk_max_daily_exposure_usd:
        blockers.append(
            "daily_limit_reached:"
            f"{daily_exposure_used_usd:.2f}>={settings.risk_max_daily_exposure_usd:.2f}"
        )

    allow = not blockers
    approved_size_usd = 0.0
    if allow:
        daily_remaining = max(
            settings.risk_max_daily_exposure_usd - daily_exposure_used_usd,
            0.0,
        )
        entity_remaining = (
            max(
                settings.risk_max_entity_open_exposure_usd - entity_open_exposure_used_usd,
                0.0,
            )
            if entity_key is not None
            else settings.risk_max_trade_size_usd
        )
        liquidity_cap = liquidity * settings.risk_max_liquidity_share
        approved_size_usd = min(
            settings.risk_max_trade_size_usd,
            daily_remaining,
            entity_remaining,
            liquidity_cap,
        )
        approved_size_usd *= approved_size_multiplier
        if approved_size_usd <= 0:
            allow = False
            blockers.append("approved_size_non_positive")

    return RiskCheckResult(
        allow=allow,
        blockers=blockers,
        approved_size_usd=round(approved_size_usd, 2),
    )


def _query_market_overlap_stats(
    *,
    query_text: str,
    market_question: str,
) -> tuple[int, int]:
    query_tokens = _tokenize_overlap_text(query_text)
    market_tokens = _tokenize_overlap_text(market_question)
    overlap_tokens = query_tokens & market_tokens
    if not overlap_tokens:
        return 0, 0
    return len(overlap_tokens), max(len(token) for token in overlap_tokens)


def _has_sufficient_query_market_overlap(
    *,
    settings: Settings,
    overlap_count: int,
    max_overlap_token_length: int,
) -> bool:
    return (
        overlap_count >= settings.risk_min_query_market_token_overlap
        or max_overlap_token_length >= settings.risk_min_query_market_overlap_token_length
    )


def _tokenize_overlap_text(value: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    return {token for token in tokens if token not in OVERLAP_STOPWORDS and len(token) > 1}


def _query_market_anchor_stats(
    *,
    settings: Settings,
    query_text: str,
    market_question: str,
) -> tuple[int, set[str]]:
    query_anchor_tokens = _extract_query_anchor_tokens(
        query_text=query_text,
        max_tokens=settings.risk_anchor_entity_max_tokens,
    )
    if not query_anchor_tokens:
        return 0, set()

    market_tokens = _tokenize_overlap_text(market_question)
    overlap_count = len(query_anchor_tokens & market_tokens)
    return overlap_count, query_anchor_tokens


def _extract_query_anchor_tokens(
    *,
    query_text: str,
    max_tokens: int,
) -> set[str]:
    query_tokens = _tokenize_overlap_text(query_text)
    filtered_tokens = [
        token for token in query_tokens if token not in ANCHOR_GENERIC_TOKENS
    ]
    ranked_tokens = sorted(filtered_tokens, key=lambda token: (-len(token), token))
    return set(ranked_tokens[:max_tokens])


def _extract_query_entity_tokens(
    *,
    query_text: str,
    max_tokens: int,
) -> set[str]:
    anchor_tokens = _extract_query_anchor_tokens(
        query_text=query_text,
        max_tokens=max_tokens,
    )
    if anchor_tokens:
        return anchor_tokens

    query_tokens = _tokenize_overlap_text(query_text)
    ranked_tokens = sorted(query_tokens, key=lambda token: (-len(token), token))
    return set(ranked_tokens[:max_tokens])


def _build_entity_key(tokens: set[str]) -> str | None:
    if not tokens:
        return None
    return "|".join(sorted(tokens))


def _resolve_bid_ask_spread(
    *,
    best_bid: float | None,
    best_ask: float | None,
) -> float | None:
    if best_bid is None or best_ask is None:
        return None
    if best_ask < best_bid:
        return None
    return round(best_ask - best_bid, 4)


def _resolve_yes_entry_slippage(
    *,
    direction: str,
    reference_market_price: float,
    best_ask: float | None,
) -> float | None:
    # Safe version:
    # - apply only to YES-side entries
    # - use best ask as immediate executable buy price
    # - leave NO-side token-level slippage as a future TODO behind a CLOB adapter
    if direction != VerdictDirection.YES.value:
        return None
    if best_ask is None:
        return None
    if best_ask < reference_market_price:
        return 0.0
    return round(best_ask - reference_market_price, 4)


async def run_risk_engine(
    session: AsyncSession,
    settings: Settings,
    *,
    signal_id: int | None = None,
) -> RiskDecision:
    """Convenience entrypoint for one risk evaluation."""
    engine = RiskEngine(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        trade_repository=TradeRepository(session),
    )
    return await engine.evaluate(signal_id=signal_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic risk checks for one signal.")
    parser.add_argument(
        "--signal-id",
        type=int,
        default=None,
        help="Evaluate a specific signals.id. Defaults to the latest signal.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        result = await run_risk_engine(
            session,
            settings,
            signal_id=args.signal_id,
        )
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
