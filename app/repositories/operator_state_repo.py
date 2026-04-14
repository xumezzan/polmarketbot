from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operator_state import OperatorState


class OperatorStateRepository:
    """Persistence helper for lightweight scheduler/operator runtime state."""

    _SINGLETON_ID = 1

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> OperatorState | None:
        stmt = sa.select(OperatorState).where(OperatorState.id == self._SINGLETON_ID)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_create(self) -> OperatorState:
        state = await self.get()
        if state is not None:
            return state

        state = OperatorState(id=self._SINGLETON_ID)
        self.session.add(state)
        await self.session.commit()
        await self.session.refresh(state)
        return state

    async def mark_cycle_started(self, *, started_at: datetime) -> OperatorState:
        state = await self.get_or_create()
        state.last_cycle_started_at = started_at
        state.last_cycle_finished_at = None
        state.last_cycle_fetched_news_count = None
        state.last_cycle_inserted_news_count = None
        state.last_cycle_error_count = None
        state.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(state)
        return state

    async def mark_cycle_completed(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        fetched_news_count: int,
        inserted_news_count: int,
        error_count: int,
    ) -> OperatorState:
        state = await self.get_or_create()
        state.last_cycle_started_at = started_at
        state.last_cycle_finished_at = finished_at
        state.last_cycle_fetched_news_count = fetched_news_count
        state.last_cycle_inserted_news_count = inserted_news_count
        state.last_cycle_error_count = error_count
        state.last_error = None
        state.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(state)
        return state

    async def mark_cycle_failed(
        self,
        *,
        finished_at: datetime,
        error: str,
    ) -> OperatorState:
        state = await self.get_or_create()
        if state.last_cycle_started_at is None:
            state.last_cycle_started_at = finished_at

        state.last_cycle_finished_at = finished_at
        state.last_cycle_error_count = (
            int(state.last_cycle_error_count) + 1
            if state.last_cycle_error_count is not None
            else 1
        )
        state.last_error = error[:2000]
        state.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(state)
        return state
