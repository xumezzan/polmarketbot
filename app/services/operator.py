from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.config import Settings
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.repositories.runtime_flag_repo import RuntimeFlagRepository
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.runtime_flags import RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH
from app.schemas.admin import (
    AdminPaperStatsResponse,
    AdminStatusResponse,
    OpenPositionItem,
    OpenPositionsResponse,
    SignalAuditItem,
    SignalAuditResponse,
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


def _analysis_snapshots(raw_response: dict[str, Any] | None) -> dict[str, Any]:
    if not raw_response:
        return {}
    snapshots = raw_response.get("snapshots")
    return snapshots if isinstance(snapshots, dict) else {}


def _find_signal_snapshot(
    *,
    raw_response: dict[str, Any] | None,
    signal_id: int,
) -> dict[str, Any] | None:
    signal_engine = _analysis_snapshots(raw_response).get("signal_engine") or {}
    signal_items = signal_engine.get("signals") or []
    for item in signal_items:
        if isinstance(item, dict) and item.get("signal_id") == signal_id:
            return item
    return None


def _find_risk_decision(
    *,
    raw_response: dict[str, Any] | None,
    signal_id: int,
) -> dict[str, Any] | None:
    risk_engine = _analysis_snapshots(raw_response).get("risk_engine") or {}
    decisions = risk_engine.get("decisions") or []
    for item in decisions:
        if isinstance(item, dict) and item.get("signal_id") == signal_id:
            return item
    return None


def _extract_market_matching_context(
    raw_response: dict[str, Any] | None,
) -> tuple[int | None, float | None]:
    market_matching = _analysis_snapshots(raw_response).get("market_matching") or {}
    candidate_count = market_matching.get("candidate_count")
    candidates = market_matching.get("candidates") or []
    scores = sorted(
        [
            float(item["match_score"])
            for item in candidates
            if isinstance(item, dict) and item.get("match_score") is not None
        ],
        reverse=True,
    )
    if len(scores) < 2:
        return candidate_count, None
    return candidate_count, round(scores[0] - scores[1], 6)


class OperatorService:
    """Read-only operator view over current bot state."""

    def __init__(
        self,
        *,
        settings: Settings,
        news_repository: NewsRepository,
        analysis_repository: AnalysisRepository,
        signal_repository: SignalRepository,
        trade_repository: TradeRepository,
        runtime_flag_repository: RuntimeFlagRepository,
        operator_state_repository: OperatorStateRepository,
        scheduler_cycle_repository: SchedulerCycleRepository,
    ) -> None:
        self.settings = settings
        self.news_repository = news_repository
        self.analysis_repository = analysis_repository
        self.signal_repository = signal_repository
        self.trade_repository = trade_repository
        self.runtime_flag_repository = runtime_flag_repository
        self.operator_state_repository = operator_state_repository
        self.scheduler_cycle_repository = scheduler_cycle_repository

    async def get_status(self) -> AdminStatusResponse:
        now = datetime.now(UTC)
        since = now - timedelta(hours=24)

        operator_state = await self.operator_state_repository.get_or_create()
        kill_switch_enabled, _ = await self.runtime_flag_repository.get_status(
            key=RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH,
            default=False,
        )

        news_items_count = await self.news_repository.count()
        fetched_news_24h = await self.scheduler_cycle_repository.sum_fetched_news_since(since=since)
        scheduler_cycles_24h = await self.scheduler_cycle_repository.count_cycles_since(since=since)
        failed_cycles_24h = await self.scheduler_cycle_repository.count_failed_cycles_since(since=since)
        provider_cooldowns = await self.scheduler_cycle_repository.get_active_provider_cooldowns(
            now=now,
            newsapi_cooldown_minutes=self.settings.news_rate_limit_cooldown_minutes,
        )
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
            fetched_news_24h=fetched_news_24h,
            scheduler_cycles_24h=scheduler_cycles_24h,
            failed_cycles_24h=failed_cycles_24h,
            provider_cooldowns={
                provider: {
                    "cooldown_until": cooldown_until.isoformat(),
                    "remaining_seconds": remaining_seconds,
                    "reason": reason,
                }
                for provider, cooldown_until, remaining_seconds, reason in provider_cooldowns
            },
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

    async def get_signal_audit(self, *, limit: int) -> SignalAuditResponse:
        now = datetime.now(UTC)
        signals = await self.signal_repository.list_recent(limit=limit)

        items: list[SignalAuditItem] = []
        for signal in signals:
            analysis = signal.analysis
            news_item = analysis.news_item
            raw_response = analysis.raw_response or {}
            signal_snapshot = _find_signal_snapshot(
                raw_response=raw_response,
                signal_id=signal.id,
            ) or {}
            risk_decision = _find_risk_decision(
                raw_response=raw_response,
                signal_id=signal.id,
            ) or {}
            candidate = signal_snapshot.get("candidate") or {}
            checks = risk_decision.get("checks") or {}
            candidate_count, top_candidate_score_delta = _extract_market_matching_context(
                raw_response
            )

            items.append(
                SignalAuditItem(
                    signal_id=signal.id,
                    created_at=signal.created_at.isoformat(),
                    analysis_id=analysis.id,
                    news_item_id=news_item.id,
                    news_title=news_item.title,
                    news_source=news_item.source,
                    news_published_at=_to_iso(news_item.published_at),
                    market_query=analysis.market_query,
                    llm_reason=analysis.reason,
                    direction=analysis.direction.value,
                    confidence=_to_float(analysis.confidence),
                    relevance=_to_float(analysis.relevance),
                    market_id=signal.market_id,
                    market_question=signal.market_question,
                    signal_status=signal.signal_status.value,
                    edge=_to_float(signal.edge),
                    market_price=_to_float(signal.market_price),
                    fair_probability=_to_float(signal.fair_probability),
                    candidate_count=(
                        int(candidate_count) if isinstance(candidate_count, int) else None
                    ),
                    match_score=(
                        float(candidate["match_score"])
                        if candidate.get("match_score") is not None
                        else None
                    ),
                    match_reasons=[
                        str(item) for item in (candidate.get("match_reasons") or [])
                    ],
                    liquidity=(
                        float(candidate["liquidity"])
                        if candidate.get("liquidity") is not None
                        else None
                    ),
                    best_bid=(
                        float(candidate["best_bid"])
                        if candidate.get("best_bid") is not None
                        else None
                    ),
                    best_ask=(
                        float(candidate["best_ask"])
                        if candidate.get("best_ask") is not None
                        else None
                    ),
                    top_candidate_score_delta=(
                        float(checks["top_candidate_score_delta"])
                        if checks.get("top_candidate_score_delta") is not None
                        else top_candidate_score_delta
                    ),
                    risk_allow=(
                        bool(risk_decision["allow"])
                        if risk_decision.get("allow") is not None
                        else None
                    ),
                    risk_blockers=[
                        str(item) for item in (risk_decision.get("blockers") or [])
                    ],
                    approved_size_usd=(
                        float(risk_decision["approved_size_usd"])
                        if risk_decision.get("approved_size_usd") is not None
                        else None
                    ),
                )
            )

        return SignalAuditResponse(
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
