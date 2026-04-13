import argparse
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.enums import MarketSide, VerdictDirection
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.schemas.risk import RiskDecision
from app.schemas.trade import PaperTradeCloseResult, PaperTradeOpenResult, PaperTradeStats
from app.services.risk_engine import RiskEngine


logger = logging.getLogger(__name__)


class PaperTraderError(Exception):
    """Raised when a paper trade cannot be opened or closed."""


def calculate_pnl(*, entry_price: float, exit_price: float, shares: float) -> float:
    """Return the realized PnL for one binary-market position."""
    return round((exit_price - entry_price) * shares, 4)


class PaperTrader:
    """Open, close, and summarize virtual trades."""

    def __init__(
        self,
        *,
        settings: Settings,
        signal_repository: SignalRepository,
        analysis_repository: AnalysisRepository,
        trade_repository: TradeRepository,
    ) -> None:
        self.settings = settings
        self.signal_repository = signal_repository
        self.analysis_repository = analysis_repository
        self.trade_repository = trade_repository
        self.risk_engine = RiskEngine(
            settings=settings,
            signal_repository=signal_repository,
            analysis_repository=analysis_repository,
            trade_repository=trade_repository,
        )

    async def open_position(
        self,
        *,
        signal_id: int | None = None,
        risk_decision: RiskDecision | None = None,
    ) -> PaperTradeOpenResult:
        """Open one paper position from an approved signal."""
        decision = risk_decision
        if decision is None and self.settings.paper_require_risk_approval:
            decision = await self.risk_engine.evaluate(signal_id=signal_id)
            if not decision.allow:
                raise PaperTraderError(
                    "Signal blocked by risk engine: " + ", ".join(decision.blockers)
                )
        if decision is not None:
            if not decision.allow:
                raise PaperTraderError(
                    "Signal blocked by risk engine: " + ", ".join(decision.blockers)
                )
            signal_id = decision.signal_id

        signal = await self._load_signal(signal_id)
        analysis = signal.analysis
        if analysis is None or analysis.news_item is None:
            raise PaperTraderError("Signal is missing linked analysis/news context.")

        side = self._select_side(direction=analysis.direction)
        entry_price = float(signal.market_price)
        if entry_price <= 0:
            raise PaperTraderError(
                f"Signal {signal.id} has non-positive entry price {entry_price:.4f}."
            )

        approved_size_usd = self.settings.risk_max_trade_size_usd
        if decision is not None:
            approved_size_usd = decision.approved_size_usd

        if approved_size_usd <= 0:
            raise PaperTraderError("Risk engine returned non-positive approved_size_usd.")

        shares = round(approved_size_usd / entry_price, 6)
        opened_at = datetime.now(UTC)

        position, trade = await self.trade_repository.open_virtual_trade(
            signal_id=signal.id,
            market_id=signal.market_id,
            market_question=signal.market_question,
            side=side,
            entry_price=entry_price,
            size_usd=approved_size_usd,
            shares=shares,
            opened_at=opened_at,
        )

        action_at = opened_at.isoformat()
        await self.analysis_repository.save_paper_trader_action(
            analysis_id=analysis.id,
            action={
                "action": "OPEN",
                "action_at": action_at,
                "signal_id": signal.id,
                "news_item_id": analysis.news_item_id,
                "position_id": position.id,
                "trade_id": trade.id,
                "market_id": signal.market_id,
                "side": side.value,
                "entry_price": entry_price,
                "size_usd": approved_size_usd,
                "shares": shares,
            },
        )

        log_event(
            logger,
            "paper_trade_opened",
            signal_id=signal.id,
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            position_id=position.id,
            trade_id=trade.id,
            market_id=signal.market_id,
            side=side.value,
            entry_price=entry_price,
            size_usd=approved_size_usd,
            shares=shares,
        )

        return PaperTradeOpenResult(
            signal_id=signal.id,
            analysis_id=analysis.id,
            news_item_id=analysis.news_item_id,
            position_id=position.id,
            trade_id=trade.id,
            market_id=signal.market_id,
            side=side.value,
            entry_price=entry_price,
            size_usd=approved_size_usd,
            shares=shares,
            status=trade.status.value,
            opened_at=opened_at.isoformat(),
        )

    async def close_position(
        self,
        *,
        position_id: int | None = None,
        exit_price: float,
    ) -> PaperTradeCloseResult:
        """Close one open paper position at the provided exit price."""
        if exit_price < 0 or exit_price > 1:
            raise PaperTraderError("exit_price must be between 0 and 1.")

        position = await self._load_position(position_id)
        trade = await self.trade_repository.get_open_trade_for_position(position_id=position.id)
        if trade is None:
            raise PaperTraderError(f"No open paper trade found for position {position.id}.")

        if position.signal is None or position.signal.analysis is None:
            raise PaperTraderError("Position is missing linked signal/analysis context.")

        linked_signal_id = position.signal_id
        analysis = position.signal.analysis
        news_item_id = analysis.news_item_id
        market_id = position.market_id
        side = position.side.value
        entry_price = float(trade.entry_price)
        shares = float(trade.shares)
        size_usd = float(trade.size_usd)

        pnl = calculate_pnl(
            entry_price=entry_price,
            exit_price=exit_price,
            shares=shares,
        )
        closed_at = datetime.now(UTC)

        position, trade = await self.trade_repository.close_virtual_trade(
            position=position,
            trade=trade,
            exit_price=exit_price,
            pnl=pnl,
            closed_at=closed_at,
        )

        action_at = closed_at.isoformat()
        await self.analysis_repository.save_paper_trader_action(
            analysis_id=analysis.id,
            action={
                "action": "CLOSE",
                "action_at": action_at,
                "signal_id": linked_signal_id,
                "news_item_id": news_item_id,
                "position_id": position.id,
                "trade_id": trade.id,
                "market_id": market_id,
                "side": side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "shares": shares,
                "pnl": pnl,
            },
        )

        log_event(
            logger,
            "paper_trade_closed",
            signal_id=linked_signal_id,
            analysis_id=analysis.id,
            news_item_id=news_item_id,
            position_id=position.id,
            trade_id=trade.id,
            market_id=market_id,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
        )

        return PaperTradeCloseResult(
            signal_id=linked_signal_id,
            analysis_id=analysis.id,
            news_item_id=news_item_id,
            position_id=position.id,
            trade_id=trade.id,
            market_id=market_id,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            size_usd=size_usd,
            shares=shares,
            pnl=pnl,
            status=trade.status.value,
            opened_at=trade.opened_at.isoformat(),
            closed_at=closed_at.isoformat(),
        )

    async def get_stats(self) -> PaperTradeStats:
        """Return paper trading metrics derived from persisted trades."""
        stats = await self.trade_repository.get_trade_statistics()
        return PaperTradeStats.model_validate(stats)

    async def _load_signal(self, signal_id: int | None):
        if signal_id is not None:
            signal = await self.signal_repository.get_by_id(signal_id)
        else:
            signal = await self.signal_repository.get_latest()

        if signal is None:
            raise PaperTraderError("No signal found for paper trading.")
        return signal

    async def _load_position(self, position_id: int | None):
        if position_id is not None:
            position = await self.trade_repository.get_position_by_id(position_id=position_id)
        else:
            position = await self.trade_repository.get_latest_open_position()

        if position is None:
            raise PaperTraderError("No open position found to close.")
        return position

    def _select_side(self, *, direction: VerdictDirection) -> MarketSide:
        if direction == VerdictDirection.YES:
            return MarketSide.YES
        if direction == VerdictDirection.NO:
            return MarketSide.NO
        raise PaperTraderError("Cannot open a paper trade when direction=NONE.")


async def open_paper_position(
    session: AsyncSession,
    settings: Settings,
    *,
    signal_id: int | None = None,
    risk_decision: RiskDecision | None = None,
) -> PaperTradeOpenResult:
    """Convenience entrypoint to open one paper position."""
    trader = PaperTrader(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        trade_repository=TradeRepository(session),
    )
    return await trader.open_position(signal_id=signal_id, risk_decision=risk_decision)


async def close_paper_position(
    session: AsyncSession,
    settings: Settings,
    *,
    position_id: int | None = None,
    exit_price: float,
) -> PaperTradeCloseResult:
    """Convenience entrypoint to close one paper position."""
    trader = PaperTrader(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        trade_repository=TradeRepository(session),
    )
    return await trader.close_position(position_id=position_id, exit_price=exit_price)


async def get_paper_trade_stats(
    session: AsyncSession,
    settings: Settings,
) -> PaperTradeStats:
    """Convenience entrypoint to calculate paper-trading metrics."""
    trader = PaperTrader(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        trade_repository=TradeRepository(session),
    )
    return await trader.get_stats()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open, close, and inspect paper trades.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser("open", help="Open a paper position from a signal.")
    open_parser.add_argument(
        "--signal-id",
        type=int,
        default=None,
        help="Open a position for a specific signals.id. Defaults to the latest signal.",
    )

    close_parser = subparsers.add_parser("close", help="Close one open paper position.")
    close_parser.add_argument(
        "--position-id",
        type=int,
        default=None,
        help="Close a specific positions.id. Defaults to the latest open position.",
    )
    close_parser.add_argument(
        "--exit-price",
        type=float,
        required=True,
        help="Exit price between 0 and 1.",
    )

    subparsers.add_parser("stats", help="Show paper-trading statistics.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        if args.command == "open":
            result = await open_paper_position(
                session,
                settings,
                signal_id=args.signal_id,
            )
            print(result.model_dump_json())
            return

        if args.command == "close":
            result = await close_paper_position(
                session,
                settings,
                position_id=args.position_id,
                exit_price=args.exit_price,
            )
            print(result.model_dump_json())
            return

        result = await get_paper_trade_stats(session, settings)
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
