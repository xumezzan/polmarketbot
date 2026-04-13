import argparse
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.enums import SignalStatus
from app.models.signal import Signal
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.schemas.market import MarketCandidate
from app.schemas.risk import RiskCheckResult, RiskDecision


logger = logging.getLogger(__name__)


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

        risk_result = evaluate_risk_case(
            settings=self.settings,
            signal_status=signal.signal_status.value,
            confidence=float(analysis.confidence),
            relevance=float(analysis.relevance),
            news_age_minutes=news_age_minutes,
            liquidity=liquidity,
            edge=float(signal.edge),
            existing_open_position=has_existing_position,
            daily_exposure_used_usd=daily_exposure_used,
        )

        approved_size_usd = 0.0
        if risk_result.allow:
            approved_size_usd = self._approved_size_usd(
                liquidity=liquidity,
                daily_exposure_used_usd=daily_exposure_used,
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
                "liquidity": liquidity,
                "daily_exposure_used_usd": round(daily_exposure_used, 2),
                "existing_open_position": has_existing_position,
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

    def _approved_size_usd(
        self,
        *,
        liquidity: float,
        daily_exposure_used_usd: float,
    ) -> float:
        daily_remaining = max(
            self.settings.risk_max_daily_exposure_usd - daily_exposure_used_usd,
            0.0,
        )
        liquidity_cap = liquidity * self.settings.risk_max_liquidity_share
        return min(
            self.settings.risk_max_trade_size_usd,
            daily_remaining,
            liquidity_cap,
        )


def evaluate_risk_case(
    *,
    settings: Settings,
    signal_status: str,
    confidence: float,
    relevance: float,
    news_age_minutes: int,
    liquidity: float,
    edge: float,
    existing_open_position: bool,
    daily_exposure_used_usd: float,
) -> RiskCheckResult:
    """
    Pure deterministic helper for local verification and unit tests.
    """
    blockers: list[str] = []

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

    if news_age_minutes > settings.risk_max_news_age_minutes:
        blockers.append(
            f"news_too_old:{news_age_minutes}>{settings.risk_max_news_age_minutes}"
        )

    if liquidity < settings.risk_min_market_liquidity:
        blockers.append(
            f"liquidity_too_low:{liquidity:.2f}<{settings.risk_min_market_liquidity:.2f}"
        )

    if edge <= settings.risk_priced_in_edge_threshold:
        blockers.append(
            f"priced_in_or_converged:{edge:.4f}<={settings.risk_priced_in_edge_threshold:.4f}"
        )

    if (
        settings.risk_block_on_existing_position
        and existing_open_position
    ):
        blockers.append("duplicate_market_position_exists")

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
        liquidity_cap = liquidity * settings.risk_max_liquidity_share
        approved_size_usd = min(
            settings.risk_max_trade_size_usd,
            daily_remaining,
            liquidity_cap,
        )
        if approved_size_usd <= 0:
            allow = False
            blockers.append("approved_size_non_positive")

    return RiskCheckResult(
        allow=allow,
        blockers=blockers,
        approved_size_usd=round(approved_size_usd, 2),
    )


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
