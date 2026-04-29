from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anomaly import AnomalyHypothesis, AnomalyObservation
from app.schemas.anomaly import AnomalyHypothesisCreate, AnomalyObservationCreate


class AnomalyRepository:
    """Persistence helper for Anomaly Hunter observations and hypotheses."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_observations(
        self,
        observations: list[AnomalyObservationCreate],
    ) -> list[AnomalyObservation]:
        rows = [
            AnomalyObservation(
                cycle_id=item.cycle_id,
                observed_at=datetime.fromisoformat(item.observed_at),
                observation_type=item.observation_type,
                subject_type=item.subject_type,
                subject_id=item.subject_id,
                severity=item.severity,
                score=item.score,
                title=item.title,
                details=item.details,
            )
            for item in observations
        ]
        self.session.add_all(rows)
        await self.session.commit()
        for row in rows:
            await self.session.refresh(row)
        return rows

    async def create_hypotheses(
        self,
        hypotheses: list[AnomalyHypothesisCreate],
    ) -> list[AnomalyHypothesis]:
        rows = [
            AnomalyHypothesis(
                generated_at=datetime.fromisoformat(item.generated_at),
                window_start=datetime.fromisoformat(item.window_start),
                window_end=datetime.fromisoformat(item.window_end),
                hypothesis_type=item.hypothesis_type,
                status=item.status,
                score=item.score,
                title=item.title,
                rationale=item.rationale,
                evidence=item.evidence,
            )
            for item in hypotheses
        ]
        self.session.add_all(rows)
        await self.session.commit()
        for row in rows:
            await self.session.refresh(row)
        return rows

    async def list_observations_since(
        self,
        *,
        since: datetime,
        limit: int = 5000,
    ) -> list[AnomalyObservation]:
        stmt = (
            sa.select(AnomalyObservation)
            .where(AnomalyObservation.observed_at >= since)
            .order_by(AnomalyObservation.observed_at.desc(), AnomalyObservation.id.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_observations_since(self, *, since: datetime) -> int:
        stmt = (
            sa.select(sa.func.count())
            .select_from(AnomalyObservation)
            .where(AnomalyObservation.observed_at >= since)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_hypotheses_since(
        self,
        *,
        since: datetime,
        limit: int = 20,
    ) -> list[AnomalyHypothesis]:
        stmt = (
            sa.select(AnomalyHypothesis)
            .where(AnomalyHypothesis.generated_at >= since)
            .order_by(AnomalyHypothesis.score.desc(), AnomalyHypothesis.generated_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def latest_hypothesis_generated_at(self) -> datetime | None:
        stmt = sa.select(sa.func.max(AnomalyHypothesis.generated_at))
        return (await self.session.execute(stmt)).scalar_one_or_none()
