from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExecutionIntentStatus, ExecutionMode, MarketSide
from app.models.execution_intent import ExecutionIntent


class ExecutionIntentRepository:
    """Persistence helper for shadow/live execution intents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        signal_id: int,
        market_id: str,
        market_question: str | None,
        side: MarketSide,
        token_id: str,
        execution_mode: ExecutionMode,
        status: ExecutionIntentStatus,
        target_size_usd: float,
        shares: float,
        requested_price: float,
        max_acceptable_price: float,
        client_order_id: str,
        generated_payload: dict[str, Any] | None,
        simulation_result: dict[str, Any] | None = None,
        exchange_order_id: str | None = None,
        error: str | None = None,
        executed_at: datetime | None = None,
    ) -> ExecutionIntent:
        intent = ExecutionIntent(
            signal_id=signal_id,
            market_id=market_id,
            market_question=market_question,
            side=side,
            token_id=token_id,
            execution_mode=execution_mode,
            status=status,
            target_size_usd=target_size_usd,
            shares=shares,
            requested_price=requested_price,
            max_acceptable_price=max_acceptable_price,
            client_order_id=client_order_id,
            generated_payload=generated_payload,
            simulation_result=simulation_result,
            exchange_order_id=exchange_order_id,
            error=error,
            executed_at=executed_at,
        )
        self.session.add(intent)
        await self.session.commit()
        await self.session.refresh(intent)
        return intent

    async def get_by_id(self, *, intent_id: int) -> ExecutionIntent | None:
        stmt = sa.select(ExecutionIntent).where(ExecutionIntent.id == intent_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_recent(self, *, limit: int = 20) -> list[ExecutionIntent]:
        stmt = sa.select(ExecutionIntent).order_by(ExecutionIntent.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def mark_submitted(
        self,
        *,
        intent: ExecutionIntent,
        exchange_order_id: str | None,
        simulation_result: dict[str, Any] | None,
    ) -> ExecutionIntent:
        intent.status = ExecutionIntentStatus.SUBMITTED
        intent.exchange_order_id = exchange_order_id
        intent.simulation_result = simulation_result
        intent.error = None
        intent.executed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(intent)
        return intent

    async def mark_failed(
        self,
        *,
        intent: ExecutionIntent,
        error: str,
        simulation_result: dict[str, Any] | None = None,
    ) -> ExecutionIntent:
        intent.status = ExecutionIntentStatus.FAILED
        intent.error = error[:2000]
        intent.simulation_result = simulation_result
        intent.executed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(intent)
        return intent
