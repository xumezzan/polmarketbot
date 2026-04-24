from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ReconciliationStatus
from app.models.reconciliation_run import ReconciliationRun


class ReconciliationRepository:
    """Persistence helper for phase-4 reconciliation snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_started(self, *, started_at: datetime) -> ReconciliationRun:
        run = ReconciliationRun(
            started_at=started_at,
            status=ReconciliationStatus.PASSED,
            mismatch_count=0,
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def finish(
        self,
        *,
        run: ReconciliationRun,
        status: ReconciliationStatus,
        mismatch_count: int,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ReconciliationRun:
        run.finished_at = datetime.now(UTC)
        run.status = status
        run.mismatch_count = mismatch_count
        run.details = details
        run.error = error
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def get_latest(self) -> ReconciliationRun | None:
        stmt = sa.select(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()
