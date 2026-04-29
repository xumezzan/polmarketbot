from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.trade import (
    PaperTradeAnalytics,
    PaperTradeAnalyticsSummary,
    PaperTradeConsistencyVerdict,
    PaperTradeDailyAnalytics,
    PaperTradeFunnelStats,
)
from app.services import proof_of_edge as proof_module
from app.services.proof_of_edge import ProofOfEdgeService, _build_consistency_verdict
from tests.helpers import build_test_settings


def _build_analytics(*, closed_trades: int, total_pnl: float, daily: list[PaperTradeDailyAnalytics]):
    return PaperTradeAnalytics(
        generated_at=datetime.now(UTC).isoformat(),
        summary=PaperTradeAnalyticsSummary(
            period_days=7,
            opened_trades=closed_trades,
            closed_trades=closed_trades,
            current_open_positions=0,
            winning_trades=max(closed_trades - 2, 0),
            losing_trades=min(2, closed_trades),
            win_rate=0.6 if closed_trades else 0.0,
            avg_pnl=round(total_pnl / closed_trades, 4) if closed_trades else 0.0,
            total_pnl=total_pnl,
            avg_win_pnl=1.5,
            avg_loss_pnl=-0.5,
            expectancy=0.4,
            avg_holding_minutes=120.0,
        ),
        funnel=PaperTradeFunnelStats(
            analyses=20,
            actionable_signals=12,
            approved_signals=10,
            blocked_signals=2,
            opened_trades=closed_trades,
            closed_trades=closed_trades,
            analysis_to_actionable_rate=0.6,
            actionable_to_approved_rate=0.8333,
            approved_to_opened_rate=1.0,
        ),
        daily=daily,
        by_market=[],
        by_source=[],
        risk_blockers=[],
    )


def test_consistency_verdict_holds_on_pnl_concentration() -> None:
    analytics = _build_analytics(
        closed_trades=10,
        total_pnl=10.0,
        daily=[
            PaperTradeDailyAnalytics(
                date="2026-04-21",
                opened_trades=2,
                closed_trades=2,
                winning_trades=2,
                losing_trades=0,
                total_pnl=9.0,
                avg_pnl=4.5,
            ),
            PaperTradeDailyAnalytics(
                date="2026-04-22",
                opened_trades=2,
                closed_trades=2,
                winning_trades=1,
                losing_trades=1,
                total_pnl=1.0,
                avg_pnl=0.5,
            ),
        ],
    )

    verdict = _build_consistency_verdict(analytics=analytics, cycles=[])

    assert verdict == PaperTradeConsistencyVerdict(
        status="HOLD",
        summary="pnl_concentrated_in_single_day",
        unstable_days=[],
        concentration_ratio=0.9,
    )


@pytest.mark.asyncio
async def test_phase_gate_report_holds_when_not_enough_closed_trades(monkeypatch) -> None:
    analytics = _build_analytics(
        closed_trades=4,
        total_pnl=2.0,
        daily=[
            PaperTradeDailyAnalytics(
                date="2026-04-21",
                opened_trades=2,
                closed_trades=2,
                winning_trades=1,
                losing_trades=1,
                total_pnl=1.0,
                avg_pnl=0.5,
            ),
            PaperTradeDailyAnalytics(
                date="2026-04-22",
                opened_trades=2,
                closed_trades=2,
                winning_trades=2,
                losing_trades=0,
                total_pnl=1.0,
                avg_pnl=0.5,
            ),
        ],
    )

    async def fake_get_paper_trade_analytics(session, settings, *, days):
        return analytics

    monkeypatch.setattr(proof_module, "get_paper_trade_analytics", fake_get_paper_trade_analytics)

    service = ProofOfEdgeService(
        trade_repository=SimpleNamespace(session=object()),
        analysis_repository=SimpleNamespace(),
        scheduler_cycle_repository=SimpleNamespace(),
    )

    service.trade_repository.list_top_closed_trades = AsyncMock(return_value=[])
    service.analysis_repository.list_with_context = AsyncMock(return_value=[])
    service.scheduler_cycle_repository.list_since = AsyncMock(
        return_value=[
            SimpleNamespace(
                started_at=datetime(2026, 4, 21, 12, 0, tzinfo=UTC),
                status="COMPLETED",
                error_count=0,
            ),
            SimpleNamespace(
                started_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
                status="COMPLETED",
                error_count=0,
            ),
        ]
    )

    report = await service.build_phase_gate_report(
        settings=build_test_settings(),
        window_days=7,
    )

    assert report.verdict == "HOLD"
    assert "need_more_runtime:2<14" in report.reasons
    assert "need_more_closed_trades:4<30" in report.reasons


@pytest.mark.asyncio
async def test_phase_gate_report_passes_when_metrics_are_good(monkeypatch) -> None:
    analytics = _build_analytics(
        closed_trades=42,
        total_pnl=14.0,
        daily=[
            PaperTradeDailyAnalytics(
                date=f"2026-04-{day:02d}",
                opened_trades=3,
                closed_trades=3,
                winning_trades=2,
                losing_trades=1,
                total_pnl=1.0,
                avg_pnl=0.3333,
            )
            for day in range(1, 15)
        ],
    )

    async def fake_get_paper_trade_analytics(session, settings, *, days):
        return analytics

    monkeypatch.setattr(proof_module, "get_paper_trade_analytics", fake_get_paper_trade_analytics)

    service = ProofOfEdgeService(
        trade_repository=SimpleNamespace(session=object()),
        analysis_repository=SimpleNamespace(),
        scheduler_cycle_repository=SimpleNamespace(),
    )
    service.trade_repository.list_top_closed_trades = AsyncMock(return_value=[])
    service.analysis_repository.list_with_context = AsyncMock(return_value=[])
    service.scheduler_cycle_repository.list_since = AsyncMock(
        return_value=[
            SimpleNamespace(
                started_at=datetime(2026, 4, day, 12, 0, tzinfo=UTC),
                status="COMPLETED",
                error_count=0,
            )
            for day in range(1, 15)
        ]
    )

    report = await service.build_phase_gate_report(
        settings=build_test_settings(),
        window_days=30,
    )

    assert report.verdict == "PASS"
    assert report.reasons == []
