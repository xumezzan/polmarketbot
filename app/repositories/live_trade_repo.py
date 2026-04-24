from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import LiveOrderStatus, LivePositionStatus, MarketSide
from app.models.live_order import LiveOrder
from app.models.live_position import LivePosition


class LiveTradeRepository:
    """Persistence helper for live orders and positions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_open_positions(self) -> int:
        stmt = sa.select(sa.func.count()).select_from(LivePosition).where(
            LivePosition.status == LivePositionStatus.OPEN
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_orders(self) -> int:
        stmt = sa.select(sa.func.count()).select_from(LiveOrder)
        return int((await self.session.execute(stmt)).scalar_one())

    async def sum_daily_exposure_used_usd(self, *, day_start: datetime) -> float:
        stmt = sa.select(sa.func.coalesce(sa.func.sum(LiveOrder.size_usd), 0)).where(
            LiveOrder.opened_at >= day_start,
            LiveOrder.status.in_([LiveOrderStatus.OPEN, LiveOrderStatus.FILLED]),
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def get_live_order_by_exchange_order_id(self, *, exchange_order_id: str) -> LiveOrder | None:
        stmt = sa.select(LiveOrder).where(LiveOrder.exchange_order_id == exchange_order_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_open_positions(self) -> list[LivePosition]:
        stmt = (
            sa.select(LivePosition)
            .options(selectinload(LivePosition.live_order))
            .where(LivePosition.status == LivePositionStatus.OPEN)
            .order_by(LivePosition.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_order(
        self,
        *,
        execution_intent_id: int,
        signal_id: int,
        market_id: str,
        side: MarketSide,
        token_id: str,
        client_order_id: str,
        exchange_order_id: str | None,
        requested_price: float,
        filled_price: float | None,
        size_usd: float,
        shares: float,
        status: LiveOrderStatus,
        raw_request: dict[str, Any] | None,
        raw_response: dict[str, Any] | None,
        failure_reason: str | None = None,
    ) -> LiveOrder:
        order = LiveOrder(
            execution_intent_id=execution_intent_id,
            signal_id=signal_id,
            market_id=market_id,
            side=side,
            token_id=token_id,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            requested_price=requested_price,
            filled_price=filled_price,
            size_usd=size_usd,
            shares=shares,
            status=status,
            failure_reason=failure_reason,
            raw_request=raw_request,
            raw_response=raw_response,
        )
        self.session.add(order)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def mark_order_status(
        self,
        *,
        order: LiveOrder,
        status: LiveOrderStatus,
        raw_response: dict[str, Any] | None = None,
        failure_reason: str | None = None,
        filled_price: float | None = None,
    ) -> LiveOrder:
        order.status = status
        order.raw_response = raw_response
        order.failure_reason = failure_reason
        if filled_price is not None:
            order.filled_price = filled_price
        if status in {LiveOrderStatus.CANCELED, LiveOrderStatus.FAILED, LiveOrderStatus.FILLED}:
            order.closed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def create_position(
        self,
        *,
        signal_id: int,
        live_order_id: int,
        market_id: str,
        market_question: str | None,
        side: MarketSide,
        token_id: str,
        entry_price: float,
        size_usd: float,
        shares: float,
    ) -> LivePosition:
        position = LivePosition(
            signal_id=signal_id,
            live_order_id=live_order_id,
            market_id=market_id,
            market_question=market_question,
            side=side,
            token_id=token_id,
            entry_price=entry_price,
            size_usd=size_usd,
            shares=shares,
            status=LivePositionStatus.OPEN,
        )
        self.session.add(position)
        await self.session.commit()
        await self.session.refresh(position)
        return position
