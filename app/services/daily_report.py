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
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.schemas.daily_report import (
    BlockerStat,
    DailyReport,
    ProviderCooldownStat,
    ProviderFailureStat,
)
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
        scheduler_cycle_repository: SchedulerCycleRepository,
        market_client: MarketClientProtocol,
    ) -> None:
        self.settings = settings
        self.news_repository = news_repository
        self.analysis_repository = analysis_repository
        self.signal_repository = signal_repository
        self.trade_repository = trade_repository
        self.scheduler_cycle_repository = scheduler_cycle_repository
        self.market_client = market_client

    async def build(self, *, window_hours: int = 24) -> DailyReport:
        now = datetime.now(UTC)
        since = now - timedelta(hours=window_hours)

        fetched_news = await self.scheduler_cycle_repository.sum_fetched_news_since(since=since)
        scheduler_cycles = await self.scheduler_cycle_repository.count_cycles_since(since=since)
        failed_cycles = await self.scheduler_cycle_repository.count_failed_cycles_since(since=since)
        actionable_signals = await self.scheduler_cycle_repository.sum_actionable_signals_since(
            since=since
        )
        consecutive_failed_cycles = await self.scheduler_cycle_repository.count_consecutive_failed_cycles()
        consecutive_idle_cycles = await self.scheduler_cycle_repository.count_consecutive_idle_cycles()
        latest_successful_cycle = await self.scheduler_cycle_repository.get_latest_completed()
        provider_failures = await self.scheduler_cycle_repository.get_provider_failure_counts_since(
            since=since
        )
        provider_cooldowns = await self.scheduler_cycle_repository.get_active_provider_cooldowns(
            now=now,
            newsapi_cooldown_minutes=self.settings.news_rate_limit_cooldown_minutes,
        )
        inserted_news = await self.news_repository.count_created_since(since=since)
        analyses = await self.analysis_repository.list_with_context(since=since)
        llm_tokens = await self.analysis_repository.sum_total_tokens_since(since=since)
        llm_cost = await self.analysis_repository.sum_estimated_cost_since(since=since)
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
        if failed_cycles > 0:
            notes.append(f"failed_scheduler_cycles_24h={failed_cycles}")
        if consecutive_failed_cycles > 0:
            notes.append(f"consecutive_failed_cycles={consecutive_failed_cycles}")
        if consecutive_idle_cycles > 0:
            notes.append(f"consecutive_idle_cycles={consecutive_idle_cycles}")
        if provider_cooldowns:
            notes.extend(
                [
                    (
                        "provider_cooldown_active:"
                        f"{provider}:remaining_seconds={remaining_seconds}"
                    )
                    for provider, _cooldown_until, remaining_seconds, _reason in provider_cooldowns
                ]
            )
        if unrealized_note:
            notes.append(unrealized_note)

        report = DailyReport(
            generated_at=now.isoformat(),
            window_start=since.isoformat(),
            window_end=now.isoformat(),
            fetched_news_24h=fetched_news,
            scheduler_cycles_24h=scheduler_cycles,
            failed_cycles_24h=failed_cycles,
            consecutive_failed_cycles=consecutive_failed_cycles,
            consecutive_idle_cycles=consecutive_idle_cycles,
            last_successful_cycle_at=(
                latest_successful_cycle.finished_at.isoformat()
                if latest_successful_cycle is not None and latest_successful_cycle.finished_at is not None
                else None
            ),
            inserted_news_24h=inserted_news,
            analyses_count_24h=len(analyses),
            llm_tokens_24h=llm_tokens,
            llm_cost_24h=round(llm_cost, 6),
            signals_count_24h=signals_count,
            actionable_signals_count_24h=actionable_signals,
            approved_signals_count_24h=approved_signals,
            opened_paper_trades_24h=opened_trades,
            closed_paper_trades_24h=closed_trades,
            open_positions_count=open_positions,
            realized_pnl_24h=round(realized_pnl, 4),
            unrealized_pnl=unrealized_pnl,
            unrealized_positions_valued=unrealized_positions_valued,
            unrealized_positions_total=unrealized_positions_total,
            provider_failures=[
                ProviderFailureStat(provider=provider, count=count)
                for provider, count in provider_failures
            ],
            provider_cooldowns=[
                ProviderCooldownStat(
                    provider=provider,
                    cooldown_until=cooldown_until.isoformat(),
                    remaining_seconds=remaining_seconds,
                    reason=reason,
                )
                for provider, cooldown_until, remaining_seconds, reason in provider_cooldowns
            ],
            top_blockers=[
                BlockerStat(reason=reason, count=count)
                for reason, count in blocker_counter.most_common(5)
            ],
            notes=notes,
        )
        log_event(
            logger,
            "daily_report_built",
            fetched_news_24h=report.fetched_news_24h,
            scheduler_cycles_24h=report.scheduler_cycles_24h,
            failed_cycles_24h=report.failed_cycles_24h,
            consecutive_failed_cycles=report.consecutive_failed_cycles,
            consecutive_idle_cycles=report.consecutive_idle_cycles,
            provider_cooldowns_active=len(report.provider_cooldowns),
            actionable_signals_count_24h=report.actionable_signals_count_24h,
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
            if last_trade_price is not None:
                return round(1 - last_trade_price, 4)
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
        scheduler_cycle_repository=SchedulerCycleRepository(session),
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
