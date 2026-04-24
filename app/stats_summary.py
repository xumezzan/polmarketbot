import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.live_trade_repo import LiveTradeRepository
from app.repositories.news_repo import NewsRepository
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.repositories.trade_repo import TradeRepository
from app.services.risk_engine import resolve_news_age_limit_minutes
from app.services.proof_of_edge import ProofOfEdgeService


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_money(value: float) -> str:
    return f"{value:.4f} USD"


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


async def get_stats() -> None:
    async with AsyncSessionLocal() as session:
        trade_repo = TradeRepository(session)
        live_trade_repo = LiveTradeRepository(session)
        analysis_repo = AnalysisRepository(session)
        news_repo = NewsRepository(session)
        cycle_repo = SchedulerCycleRepository(session)
        settings = get_settings()
        settings_age_limit = resolve_news_age_limit_minutes(settings)

        stats = await trade_repo.get_trade_statistics()
        proof_service = ProofOfEdgeService(
            trade_repository=trade_repo,
            analysis_repository=analysis_repo,
            scheduler_cycle_repository=cycle_repo,
        )
        phase_gate = await proof_service.build_phase_gate_report(
            settings=settings,
            window_days=7,
        )
        now = datetime.now(UTC)
        since_24h = now - timedelta(hours=24)
        recent_news = await news_repo.list_without_analysis(limit=5)
        last_cycle = await cycle_repo.list_recent(limit=1)
        trades_24h = await trade_repo.count_opened_trades_since(since=since_24h)
        pnl_24h = await trade_repo.sum_realized_pnl_since(since=since_24h)
        closed_trades_24h = await trade_repo.count_closed_trades_since(since=since_24h)
        stale_pending = await news_repo.count_stale_without_analysis(
            cutoff=now - timedelta(minutes=settings_age_limit)
        )
        open_positions = await trade_repo.list_open_positions()
        live_open_positions = await live_trade_repo.count_open_positions()
        live_orders = await live_trade_repo.count_orders()

        print("\n=== BOT STATISTICS ===")
        print(f"Total trades: {stats['total_trades']}")
        print(f"Closed trades: {stats['closed_trades']}")
        print(f"Open positions: {stats['open_positions']}")
        print(f"Live orders: {live_orders}")
        print(f"Live open positions: {live_open_positions}")
        print(f"Opened trades in last 24h: {trades_24h}")
        print(f"Closed trades in last 24h: {closed_trades_24h}")
        print(f"Total PnL: {_format_money(stats['total_pnl'])}")
        print(f"PnL in last 24h: {_format_money(pnl_24h)}")
        print(f"Win rate: {_format_pct(stats['win_rate'])}")
        print(f"Stale pending news: {stale_pending} (> {settings_age_limit} min without analysis)")

        print("\n=== PROOF OF EDGE ===")
        print(f"Verdict: {phase_gate.verdict}")
        print(f"Closed trades: {phase_gate.closed_trades}")
        print(f"Average PnL: {_format_money(phase_gate.avg_pnl)}")
        print(f"Total PnL: {_format_money(phase_gate.total_pnl)}")
        print(f"Consistency: {phase_gate.consistency.status} ({phase_gate.consistency.summary})")

        if last_cycle:
            lc = last_cycle[0]
            print("\n=== LAST CYCLE ===")
            print(f"Cycle ID: {lc.cycle_id}")
            print(f"Status: {lc.status}")
            print(f"Started: {_format_dt(lc.started_at)}")
            print(f"Finished: {_format_dt(lc.finished_at)}")
            print(f"Errors: {lc.error_count}")
            print(f"News processed: {lc.processed_news_count}")
            print(f"Opened positions: {lc.opened_position_count}")

        print("\n=== OPEN POSITIONS ===")
        if not open_positions:
            print("None")
        for position in open_positions:
            print(
                f"- position #{position.id} {position.side.value} {position.market_id} "
                f"entry={position.entry_price:.4f} size={position.size_usd:.2f} "
                f"opened={_format_dt(position.opened_at)}"
            )

        print("\n=== RECENT UNANALYZED NEWS ===")
        if not recent_news:
            print("None")
        for item in recent_news:
            print(
                f"- [{_format_dt(item.published_at)}] #{item.id} "
                f"{item.title} ({item.source})"
            )


if __name__ == "__main__":
    asyncio.run(get_stats())
