from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.repositories.trade_repo import TradeRepository
from app.schemas.trade import (
    PaperRiskBlockerCount,
    PaperTradeAnalytics,
    PaperTradeAuditRow,
    PaperTradeConsistencyVerdict,
    PaperTradePhaseGateReport,
)
from app.services.paper_trader import get_paper_trade_analytics


def _trade_to_audit_row(trade) -> PaperTradeAuditRow:
    analysis = trade.signal.analysis if trade.signal is not None else None
    news_item = analysis.news_item if analysis is not None else None
    return PaperTradeAuditRow(
        trade_id=trade.id,
        signal_id=trade.signal_id,
        market_id=trade.market_id,
        market_question=getattr(trade.position, "market_question", None),
        news_source=getattr(news_item, "source", None),
        opened_at=trade.opened_at.isoformat(),
        closed_at=trade.closed_at.isoformat() if trade.closed_at is not None else None,
        side=trade.side.value,
        entry_price=float(trade.entry_price),
        exit_price=float(trade.exit_price) if trade.exit_price is not None else None,
        size_usd=float(trade.size_usd),
        pnl=float(trade.pnl) if trade.pnl is not None else None,
        close_reason=trade.close_reason,
    )


def _extract_risk_blocker_counts(analyses: list) -> Counter[str]:
    counter: Counter[str] = Counter()
    for analysis in analyses:
        snapshots = dict((analysis.raw_response or {}).get("snapshots") or {})
        risk_snapshot = dict(snapshots.get("risk_engine") or {})
        for decision in risk_snapshot.get("decisions") or []:
            blockers = decision.get("blockers") or []
            counter.update(str(blocker) for blocker in blockers)
    return counter


def _build_consistency_verdict(
    *,
    analytics: PaperTradeAnalytics,
    cycles,
) -> PaperTradeConsistencyVerdict:
    unstable_days: set[str] = set()
    failed_cycles_by_day: dict[str, int] = defaultdict(int)
    for cycle in cycles:
        cycle_date = cycle.started_at.date().isoformat()
        if cycle.status == "FAILED" or int(cycle.error_count or 0) > 0:
            failed_cycles_by_day[cycle_date] += 1
            unstable_days.add(cycle_date)

    for day in analytics.daily:
        if day.closed_trades >= 2 and day.losing_trades == day.closed_trades:
            unstable_days.add(day.date)

    total_pnl = float(analytics.summary.total_pnl)
    daily_pnls = [abs(float(day.total_pnl)) for day in analytics.daily if day.closed_trades > 0]
    concentration_ratio = 0.0
    if daily_pnls:
        denominator = abs(total_pnl) if abs(total_pnl) > 0 else max(daily_pnls)
        concentration_ratio = round(max(daily_pnls) / denominator, 4) if denominator else 0.0

    if unstable_days:
        return PaperTradeConsistencyVerdict(
            status="HOLD",
            summary="daily_non_collapse_failed",
            unstable_days=sorted(unstable_days),
            concentration_ratio=concentration_ratio,
        )

    if concentration_ratio >= 0.8 and analytics.summary.closed_trades > 0:
        return PaperTradeConsistencyVerdict(
            status="HOLD",
            summary="pnl_concentrated_in_single_day",
            unstable_days=[],
            concentration_ratio=concentration_ratio,
        )

    return PaperTradeConsistencyVerdict(
        status="PASS",
        summary="daily_non_collapse_ok",
        unstable_days=[],
        concentration_ratio=concentration_ratio,
    )


class ProofOfEdgeService:
    """Build operator-friendly phase-gate report for paper-trading edge validation."""

    def __init__(
        self,
        *,
        trade_repository: TradeRepository,
        analysis_repository: AnalysisRepository,
        scheduler_cycle_repository: SchedulerCycleRepository,
    ) -> None:
        self.trade_repository = trade_repository
        self.analysis_repository = analysis_repository
        self.scheduler_cycle_repository = scheduler_cycle_repository

    async def build_phase_gate_report(
        self,
        *,
        settings,
        window_days: int = 30,
        required_min_days: int = 14,
        required_max_days: int = 30,
        required_min_closed_trades: int = 30,
    ) -> PaperTradePhaseGateReport:
        now = datetime.now(UTC)
        since = now - timedelta(days=window_days)
        analytics = await get_paper_trade_analytics(
            self.trade_repository.session,
            settings,
            days=window_days,
        )
        top_winners = await self.trade_repository.list_top_closed_trades(
            limit=3,
            descending=True,
            since=since,
        )
        top_losers = await self.trade_repository.list_top_closed_trades(
            limit=3,
            descending=False,
            since=since,
        )
        analyses = await self.analysis_repository.list_with_context(since=since)
        cycles = await self.scheduler_cycle_repository.list_since(since=since)
        blocker_counts = _extract_risk_blocker_counts(analyses)
        consistency = _build_consistency_verdict(
            analytics=analytics,
            cycles=cycles,
        )

        reasons: list[str] = []
        observed_days = len(
            {
                *{day.date for day in analytics.daily},
                *{cycle.started_at.date().isoformat() for cycle in cycles},
            }
        )
        if observed_days < required_min_days:
            reasons.append(
                f"need_more_runtime:{observed_days}<{required_min_days}"
            )
        if analytics.summary.closed_trades < required_min_closed_trades:
            reasons.append(
                f"need_more_closed_trades:{analytics.summary.closed_trades}"
                f"<{required_min_closed_trades}"
            )
        if analytics.summary.total_pnl <= 0:
            reasons.append(f"non_positive_total_pnl:{analytics.summary.total_pnl:.4f}")
        if consistency.status != "PASS":
            reasons.append(f"consistency:{consistency.summary}")
        failed_cycles = sum(
            1
            for cycle in cycles
            if cycle.status == "FAILED" or int(cycle.error_count or 0) > 0
        )
        if failed_cycles:
            reasons.append(f"pipeline_failures:{failed_cycles}")

        verdict = "PASS"
        if reasons:
            verdict = "FAIL" if observed_days >= required_max_days else "HOLD"

        return PaperTradePhaseGateReport(
            generated_at=now.isoformat(),
            window_days=window_days,
            required_min_days=required_min_days,
            required_max_days=required_max_days,
            required_min_closed_trades=required_min_closed_trades,
            verdict=verdict,
            reasons=reasons,
            win_rate=analytics.summary.win_rate,
            avg_pnl=analytics.summary.avg_pnl,
            total_pnl=analytics.summary.total_pnl,
            closed_trades=analytics.summary.closed_trades,
            analyses=analytics.funnel.analyses,
            actionable_signals=analytics.funnel.actionable_signals,
            approved_signals=analytics.funnel.approved_signals,
            pipeline_failed_cycles=failed_cycles,
            top_winners=[_trade_to_audit_row(trade) for trade in top_winners],
            top_losers=[_trade_to_audit_row(trade) for trade in top_losers],
            risk_blockers=[
                PaperRiskBlockerCount(blocker=blocker, count=count)
                for blocker, count in blocker_counts.most_common(10)
            ],
            consistency=consistency,
        )
