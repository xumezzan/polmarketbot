from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis import Analysis
from app.models.enums import MarketSide, PositionStatus, TradeStatus
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import PaperTrade


class TradeRepository:
    """Persistence helper for paper trades and positions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _position_with_context(self) -> sa.Select[tuple[Position]]:
        return sa.select(Position).options(
            selectinload(Position.signal)
            .selectinload(Signal.analysis)
            .selectinload(Analysis.news_item),
        )

    def _trade_with_context(self) -> sa.Select[tuple[PaperTrade]]:
        return sa.select(PaperTrade).options(
            selectinload(PaperTrade.signal)
            .selectinload(Signal.analysis)
            .selectinload(Analysis.news_item),
            selectinload(PaperTrade.position),
        )

    async def get_daily_exposure_used_usd(self, *, day_start: datetime) -> float:
        """Return total paper trade size opened since day_start."""
        stmt = sa.select(sa.func.coalesce(sa.func.sum(PaperTrade.size_usd), 0)).where(
            PaperTrade.opened_at >= day_start
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def has_open_position_for_market(self, *, market_id: str) -> bool:
        """Return True if there is already an open paper position in this market."""
        stmt = (
            sa.select(sa.literal(True))
            .select_from(Position)
            .where(
                Position.market_id == market_id,
                Position.status == PositionStatus.OPEN,
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is True

    async def count_trades_for_analysis(self, *, analysis_id: int) -> int:
        """Return total paper trades already opened from one analysis."""
        stmt = (
            sa.select(sa.func.count())
            .select_from(PaperTrade)
            .join(Signal, PaperTrade.signal_id == Signal.id)
            .where(Signal.analysis_id == analysis_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_position_by_id(self, *, position_id: int) -> Position | None:
        """Return one position with linked signal/analysis/news context."""
        stmt = self._position_with_context().where(Position.id == position_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest_open_position(self) -> Position | None:
        """Return the latest open paper position."""
        stmt = (
            self._position_with_context()
            .where(Position.status == PositionStatus.OPEN)
            .order_by(Position.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_open_positions(self) -> list[Position]:
        """Return all open paper positions with linked signal/analysis/news context."""
        stmt = (
            self._position_with_context()
            .where(Position.status == PositionStatus.OPEN)
            .order_by(Position.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_open_trade_for_position(self, *, position_id: int) -> PaperTrade | None:
        """Return the latest open paper trade attached to a position."""
        stmt = (
            sa.select(PaperTrade)
            .where(
                PaperTrade.position_id == position_id,
                PaperTrade.status == TradeStatus.OPEN,
            )
            .order_by(PaperTrade.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_trades_with_context(
        self,
        *,
        since: datetime | None = None,
    ) -> list[PaperTrade]:
        """Return paper trades with signal/analysis/news context for analytics."""
        stmt = self._trade_with_context().order_by(PaperTrade.id)
        if since is not None:
            stmt = stmt.where(
                sa.or_(
                    PaperTrade.opened_at >= since,
                    PaperTrade.closed_at >= since,
                )
            )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_recent_trades(self, *, limit: int = 5) -> list[PaperTrade]:
        """Return latest paper trades with context."""
        stmt = self._trade_with_context().order_by(PaperTrade.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_recent_closed_trades(self, *, limit: int = 10) -> list[PaperTrade]:
        """Return latest closed paper trades with context."""
        stmt = (
            self._trade_with_context()
            .where(PaperTrade.status == TradeStatus.CLOSED)
            .order_by(PaperTrade.closed_at.desc().nullslast(), PaperTrade.id.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_top_closed_trades(
        self,
        *,
        limit: int = 5,
        descending: bool = True,
    ) -> list[PaperTrade]:
        """Return best or worst closed trades by realized PnL."""
        order_column = PaperTrade.pnl.desc() if descending else PaperTrade.pnl.asc()
        stmt = (
            self._trade_with_context()
            .where(
                PaperTrade.status == TradeStatus.CLOSED,
                PaperTrade.pnl.is_not(None),
            )
            .order_by(order_column, PaperTrade.closed_at.desc().nullslast(), PaperTrade.id.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_open_positions(self) -> int:
        """Return the current number of open paper positions."""
        stmt = sa.select(sa.func.count()).select_from(Position).where(
            Position.status == PositionStatus.OPEN
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_trades(self) -> int:
        """Return total number of paper trades."""
        stmt = sa.select(sa.func.count()).select_from(PaperTrade)
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_opened_trades_since(self, *, since: datetime) -> int:
        """Return paper trades opened since timestamp."""
        stmt = (
            sa.select(sa.func.count())
            .select_from(PaperTrade)
            .where(PaperTrade.opened_at >= since)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_closed_trades_since(self, *, since: datetime) -> int:
        """Return paper trades closed since timestamp."""
        stmt = (
            sa.select(sa.func.count())
            .select_from(PaperTrade)
            .where(
                PaperTrade.status == TradeStatus.CLOSED,
                PaperTrade.closed_at.is_not(None),
                PaperTrade.closed_at >= since,
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def sum_realized_pnl_since(self, *, since: datetime) -> float:
        """Return sum of realized PnL for trades closed since timestamp."""
        stmt = (
            sa.select(sa.func.coalesce(sa.func.sum(PaperTrade.pnl), 0))
            .select_from(PaperTrade)
            .where(
                PaperTrade.status == TradeStatus.CLOSED,
                PaperTrade.closed_at.is_not(None),
                PaperTrade.closed_at >= since,
            )
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def open_virtual_trade(
        self,
        *,
        signal_id: int,
        market_id: str,
        market_question: str | None,
        side: MarketSide,
        entry_price: float,
        size_usd: float,
        shares: float,
        opened_at: datetime,
    ) -> tuple[Position, PaperTrade]:
        """Create one open position and its backing paper trade."""
        position = Position(
            signal_id=signal_id,
            market_id=market_id,
            market_question=market_question,
            side=side,
            entry_price=entry_price,
            size_usd=size_usd,
            shares=shares,
            status=PositionStatus.OPEN,
            opened_at=opened_at,
        )
        self.session.add(position)
        await self.session.flush()

        trade = PaperTrade(
            signal_id=signal_id,
            position_id=position.id,
            market_id=market_id,
            side=side,
            entry_price=entry_price,
            size_usd=size_usd,
            shares=shares,
            status=TradeStatus.OPEN,
            opened_at=opened_at,
        )
        self.session.add(trade)
        await self.session.commit()
        await self.session.refresh(position)
        await self.session.refresh(trade)
        return position, trade

    async def close_virtual_trade(
        self,
        *,
        position: Position,
        trade: PaperTrade,
        exit_price: float,
        pnl: float,
        closed_at: datetime,
        close_reason: str | None = None,
        resolution_outcome: str | None = None,
        resolved_at: datetime | None = None,
    ) -> tuple[Position, PaperTrade]:
        """Close one open position and its linked paper trade."""
        position.status = PositionStatus.CLOSED
        position.closed_at = closed_at
        position.close_reason = close_reason
        position.resolution_outcome = resolution_outcome
        position.resolved_at = resolved_at

        trade.exit_price = exit_price
        trade.pnl = pnl
        trade.status = TradeStatus.CLOSED
        trade.closed_at = closed_at
        trade.close_reason = close_reason
        trade.resolution_outcome = resolution_outcome
        trade.resolved_at = resolved_at

        await self.session.commit()
        await self.session.refresh(position)
        await self.session.refresh(trade)
        return position, trade

    async def get_trade_statistics(self) -> dict[str, float | int | list[int]]:
        """Return aggregated paper-trading statistics."""
        total_trades_stmt = sa.select(sa.func.count()).select_from(PaperTrade)
        total_trades = int((await self.session.execute(total_trades_stmt)).scalar_one())

        open_positions_stmt = sa.select(sa.func.count()).select_from(Position).where(
            Position.status == PositionStatus.OPEN
        )
        open_positions = int((await self.session.execute(open_positions_stmt)).scalar_one())

        closed_stmt = (
            sa.select(PaperTrade)
            .where(PaperTrade.status == TradeStatus.CLOSED)
            .order_by(PaperTrade.id)
        )
        closed_trades = list((await self.session.execute(closed_stmt)).scalars().all())

        closed_count = len(closed_trades)
        pnl_values = [float(trade.pnl or 0.0) for trade in closed_trades]
        winning = [value for value in pnl_values if value > 0]
        losing = [value for value in pnl_values if value < 0]
        total_pnl = sum(pnl_values)
        avg_pnl = total_pnl / closed_count if closed_count else 0.0
        win_rate = len(winning) / closed_count if closed_count else 0.0
        avg_win_pnl = sum(winning) / len(winning) if winning else 0.0
        avg_loss_pnl = sum(losing) / len(losing) if losing else 0.0
        expectancy = (win_rate * avg_win_pnl) + ((1 - win_rate) * avg_loss_pnl)

        return {
            "total_trades": total_trades,
            "closed_trades": closed_count,
            "open_positions": open_positions,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": round(win_rate, 4),
            "avg_pnl": round(avg_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "avg_win_pnl": round(avg_win_pnl, 4),
            "avg_loss_pnl": round(avg_loss_pnl, 4),
            "expectancy": round(expectancy, 4),
            "closed_trade_ids": [trade.id for trade in closed_trades],
        }
