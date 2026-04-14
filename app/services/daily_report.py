import argparse
import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.enums import MarketSide
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.news_repo import NewsRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.schemas.daily_report import BlockerStat, DailyReport
from app.services.alerting import AlertingService, build_alert_client
from app.services.market_client import MarketClientProtocol, build_market_client


logger = logging.getLogger(__name__)


class DailyReportService:
    """Build one daily operational report from PostgreSQL and market snapshots."""

    def __init__(
        self,
        *,
        settings: Settings,
        news_repository: NewsRepository,
        analysis_repository: AnalysisRepository,
        signal_repository: SignalRepository,
        trade_repository: TradeRepository,
        market_client: MarketClientProtocol,
    ) -> None:
        self.settings = settings
        self.news_repository = news_repository
        self.analysis_repository = analysis_repository
        self.signal_repository = signal_repository
        self.trade_repository = trade_repository
        self.market_client = market_client

    async def build(self, *, window_hours: int = 24) -> DailyReport:
        now = datetime.now(UTC)
        since = now - timedelta(hours=window_hours)

        inserted_news = await self.news_repository.count_created_since(since=since)
        analyses = await self.analysis_repository.list_with_context(since=since)
        signals_count = await self.signal_repository.count_created_since(since=since)
        opened_trades = await self.trade_repository.count_opened_trades_since(since=since)
        closed_trades = await self.trade_repository.count_closed_trades_since(since=since)
        open_positions = await self.trade_repository.count_open_positions()
        realized_pnl = await self.trade_repository.sum_realized_pnl_since(since=since)

        approved_signals, blocker_counter = self._collect_risk_decisions(
            analyses=analyses,
            since=since,
        )
        (
            unrealized_pnl,
            unrealized_positions_valued,
            unrealized_positions_total,
            unrealized_note,
        ) = await self._compute_unrealized_pnl()

        notes: list[str] = []
        notes.append(
            "fetched_news_24h is unavailable because ingestion fetched counts are not persisted in PostgreSQL yet."
        )
        if unrealized_note:
            notes.append(unrealized_note)

        report = DailyReport(
            generated_at=now.isoformat(),
            window_start=since.isoformat(),
            window_end=now.isoformat(),
            fetched_news_24h=None,
            inserted_news_24h=inserted_news,
            analyses_count_24h=len(analyses),
            signals_count_24h=signals_count,
            approved_signals_count_24h=approved_signals,
            opened_paper_trades_24h=opened_trades,
            closed_paper_trades_24h=closed_trades,
            open_positions_count=open_positions,
            realized_pnl_24h=round(realized_pnl, 4),
            unrealized_pnl=unrealized_pnl,
            unrealized_positions_valued=unrealized_positions_valued,
            unrealized_positions_total=unrealized_positions_total,
            top_blockers=[
                BlockerStat(reason=reason, count=count)
                for reason, count in blocker_counter.most_common(5)
            ],
            notes=notes,
        )
        log_event(
            logger,
            "daily_report_built",
            inserted_news_24h=report.inserted_news_24h,
            analyses_count_24h=report.analyses_count_24h,
            signals_count_24h=report.signals_count_24h,
            approved_signals_count_24h=report.approved_signals_count_24h,
            opened_paper_trades_24h=report.opened_paper_trades_24h,
            closed_paper_trades_24h=report.closed_paper_trades_24h,
            open_positions_count=report.open_positions_count,
            realized_pnl_24h=report.realized_pnl_24h,
            unrealized_pnl=report.unrealized_pnl,
        )
        return report

    def _collect_risk_decisions(
        self,
        *,
        analyses,
        since: datetime,
    ) -> tuple[int, Counter[str]]:
        approved_signals = 0
        blockers = Counter()

        for analysis in analyses:
            raw_response = analysis.raw_response or {}
            snapshots = raw_response.get("snapshots") or {}
            risk_snapshot = snapshots.get("risk_engine") or {}
            decisions = risk_snapshot.get("decisions") or []

            for decision in decisions:
                evaluated_at = self._parse_iso_datetime(decision.get("evaluated_at"))
                if evaluated_at is not None and evaluated_at < since:
                    continue

                if decision.get("allow"):
                    approved_signals += 1
                    continue

                for blocker in decision.get("blockers") or []:
                    reason = str(blocker).split(":", 1)[0]
                    blockers[reason] += 1

        return approved_signals, blockers

    async def _compute_unrealized_pnl(self) -> tuple[float | None, int, int, str | None]:
        open_positions = await self.trade_repository.list_open_positions()
        if not open_positions:
            return 0.0, 0, 0, None

        try:
            markets = await self.market_client.fetch_markets()
        except Exception as exc:
            return (
                None,
                0,
                len(open_positions),
                f"unrealized_pnl is unavailable: market snapshot fetch failed ({exc}).",
            )

        markets_by_id = {market.id: market for market in markets}
        unrealized_total = 0.0
        valued_count = 0

        for position in open_positions:
            trade = await self.trade_repository.get_open_trade_for_position(position_id=position.id)
            if trade is None:
                continue

            market = markets_by_id.get(position.market_id)
            if market is None:
                continue

            current_price = self._resolve_position_price(
                side=position.side.value,
                yes_price=market.yes_price,
                no_price=market.no_price,
                last_trade_price=market.last_trade_price,
            )
            if current_price is None:
                continue

            entry_price = float(trade.entry_price)
            shares = float(trade.shares)
            unrealized_total += (current_price - entry_price) * shares
            valued_count += 1

        note = None
        if valued_count < len(open_positions):
            note = (
                "unrealized_pnl is partial: not all open positions had a usable market snapshot price."
            )

        return round(unrealized_total, 4), valued_count, len(open_positions), note

    def _resolve_position_price(
        self,
        *,
        side: str,
        yes_price: float | None,
        no_price: float | None,
        last_trade_price: float | None,
    ) -> float | None:
        if side == MarketSide.YES.value:
            if yes_price is not None:
                return yes_price
            return last_trade_price

        if side == MarketSide.NO.value:
            if no_price is not None:
                return no_price
            if yes_price is not None:
                return round(1 - yes_price, 4)
            return None

        return None

    def _parse_iso_datetime(self, value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


async def run_daily_report(
    session: AsyncSession,
    settings: Settings,
    *,
    window_hours: int,
) -> DailyReport:
    """Convenience entrypoint to build one daily report payload."""
    service = DailyReportService(
        settings=settings,
        news_repository=NewsRepository(session),
        analysis_repository=AnalysisRepository(session),
        signal_repository=SignalRepository(session),
        trade_repository=TradeRepository(session),
        market_client=build_market_client(settings),
    )
    return await service.build(window_hours=window_hours)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally send a daily report.")
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Report window in hours (default: 24).",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send the report via AlertingService after building it.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        report = await run_daily_report(
            session,
            settings,
            window_hours=max(args.hours, 1),
        )
        print(report.model_dump_json())

    if args.send:
        alerting_service = AlertingService(
            settings=settings,
            client=build_alert_client(settings),
        )
        dispatch = await alerting_service.send_daily_report(report=report)
        print(dispatch.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
