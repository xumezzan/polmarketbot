import argparse
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.analysis import Analysis
from app.models.enums import SignalStatus, VerdictDirection
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.forecast_observation_repo import ForecastObservationRepository
from app.repositories.signal_repo import SignalRepository
from app.schemas.market import MarketCandidate
from app.schemas.signal import SignalEvaluation, SignalRunResult
from app.services.forecasting import (
    EdgeEstimate,
    CalibrationPoint,
    build_execution_edge,
    calibrate_probability,
)


logger = logging.getLogger(__name__)


class SignalEngineError(Exception):
    """Raised when the signal engine cannot build signals."""


class SignalEngine:
    """Convert analysis + market candidates into persisted signal rows."""

    def __init__(
        self,
        *,
        settings: Settings,
        analysis_repository: AnalysisRepository,
        signal_repository: SignalRepository,
        forecast_observation_repository: ForecastObservationRepository,
    ) -> None:
        self.settings = settings
        self.analysis_repository = analysis_repository
        self.signal_repository = signal_repository
        self.forecast_observation_repository = forecast_observation_repository

    async def run(self, analysis_id: int | None = None) -> SignalRunResult:
        analysis = await self._load_analysis(analysis_id)
        if analysis is None:
            raise SignalEngineError("No analysis found for signal generation.")

        candidates = self._load_candidates_from_snapshot(analysis)
        if not candidates:
            raise SignalEngineError(
                "No market matching candidates found. Run stage 6 before stage 7."
            )

        evaluations: list[SignalEvaluation] = []
        for candidate in candidates:
            evaluation = await self._evaluate_candidate(analysis=analysis, candidate=candidate)
            evaluations.append(evaluation)

        result = SignalRunResult(
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            evaluated_count=len(evaluations),
            actionable_count=sum(
                1 for item in evaluations if item.signal_status == SignalStatus.ACTIONABLE.value
            ),
            watchlist_count=sum(
                1 for item in evaluations if item.signal_status == SignalStatus.WATCHLIST.value
            ),
            rejected_count=sum(
                1 for item in evaluations if item.signal_status == SignalStatus.REJECTED.value
            ),
            signals=evaluations,
        )

        await self.analysis_repository.save_signal_engine_snapshot(
            analysis_id=analysis.id,
            snapshot={
                "generated_at": datetime.now(UTC).isoformat(),
                "evaluated_count": result.evaluated_count,
                "actionable_count": result.actionable_count,
                "watchlist_count": result.watchlist_count,
                "rejected_count": result.rejected_count,
                "signals": [signal.model_dump(mode="json") for signal in result.signals],
            },
        )

        log_event(
            logger,
            "signal_engine_completed",
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            evaluated_count=result.evaluated_count,
            actionable_count=result.actionable_count,
            watchlist_count=result.watchlist_count,
            rejected_count=result.rejected_count,
        )
        return result

    async def _evaluate_candidate(
        self,
        *,
        analysis: Analysis,
        candidate: MarketCandidate,
    ) -> SignalEvaluation:
        market_price = self._select_market_price(analysis=analysis, candidate=candidate)
        raw_probability = float(analysis.fair_probability)
        calibration_result = await self._calibrate_probability(
            analysis=analysis,
            raw_probability=raw_probability,
        )
        if analysis.direction == VerdictDirection.NONE:
            edge_estimate = EdgeEstimate(
                reference_market_price=round(market_price, 4),
                execution_price=round(market_price, 4),
                raw_probability=round(raw_probability, 4),
                calibrated_probability=round(calibration_result.calibrated_probability, 4),
                raw_edge=round(raw_probability - market_price, 4),
                net_edge=round(calibration_result.calibrated_probability - market_price, 4),
                estimated_fee_rate=0.0,
                estimated_fee_per_share=0.0,
                market_consensus_weight=0.0,
            )
        else:
            edge_estimate = build_execution_edge(
                settings=self.settings,
                direction=analysis.direction.value,
                candidate=candidate,
                reference_market_price=market_price,
                raw_probability=raw_probability,
                calibrated_probability=calibration_result.calibrated_probability,
            )
        status, explanation = self._classify_signal(
            analysis=analysis,
            candidate=candidate,
            market_price=market_price,
            execution_price=edge_estimate.execution_price,
            raw_edge=edge_estimate.raw_edge,
            edge=edge_estimate.net_edge,
            fair_probability=edge_estimate.calibrated_probability,
            raw_probability=raw_probability,
            calibration_sample_count=calibration_result.sample_count,
            estimated_fee_per_share=edge_estimate.estimated_fee_per_share,
            market_consensus_weight=edge_estimate.market_consensus_weight,
        )

        signal = await self.signal_repository.upsert(
            analysis_id=analysis.id,
            market_id=candidate.market_id,
            market_slug=candidate.slug,
            market_question=candidate.question,
            market_price=market_price,
            execution_price=edge_estimate.execution_price,
            raw_fair_probability=raw_probability,
            fair_probability=edge_estimate.calibrated_probability,
            raw_edge=edge_estimate.raw_edge,
            edge=edge_estimate.net_edge,
            estimated_fee_rate=edge_estimate.estimated_fee_rate,
            estimated_fee_per_share=edge_estimate.estimated_fee_per_share,
            market_consensus_weight=edge_estimate.market_consensus_weight,
            calibration_sample_count=calibration_result.sample_count,
            signal_status=status,
            explanation=explanation,
        )

        log_event(
            logger,
            "signal_generated",
            signal_id=signal.id,
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            market_id=candidate.market_id,
            signal_status=status.value,
            edge=edge_estimate.net_edge,
        )

        return SignalEvaluation(
            signal_id=signal.id,
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            market_id=candidate.market_id,
            market_question=candidate.question,
            direction=analysis.direction.value,
            market_price=market_price,
            execution_price=edge_estimate.execution_price,
            raw_fair_probability=raw_probability,
            fair_probability=edge_estimate.calibrated_probability,
            raw_edge=edge_estimate.raw_edge,
            edge=edge_estimate.net_edge,
            estimated_fee_rate=edge_estimate.estimated_fee_rate,
            estimated_fee_per_share=edge_estimate.estimated_fee_per_share,
            market_consensus_weight=edge_estimate.market_consensus_weight,
            calibration_sample_count=calibration_result.sample_count,
            signal_status=status.value,
            explanation=explanation,
            candidate=candidate,
        )

    async def _calibrate_probability(
        self,
        *,
        analysis: Analysis,
        raw_probability: float,
    ):
        raw_response = analysis.raw_response or {}
        history = await self.forecast_observation_repository.list_for_provider_model(
            provider=str(raw_response.get("provider")) if analysis.llm_provider is None and raw_response.get("provider") is not None else analysis.llm_provider,
            model=str(raw_response.get("model")) if analysis.llm_model is None and raw_response.get("model") is not None else analysis.llm_model,
        )
        points = [
            CalibrationPoint(
                raw_probability=float(item.raw_probability),
                outcome_value=float(item.outcome_value),
            )
            for item in history
        ]
        return calibrate_probability(
            settings=self.settings,
            raw_probability=raw_probability,
            history=points,
        )

    async def _load_analysis(self, analysis_id: int | None) -> Analysis | None:
        if analysis_id is not None:
            return await self.analysis_repository.get_by_id(analysis_id)
        return await self.analysis_repository.get_latest()

    def _load_candidates_from_snapshot(self, analysis: Analysis) -> list[MarketCandidate]:
        raw_response = analysis.raw_response or {}
        snapshots = raw_response.get("snapshots") or {}
        market_matching = snapshots.get("market_matching") or {}
        raw_candidates = market_matching.get("candidates") or []
        return [MarketCandidate.model_validate(item) for item in raw_candidates]

    def _select_market_price(
        self,
        *,
        analysis: Analysis,
        candidate: MarketCandidate,
    ) -> float:
        direction = analysis.direction

        if direction == VerdictDirection.YES:
            if candidate.yes_price is not None:
                return candidate.yes_price
            if candidate.last_trade_price is not None:
                return candidate.last_trade_price

        if direction == VerdictDirection.NO:
            if candidate.no_price is not None:
                return candidate.no_price
            if candidate.yes_price is not None:
                return round(1 - candidate.yes_price, 4)

        # For NONE we keep a neutral reference price so the rejected signal is explainable.
        if candidate.yes_price is not None:
            return candidate.yes_price
        if candidate.last_trade_price is not None:
            return candidate.last_trade_price

        raise SignalEngineError(
            f"Candidate {candidate.market_id} has no usable market price."
        )

    def _classify_signal(
        self,
        *,
        analysis: Analysis,
        candidate: MarketCandidate,
        market_price: float,
        execution_price: float,
        raw_edge: float,
        edge: float,
        fair_probability: float,
        raw_probability: float,
        calibration_sample_count: int,
        estimated_fee_per_share: float,
        market_consensus_weight: float,
    ) -> tuple[SignalStatus, str]:
        relevance = float(analysis.relevance)
        confidence = float(analysis.confidence)
        direction = analysis.direction

        if direction == VerdictDirection.NONE:
            return (
                SignalStatus.REJECTED,
                (
                    f"Rejected because direction=NONE. raw_probability={raw_probability:.4f}, "
                    f"calibrated_probability={fair_probability:.4f}, "
                    f"reference_market_price={market_price:.4f}, net_edge={edge:.4f}."
                ),
            )

        if (
            edge > self.settings.signal_actionable_edge_threshold
            and confidence > self.settings.signal_actionable_confidence_threshold
            and relevance > self.settings.signal_actionable_relevance_threshold
        ):
            return (
                SignalStatus.ACTIONABLE,
                (
                    f"Actionable: net_edge={edge:.4f} exceeds "
                    f"{self.settings.signal_actionable_edge_threshold:.4f}, "
                    f"confidence={confidence:.4f}, relevance={relevance:.4f}, "
                    f"market_price={market_price:.4f}, execution_price={execution_price:.4f}, "
                    f"raw_probability={raw_probability:.4f}, "
                    f"calibrated_probability={fair_probability:.4f}, "
                    f"raw_edge={raw_edge:.4f}, fee_per_share={estimated_fee_per_share:.6f}, "
                    f"consensus_weight={market_consensus_weight:.4f}, "
                    f"calibration_samples={calibration_sample_count}, "
                    f"match_score={candidate.match_score:.4f}."
                ),
            )

        if edge > self.settings.signal_watchlist_edge_threshold:
            return (
                SignalStatus.WATCHLIST,
                (
                    f"Watchlist: positive net_edge={edge:.4f}, but actionable thresholds were not all met. "
                    f"confidence={confidence:.4f}, relevance={relevance:.4f}, "
                    f"market_price={market_price:.4f}, execution_price={execution_price:.4f}, "
                    f"raw_probability={raw_probability:.4f}, "
                    f"calibrated_probability={fair_probability:.4f}, raw_edge={raw_edge:.4f}."
                ),
            )

        return (
            SignalStatus.REJECTED,
            (
                f"Rejected: net_edge={edge:.4f} is not above watchlist threshold "
                f"{self.settings.signal_watchlist_edge_threshold:.4f}. "
                f"market_price={market_price:.4f}, execution_price={execution_price:.4f}, "
                f"raw_probability={raw_probability:.4f}, "
                f"calibrated_probability={fair_probability:.4f}, raw_edge={raw_edge:.4f}, "
                f"confidence={confidence:.4f}, relevance={relevance:.4f}."
            ),
        )


def evaluate_signal_candidate(
    *,
    settings: Settings,
    direction: str,
    relevance: float,
    confidence: float,
    fair_probability: float,
    market_price: float,
) -> tuple[str, float]:
    """
    Small pure helper used for quick fake-data verification.

    Returns:
        signal_status, edge
    """
    edge = round(fair_probability - market_price, 4)

    if direction == VerdictDirection.NONE.value:
        return SignalStatus.REJECTED.value, edge

    if (
        edge > settings.signal_actionable_edge_threshold
        and confidence > settings.signal_actionable_confidence_threshold
        and relevance > settings.signal_actionable_relevance_threshold
    ):
        return SignalStatus.ACTIONABLE.value, edge

    if edge > settings.signal_watchlist_edge_threshold:
        return SignalStatus.WATCHLIST.value, edge

    return SignalStatus.REJECTED.value, edge


async def run_signal_engine(
    session: AsyncSession,
    settings: Settings,
    *,
    analysis_id: int | None = None,
) -> SignalRunResult:
    """Convenience entrypoint for one signal engine run."""
    engine = SignalEngine(
        settings=settings,
        analysis_repository=AnalysisRepository(session),
        signal_repository=SignalRepository(session),
        forecast_observation_repository=ForecastObservationRepository(session),
    )
    return await engine.run(analysis_id=analysis_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate signal rows from matched market candidates.")
    parser.add_argument(
        "--analysis-id",
        type=int,
        default=None,
        help="Generate signals for a specific analyses.id. Defaults to the latest analysis.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        result = await run_signal_engine(
            session,
            settings,
            analysis_id=args.analysis_id,
        )
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
