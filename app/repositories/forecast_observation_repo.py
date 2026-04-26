from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.forecast_observation import ForecastObservation


class ForecastObservationRepository:
    """Persistence helper for resolved forecast outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_position_id(self, *, position_id: int) -> ForecastObservation | None:
        stmt = sa.select(ForecastObservation).where(ForecastObservation.position_id == position_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_signal_id(self, *, signal_id: int) -> ForecastObservation | None:
        stmt = sa.select(ForecastObservation).where(ForecastObservation.signal_id == signal_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert_for_signal(
        self,
        *,
        signal_id: int,
        analysis_id: int,
        position_id: int | None,
        market_id: str,
        provider: str | None,
        model: str | None,
        side: str,
        raw_probability: float,
        calibrated_probability: float,
        market_price: float,
        execution_price: float,
        outcome_value: float,
        outcome_label: str | None,
        brier_score: float,
        resolved_at: datetime,
    ) -> ForecastObservation:
        observation = await self.get_by_signal_id(signal_id=signal_id)

        if observation is None:
            observation = ForecastObservation(
                signal_id=signal_id,
                analysis_id=analysis_id,
                position_id=position_id,
                market_id=market_id,
                provider=provider,
                model=model,
                side=side,
                raw_probability=raw_probability,
                calibrated_probability=calibrated_probability,
                market_price=market_price,
                execution_price=execution_price,
                outcome_value=outcome_value,
                outcome_label=outcome_label,
                brier_score=brier_score,
                resolved_at=resolved_at,
            )
            self.session.add(observation)
        else:
            observation.signal_id = signal_id
            observation.analysis_id = analysis_id
            if position_id is not None and observation.position_id is None:
                observation.position_id = position_id
            observation.market_id = market_id
            observation.provider = provider
            observation.model = model
            observation.side = side
            observation.raw_probability = raw_probability
            observation.calibrated_probability = calibrated_probability
            observation.market_price = market_price
            observation.execution_price = execution_price
            observation.outcome_value = outcome_value
            observation.outcome_label = outcome_label
            observation.brier_score = brier_score
            observation.resolved_at = resolved_at

        await self.session.commit()
        await self.session.refresh(observation)
        return observation

    async def upsert_for_position(
        self,
        *,
        signal_id: int,
        analysis_id: int,
        position_id: int,
        market_id: str,
        provider: str | None,
        model: str | None,
        side: str,
        raw_probability: float,
        calibrated_probability: float,
        market_price: float,
        execution_price: float,
        outcome_value: float,
        outcome_label: str | None,
        brier_score: float,
        resolved_at: datetime,
    ) -> ForecastObservation:
        return await self.upsert_for_signal(
            signal_id=signal_id,
            analysis_id=analysis_id,
            position_id=position_id,
            market_id=market_id,
            provider=provider,
            model=model,
            side=side,
            raw_probability=raw_probability,
            calibrated_probability=calibrated_probability,
            market_price=market_price,
            execution_price=execution_price,
            outcome_value=outcome_value,
            outcome_label=outcome_label,
            brier_score=brier_score,
            resolved_at=resolved_at,
        )

    async def list_for_provider_model(
        self,
        *,
        provider: str | None,
        model: str | None,
        limit: int = 1000,
    ) -> list[ForecastObservation]:
        stmt = sa.select(ForecastObservation)

        if provider:
            stmt = stmt.where(ForecastObservation.provider == provider)
        if model:
            stmt = stmt.where(ForecastObservation.model == model)

        stmt = stmt.order_by(ForecastObservation.resolved_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def average_brier_score_since(
        self,
        *,
        since: datetime,
        provider: str | None = None,
        model: str | None = None,
    ) -> float | None:
        stmt = sa.select(sa.func.avg(ForecastObservation.brier_score)).where(
            ForecastObservation.resolved_at >= since
        )
        if provider:
            stmt = stmt.where(ForecastObservation.provider == provider)
        if model:
            stmt = stmt.where(ForecastObservation.model == model)
        value = (await self.session.execute(stmt)).scalar_one_or_none()
        return None if value is None else float(value)
