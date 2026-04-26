from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis import Analysis
from app.models.forecast_observation import ForecastObservation
from app.models.enums import SignalStatus
from app.models.signal import Signal


class SignalRepository:
    """Persistence helper for derived trading signals."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _with_context(self) -> sa.Select[tuple[Signal]]:
        return sa.select(Signal).options(
            selectinload(Signal.analysis).selectinload(Analysis.news_item),
        )

    async def get_by_id(self, signal_id: int) -> Signal | None:
        """Return one signal with analysis/news context loaded."""
        stmt = self._with_context().where(Signal.id == signal_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest(self) -> Signal | None:
        """Return the latest signal with analysis/news context loaded."""
        stmt = self._with_context().order_by(Signal.id.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_recent(self, *, limit: int = 20) -> list[Signal]:
        """Return latest signals with analysis/news context loaded."""
        stmt = self._with_context().order_by(Signal.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_created_between(
        self,
        *,
        since: datetime,
        until: datetime,
        signal_statuses: list[SignalStatus] | None = None,
    ) -> list[Signal]:
        """Return signals created in a window with linked analysis/news context."""
        stmt = (
            self._with_context()
            .where(
                Signal.created_at >= since,
                Signal.created_at <= until,
            )
            .order_by(Signal.created_at, Signal.id)
        )
        if signal_statuses:
            stmt = stmt.where(Signal.signal_status.in_(signal_statuses))
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_without_observation(
        self,
        *,
        signal_statuses: list[SignalStatus] | None = None,
    ) -> list[Signal]:
        """Return signals that still have no resolved forecast observation row."""
        stmt = (
            self._with_context()
            .outerjoin(ForecastObservation, ForecastObservation.signal_id == Signal.id)
            .where(ForecastObservation.id.is_(None))
            .order_by(Signal.created_at, Signal.id)
        )
        if signal_statuses:
            stmt = stmt.where(Signal.signal_status.in_(signal_statuses))
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_analysis_and_market(
        self,
        *,
        analysis_id: int,
        market_id: str,
    ) -> Signal | None:
        """Return the latest signal for one analysis/market pair."""
        stmt = (
            sa.select(Signal)
            .where(
                Signal.analysis_id == analysis_id,
                Signal.market_id == market_id,
            )
            .order_by(Signal.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        analysis_id: int,
        market_id: str,
        market_slug: str | None,
        market_question: str | None,
        market_price: float,
        execution_price: float | None,
        raw_fair_probability: float | None,
        fair_probability: float,
        raw_edge: float | None,
        edge: float,
        estimated_fee_rate: float | None,
        estimated_fee_per_share: float | None,
        market_consensus_weight: float | None,
        calibration_sample_count: int | None,
        signal_status: SignalStatus,
        explanation: str,
    ) -> Signal:
        """Insert or update one signal row for an analysis/market pair."""
        signal = await self.get_by_analysis_and_market(
            analysis_id=analysis_id,
            market_id=market_id,
        )

        if signal is None:
            signal = Signal(
                analysis_id=analysis_id,
                market_id=market_id,
                market_slug=market_slug,
                market_question=market_question,
                market_price=market_price,
                execution_price=execution_price,
                raw_fair_probability=raw_fair_probability,
                fair_probability=fair_probability,
                raw_edge=raw_edge,
                edge=edge,
                estimated_fee_rate=estimated_fee_rate,
                estimated_fee_per_share=estimated_fee_per_share,
                market_consensus_weight=market_consensus_weight,
                calibration_sample_count=calibration_sample_count,
                signal_status=signal_status,
                explanation=explanation,
            )
            self.session.add(signal)
        else:
            signal.market_slug = market_slug
            signal.market_question = market_question
            signal.market_price = market_price
            signal.execution_price = execution_price
            signal.raw_fair_probability = raw_fair_probability
            signal.fair_probability = fair_probability
            signal.raw_edge = raw_edge
            signal.edge = edge
            signal.estimated_fee_rate = estimated_fee_rate
            signal.estimated_fee_per_share = estimated_fee_per_share
            signal.market_consensus_weight = market_consensus_weight
            signal.calibration_sample_count = calibration_sample_count
            signal.signal_status = signal_status
            signal.explanation = explanation

        await self.session.commit()
        await self.session.refresh(signal)
        return signal

    async def count(self) -> int:
        """Return total number of stored signals."""
        stmt = sa.select(sa.func.count()).select_from(Signal)
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_created_since(self, *, since: datetime) -> int:
        """Return signals count created since a timestamp."""
        stmt = (
            sa.select(sa.func.count())
            .select_from(Signal)
            .where(Signal.created_at >= since)
        )
        return int((await self.session.execute(stmt)).scalar_one())
