import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis import Analysis
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
        fair_probability: float,
        edge: float,
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
                fair_probability=fair_probability,
                edge=edge,
                signal_status=signal_status,
                explanation=explanation,
            )
            self.session.add(signal)
        else:
            signal.market_slug = market_slug
            signal.market_question = market_question
            signal.market_price = market_price
            signal.fair_probability = fair_probability
            signal.edge = edge
            signal.signal_status = signal_status
            signal.explanation = explanation

        await self.session.commit()
        await self.session.refresh(signal)
        return signal

    async def count(self) -> int:
        """Return total number of stored signals."""
        stmt = sa.select(sa.func.count()).select_from(Signal)
        return int((await self.session.execute(stmt)).scalar_one())
