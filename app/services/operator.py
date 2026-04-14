from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.repositories.runtime_flag_repo import RuntimeFlagRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.runtime_flags import RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH
from app.schemas.admin import (
    AdminPaperStatsResponse,
    AdminStatusResponse,
    OpenPositionItem,
    OpenPositionsResponse,
    RecentSignalItem,
    RecentSignalsResponse,
)
from app.schemas.trade import PaperTradeStats


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _to_float(value: float | Decimal) -> float:
    return float(value)


class OperatorService:
    """Read-only operator view over current bot state."""

    def __init__(
        self,
        *,
        news_repository: NewsRepository,
        analysis_repository: AnalysisRepository,
        signal_repository: SignalRepository,
        trade_repository: TradeRepository,
        runtime_flag_repository: RuntimeFlagRepository,
        operator_state_repository: OperatorStateRepository,
    ) -> None:
        self.news_repository = news_repository
        self.analysis_repository = analysis_repository
        self.signal_repository = signal_repository
        self.trade_repository = trade_repository
        self.runtime_flag_repository = runtime_flag_repository
        self.operator_state_repository = operator_state_repository

    async def get_status(self) -> AdminStatusResponse:
        now = datetime.now(UTC)
        since = now - timedelta(hours=24)

        operator_state = await self.operator_state_repository.get_or_create()
        kill_switch_enabled, _ = await self.runtime_flag_repository.get_status(
            key=RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH,
            default=False,
        )

        news_items_count = await self.news_repository.count()
        inserted_news_24h = await self.news_repository.count_created_since(since=since)
        analyses_count = await self.analysis_repository.count()
        analyses_count_24h = await self.analysis_repository.count_created_since(since=since)
        signals_count = await self.signal_repository.count()
        signals_count_24h = await self.signal_repository.count_created_since(since=since)
        paper_trades_count = await self.trade_repository.count_trades()
        open_positions_count = await self.trade_repository.count_open_positions()
        opened_trades_24h = await self.trade_repository.count_opened_trades_since(since=since)
        closed_trades_24h = await self.trade_repository.count_closed_trades_since(since=since)

        return AdminStatusResponse(
            api_alive=True,
            generated_at=now.isoformat(),
            last_scheduler_cycle_started_at=_to_iso(operator_state.last_cycle_started_at),
            last_scheduler_cycle_finished_at=_to_iso(operator_state.last_cycle_finished_at),
            last_scheduler_cycle_fetched_news_count=operator_state.last_cycle_fetched_news_count,
            last_scheduler_cycle_inserted_news_count=operator_state.last_cycle_inserted_news_count,
            last_scheduler_cycle_error_count=operator_state.last_cycle_error_count,
            last_error=operator_state.last_error,
            news_items_count=news_items_count,
            analyses_count=analyses_count,
            signals_count=signals_count,
            paper_trades_count=paper_trades_count,
            open_positions_count=open_positions_count,
            kill_switch_enabled=kill_switch_enabled,
            inserted_news_24h=inserted_news_24h,
            analyses_count_24h=analyses_count_24h,
            signals_count_24h=signals_count_24h,
            opened_trades_24h=opened_trades_24h,
            closed_trades_24h=closed_trades_24h,
        )

    async def get_recent_signals(self, *, limit: int) -> RecentSignalsResponse:
        now = datetime.now(UTC)
        signals = await self.signal_repository.list_recent(limit=limit)

        items: list[RecentSignalItem] = []
        for signal in signals:
            analysis = signal.analysis
            items.append(
                RecentSignalItem(
                    signal_id=signal.id,
                    created_at=signal.created_at.isoformat(),
                    analysis_id=analysis.id,
                    news_item_id=analysis.news_item_id,
                    market_id=signal.market_id,
                    market_question=signal.market_question,
                    signal_status=signal.signal_status.value,
                    edge=_to_float(signal.edge),
                    market_price=_to_float(signal.market_price),
                    fair_probability=_to_float(signal.fair_probability),
                    explanation=signal.explanation,
                )
            )

        return RecentSignalsResponse(
            generated_at=now.isoformat(),
            limit=limit,
            count=len(items),
            items=items,
        )

    async def get_open_positions(self) -> OpenPositionsResponse:
        now = datetime.now(UTC)
        positions = await self.trade_repository.list_open_positions()

        items: list[OpenPositionItem] = []
        for position in positions:
            analysis = position.signal.analysis if position.signal is not None else None
            holding_minutes = round((now - position.opened_at).total_seconds() / 60, 2)
            items.append(
                OpenPositionItem(
                    position_id=position.id,
                    signal_id=position.signal_id,
                    analysis_id=analysis.id if analysis is not None else None,
                    news_item_id=analysis.news_item_id if analysis is not None else None,
                    market_id=position.market_id,
                    market_question=position.market_question,
                    side=position.side.value,
                    entry_price=_to_float(position.entry_price),
                    size_usd=_to_float(position.size_usd),
                    shares=_to_float(position.shares),
                    opened_at=position.opened_at.isoformat(),
                    holding_minutes=holding_minutes,
                )
            )

        return OpenPositionsResponse(
            generated_at=now.isoformat(),
            count=len(items),
            items=items,
        )

    async def get_paper_stats(self) -> AdminPaperStatsResponse:
        now = datetime.now(UTC)
        stats = await self.trade_repository.get_trade_statistics()

        return AdminPaperStatsResponse(
            generated_at=now.isoformat(),
            stats=PaperTradeStats.model_validate(stats),
        )
