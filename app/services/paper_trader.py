import argparse
import asyncio
import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.enums import MarketSide, SignalStatus, VerdictDirection
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.forecast_observation_repo import ForecastObservationRepository
from app.repositories.runtime_flag_repo import RuntimeFlagRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.runtime_flags import RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH
from app.schemas.risk import RiskDecision
from app.schemas.forecast_observation import ForecastObservationSyncResult
from app.schemas.trade import (
    PaperOpenPositionReport,
    PaperOpenPositionReportItem,
    PaperRiskBlockerCount,
    PaperTradeAnalytics,
    PaperTradeAnalyticsSummary,
    PaperTradeAutoCloseDecision,
    PaperTradeBreakdownRow,
    PaperTradeCloseResult,
    PaperTradeDailyAnalytics,
    PaperTradeFunnelStats,
    PaperTradeMaintenanceResult,
    PaperTradeOpenResult,
    PaperTradeStats,
)
from app.services.alerting import AlertingService, build_alert_client
from app.services.forecasting import calculate_brier_score, resolve_market_resolution
from app.services.market_client import MarketClientProtocol, build_market_client
from app.services.risk_engine import RiskEngine


logger = logging.getLogger(__name__)


class PaperTraderError(Exception):
    """Raised when a paper trade cannot be opened or closed."""


class PaperTradingDisabledError(PaperTraderError):
    """Raised when kill switch blocks opening a new paper trade."""


def calculate_pnl(*, entry_price: float, exit_price: float, shares: float) -> float:
    """Return the realized PnL for one binary-market position."""
    return round((exit_price - entry_price) * shares, 4)


def select_exit_market_price(
    *,
    side: str,
    yes_price: float | None,
    no_price: float | None,
    last_trade_price: float | None,
) -> float | None:
    """Return the current side-aligned market price used for paper exits."""
    normalized_side = side.upper()

    if normalized_side == MarketSide.YES.value:
        if yes_price is not None:
            return yes_price
        if last_trade_price is not None:
            return last_trade_price

    if normalized_side == MarketSide.NO.value:
        if no_price is not None:
            return no_price
        if yes_price is not None:
            return round(1 - yes_price, 4)
        if last_trade_price is not None:
            return round(1 - last_trade_price, 4)

    if last_trade_price is not None and normalized_side == MarketSide.YES.value:
        return last_trade_price
    return None


def evaluate_auto_close_case(
    *,
    settings: Settings,
    entry_price: float,
    current_price: float,
    holding_minutes: float,
    entry_edge: float | None = None,
    current_edge: float | None = None,
) -> tuple[bool, str | None, float]:
    """Return whether one paper position should be auto-closed now."""
    delta = round(current_price - entry_price, 4)

    if delta >= settings.paper_take_profit_delta:
        return (
            True,
            (
                f"take_profit_reached:{delta:.4f}>="
                f"{settings.paper_take_profit_delta:.4f}"
            ),
            delta,
        )

    if delta <= -settings.paper_stop_loss_delta:
        return (
            True,
            (
                f"stop_loss_reached:{delta:.4f}<="
                f"-{settings.paper_stop_loss_delta:.4f}"
            ),
            delta,
        )

    if (
        settings.paper_edge_exit_enabled
        and holding_minutes >= settings.paper_edge_exit_grace_minutes
        and entry_edge is not None
        and current_edge is not None
    ):
        edge_delta = round(current_edge - entry_edge, 4)
        if current_edge <= settings.paper_min_current_edge:
            return (
                True,
                (
                    f"edge_evaporated:{current_edge:.4f}<="
                    f"{settings.paper_min_current_edge:.4f}"
                ),
                delta,
            )
        if edge_delta <= -settings.paper_max_edge_deterioration:
            return (
                True,
                (
                    f"edge_deteriorated:{edge_delta:.4f}<="
                    f"-{settings.paper_max_edge_deterioration:.4f}"
                ),
                delta,
            )

    if holding_minutes >= settings.paper_max_hold_minutes:
        return (
            True,
            (
                f"max_holding_time_reached:{holding_minutes:.2f}>="
                f"{settings.paper_max_hold_minutes}"
            ),
            delta,
        )

    return False, None, delta


def calculate_current_edge(
    *,
    fair_probability: float | None,
    current_price: float,
) -> float | None:
    """Return side-aligned current edge for an open paper position."""
    if fair_probability is None:
        return None
    return round(float(fair_probability) - current_price, 4)


def evaluate_opposite_news_exit_case(
    *,
    settings: Settings,
    position_side: str,
    position_query: str | None,
    candidate_direction: str,
    candidate_query: str | None,
    candidate_confidence: float,
    candidate_relevance: float,
) -> tuple[bool, str | None]:
    """Return whether a fresh opposite verdict should invalidate an open thesis."""
    if not settings.paper_opposite_news_exit_enabled:
        return False, None

    normalized_side = position_side.upper()
    normalized_direction = candidate_direction.upper()
    if normalized_direction == VerdictDirection.NONE.value:
        return False, None
    if normalized_direction == normalized_side:
        return False, None
    if candidate_confidence < settings.paper_opposite_news_min_confidence:
        return False, None
    if candidate_relevance < settings.paper_opposite_news_min_relevance:
        return False, None

    overlap = _query_token_overlap(position_query or "", candidate_query or "")
    if overlap < settings.paper_opposite_news_min_token_overlap:
        return False, None

    return (
        True,
        (
            "opposite_news_thesis_break:"
            f"{normalized_side}->{normalized_direction},"
            f"confidence={candidate_confidence:.4f},"
            f"relevance={candidate_relevance:.4f},"
            f"overlap={overlap}"
        ),
    )


def _query_token_overlap(left: str, right: str) -> int:
    return len(_query_tokens(left) & _query_tokens(right))


def _query_tokens(value: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    stopwords = {
        "a",
        "an",
        "and",
        "by",
        "for",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "will",
    }
    generic = {
        "2026",
        "2027",
        "market",
        "markets",
        "news",
        "price",
        "prediction",
    }
    return {
        token
        for token in tokens
        if token not in stopwords and token not in generic and len(token) > 1
    }


def build_paper_trade_analytics(
    *,
    generated_at: str,
    period_days: int | None,
    trade_rows: list[dict[str, object]],
    current_open_positions: int,
    analyses_count: int,
    actionable_signal_count: int,
    approved_signal_count: int,
    blocked_signal_count: int,
    blocker_counts: Counter[str],
) -> PaperTradeAnalytics:
    """Build one explainable analytics payload from normalized trade rows."""
    opened_rows = [row for row in trade_rows if row.get("opened_in_period")]
    closed_rows = [row for row in trade_rows if row.get("closed_in_period")]
    pnl_values = [float(row["pnl"]) for row in closed_rows]
    winning = [value for value in pnl_values if value > 0]
    losing = [value for value in pnl_values if value < 0]
    total_pnl = sum(pnl_values)
    closed_count = len(closed_rows)
    win_rate = len(winning) / closed_count if closed_count else 0.0
    avg_pnl = total_pnl / closed_count if closed_count else 0.0
    avg_win_pnl = sum(winning) / len(winning) if winning else 0.0
    avg_loss_pnl = sum(losing) / len(losing) if losing else 0.0
    expectancy = (win_rate * avg_win_pnl) + ((1 - win_rate) * avg_loss_pnl)
    holding_values = [float(row["holding_minutes"]) for row in closed_rows]
    avg_holding_minutes = (
        sum(holding_values) / len(holding_values) if holding_values else 0.0
    )

    daily_map: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "opened_trades": 0,
            "closed_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
        }
    )
    for row in opened_rows:
        opened_date = str(row["opened_date"])
        daily_map[opened_date]["opened_trades"] += 1
    for row in closed_rows:
        closed_date = str(row["closed_date"])
        daily_map[closed_date]["closed_trades"] += 1
        pnl = float(row["pnl"])
        daily_map[closed_date]["total_pnl"] += pnl
        if pnl > 0:
            daily_map[closed_date]["winning_trades"] += 1
        elif pnl < 0:
            daily_map[closed_date]["losing_trades"] += 1

    daily = []
    for date_key in sorted(daily_map):
        row = daily_map[date_key]
        closed_trades = int(row["closed_trades"])
        total_day_pnl = float(row["total_pnl"])
        daily.append(
            PaperTradeDailyAnalytics(
                date=date_key,
                opened_trades=int(row["opened_trades"]),
                closed_trades=closed_trades,
                winning_trades=int(row["winning_trades"]),
                losing_trades=int(row["losing_trades"]),
                total_pnl=round(total_day_pnl, 4),
                avg_pnl=round(total_day_pnl / closed_trades, 4) if closed_trades else 0.0,
            )
        )

    by_market = _build_breakdown(
        rows=closed_rows,
        key_name="market_id",
        label_name="market_question",
    )
    by_source = _build_breakdown(
        rows=closed_rows,
        key_name="news_source",
        label_name="news_source",
    )
    risk_blockers = [
        PaperRiskBlockerCount(blocker=blocker, count=count)
        for blocker, count in blocker_counts.most_common()
    ]
    funnel = PaperTradeFunnelStats(
        analyses=analyses_count,
        actionable_signals=actionable_signal_count,
        approved_signals=approved_signal_count,
        blocked_signals=blocked_signal_count,
        opened_trades=len(opened_rows),
        closed_trades=closed_count,
        analysis_to_actionable_rate=round(
            actionable_signal_count / analyses_count, 4
        )
        if analyses_count
        else 0.0,
        actionable_to_approved_rate=round(
            approved_signal_count / actionable_signal_count, 4
        )
        if actionable_signal_count
        else 0.0,
        approved_to_opened_rate=round( len(opened_rows) / approved_signal_count, 4)
        if approved_signal_count
        else 0.0,
    )

    return PaperTradeAnalytics(
        generated_at=generated_at,
        summary=PaperTradeAnalyticsSummary(
            period_days=period_days,
            opened_trades=len(opened_rows),
            closed_trades=closed_count,
            current_open_positions=current_open_positions,
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=round(win_rate, 4),
            avg_pnl=round(avg_pnl, 4),
            total_pnl=round(total_pnl, 4),
            avg_win_pnl=round(avg_win_pnl, 4),
            avg_loss_pnl=round(avg_loss_pnl, 4),
            expectancy=round(expectancy, 4),
            avg_holding_minutes=round(avg_holding_minutes, 2),
        ),
        funnel=funnel,
        daily=daily,
        by_market=by_market,
        by_source=by_source,
        risk_blockers=risk_blockers,
    )


def _build_breakdown(
    *,
    rows: list[dict[str, object]],
    key_name: str,
    label_name: str,
) -> list[PaperTradeBreakdownRow]:
    grouped: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "label": "",
            "trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
            "holding_values": [],
        }
    )

    for row in rows:
        key = str(row.get(key_name) or "unknown")
        label = str(row.get(label_name) or key)
        bucket = grouped[key]
        bucket["label"] = label
        bucket["trades"] = int(bucket["trades"]) + 1

        pnl = float(row["pnl"])
        bucket["total_pnl"] = float(bucket["total_pnl"]) + pnl
        if pnl > 0:
            bucket["winning_trades"] = int(bucket["winning_trades"]) + 1
        elif pnl < 0:
            bucket["losing_trades"] = int(bucket["losing_trades"]) + 1
        cast_holding_values = bucket["holding_values"]
        assert isinstance(cast_holding_values, list)
        cast_holding_values.append(float(row["holding_minutes"]))

    result: list[PaperTradeBreakdownRow] = []
    for key, bucket in grouped.items():
        trades = int(bucket["trades"])
        holding_values = bucket["holding_values"]
        assert isinstance(holding_values, list)
        total_pnl = float(bucket["total_pnl"])
        result.append(
            PaperTradeBreakdownRow(
                key=key,
                label=str(bucket["label"]),
                trades=trades,
                winning_trades=int(bucket["winning_trades"]),
                losing_trades=int(bucket["losing_trades"]),
                win_rate=round(int(bucket["winning_trades"]) / trades, 4) if trades else 0.0,
                total_pnl=round(total_pnl, 4),
                avg_pnl=round(total_pnl / trades, 4) if trades else 0.0,
                avg_holding_minutes=round(sum(holding_values) / len(holding_values), 2)
                if holding_values
                else 0.0,
            )
        )

    result.sort(key=lambda item: (item.total_pnl, item.trades), reverse=True)
    return result


def _normalize_blocker_name(blocker: str) -> str:
    """Collapse parameterized blocker strings into stable blocker categories."""
    return blocker.split(":", 1)[0]


def _enum_value(value) -> str:
    """Return an enum value while keeping tests with simple strings easy to build."""
    return str(value.value if hasattr(value, "value") else value)


class PaperTrader:
    """Open, close, and summarize virtual trades."""

    def __init__(
        self,
        *,
        settings: Settings,
        signal_repository: SignalRepository,
        analysis_repository: AnalysisRepository,
        trade_repository: TradeRepository,
        forecast_observation_repository: ForecastObservationRepository,
        runtime_flag_repository: RuntimeFlagRepository,
        market_client: MarketClientProtocol,
    ) -> None:
        self.settings = settings
        self.signal_repository = signal_repository
        self.analysis_repository = analysis_repository
        self.trade_repository = trade_repository
        self.forecast_observation_repository = forecast_observation_repository
        self.runtime_flag_repository = runtime_flag_repository
        self.market_client = market_client
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
        kill_switch_enabled = await self.runtime_flag_repository.get_bool(
            key=RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH,
            default=False,
        )
        if kill_switch_enabled:
            log_event(
                logger,
                "paper_trade_open_blocked_kill_switch",
                signal_id=signal_id,
                reason="paper_trading_kill_switch_enabled",
            )
            raise PaperTradingDisabledError("paper_trading_kill_switch_enabled")

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

        analysis_trade_count = await self.trade_repository.count_trades_for_analysis(
            analysis_id=analysis.id
        )
        if analysis_trade_count >= self.settings.risk_max_trades_per_analysis:
            log_event(
                logger,
                "paper_trade_open_blocked_analysis_trade_limit",
                signal_id=signal.id,
                analysis_id=analysis.id,
                news_item_id=analysis.news_item_id,
                market_id=signal.market_id,
                analysis_trade_count=analysis_trade_count,
                max_trades_per_analysis=self.settings.risk_max_trades_per_analysis,
            )
            raise PaperTraderError(
                "analysis_trade_limit_reached:"
                f"{analysis_trade_count}>={self.settings.risk_max_trades_per_analysis}"
            )

        side = self._select_side(direction=analysis.direction)
        entry_price = float(signal.execution_price or signal.market_price)
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
        close_reason: str | None = None,
        holding_minutes: float | None = None,
        current_price_delta: float | None = None,
        resolution_outcome: str | None = None,
        resolved_at: datetime | None = None,
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
            close_reason=close_reason,
            resolution_outcome=resolution_outcome,
            resolved_at=resolved_at,
        )

        if resolution_outcome is not None:
            await self.forecast_observation_repository.upsert_for_position(
                signal_id=linked_signal_id,
                analysis_id=analysis.id,
                position_id=position.id,
                market_id=market_id,
                provider=self._select_provider(analysis),
                model=self._select_model(analysis),
                side=side,
                raw_probability=self._select_raw_probability(position.signal),
                calibrated_probability=float(position.signal.fair_probability),
                market_price=float(position.signal.market_price),
                execution_price=self._select_execution_price(position.signal),
                outcome_value=exit_price,
                outcome_label=resolution_outcome,
                brier_score=calculate_brier_score(
                    probability=float(position.signal.fair_probability),
                    outcome_value=exit_price,
                ),
                resolved_at=resolved_at or closed_at,
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
                "close_reason": close_reason,
                "holding_minutes": holding_minutes,
                "current_price_delta": current_price_delta,
                "resolution_outcome": resolution_outcome,
                "resolved_at": (resolved_at or closed_at).isoformat()
                if resolution_outcome is not None
                else None,
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
            close_reason=close_reason,
            holding_minutes=holding_minutes,
            current_price_delta=current_price_delta,
            resolution_outcome=resolution_outcome,
            resolved_at=(resolved_at or closed_at).isoformat()
            if resolution_outcome is not None
            else None,
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
            close_reason=close_reason,
            holding_minutes=holding_minutes,
            current_price_delta=current_price_delta,
            resolution_outcome=resolution_outcome,
            resolved_at=(resolved_at or closed_at).isoformat()
            if resolution_outcome is not None
            else None,
        )

    async def get_stats(self) -> PaperTradeStats:
        """Return paper trading metrics derived from persisted trades."""
        stats = await self.trade_repository.get_trade_statistics()
        return PaperTradeStats.model_validate(stats)

    async def get_analytics(self, *, days: int | None = 7) -> PaperTradeAnalytics:
        """Return paper-trading analytics for a recent period."""
        generated_at = datetime.now(UTC)
        if days is not None and days < 0:
            raise PaperTraderError("days must be >= 0.")

        since = None
        if days == 0:
            since = generated_at.replace(hour=0, minute=0, second=0, microsecond=0)
        elif days is not None:
            since = generated_at - timedelta(days=days)

        trades = await self.trade_repository.list_trades_with_context(since=since)
        analyses = await self.analysis_repository.list_with_context(since=since)
        current_open_positions = await self.trade_repository.count_open_positions()

        trade_rows = [self._serialize_trade_row(trade=trade, since=since) for trade in trades]
        actionable_signal_count = 0
        approved_signal_count = 0
        blocked_signal_count = 0
        blocker_counts: Counter[str] = Counter()

        for analysis in analyses:
            actionable_signal_count += sum(
                1
                for signal in analysis.signals
                if signal.signal_status == SignalStatus.ACTIONABLE
            )

            raw_response = analysis.raw_response or {}
            snapshots = raw_response.get("snapshots") or {}
            risk_snapshot = snapshots.get("risk_engine") or {}
            decisions = risk_snapshot.get("decisions") or []
            for decision in decisions:
                if decision.get("allow"):
                    approved_signal_count += 1
                    continue

                blocked_signal_count += 1
                for blocker in decision.get("blockers") or []:
                    blocker_counts[_normalize_blocker_name(str(blocker))] += 1

        analytics = build_paper_trade_analytics(
            generated_at=generated_at.isoformat(),
            period_days=days,
            trade_rows=trade_rows,
            current_open_positions=current_open_positions,
            analyses_count=len(analyses),
            actionable_signal_count=actionable_signal_count,
            approved_signal_count=approved_signal_count,
            blocked_signal_count=blocked_signal_count,
            blocker_counts=blocker_counts,
        )
        log_event(
            logger,
            "paper_trade_analytics_generated",
            period_days=days,
            opened_trades=analytics.summary.opened_trades,
            closed_trades=analytics.summary.closed_trades,
            current_open_positions=analytics.summary.current_open_positions,
            total_pnl=analytics.summary.total_pnl,
            actionable_signals=analytics.funnel.actionable_signals,
            approved_signals=analytics.funnel.approved_signals,
            blocked_signals=analytics.funnel.blocked_signals,
        )
        return analytics

    async def inspect_open_positions(self) -> PaperOpenPositionReport:
        """Return read-only diagnostics for open paper positions without closing them."""
        generated_at = datetime.now(UTC)
        open_positions = await self.trade_repository.list_open_positions()
        items: list[PaperOpenPositionReportItem] = []

        for position in open_positions:
            trade = await self.trade_repository.get_open_trade_for_position(position_id=position.id)
            market = await self.market_client.fetch_market(position.market_id)
            opposite_news_reason = await self._opposite_news_exit_reason(position=position)
            items.append(
                self._build_open_position_report_item(
                    position=position,
                    trade=trade,
                    market=market,
                    generated_at=generated_at,
                    opposite_news_reason=opposite_news_reason,
                )
            )

        would_close_count = sum(1 for item in items if item.action == "WOULD_CLOSE")
        held_count = sum(1 for item in items if item.action == "HELD")
        skipped_count = sum(1 for item in items if item.action == "SKIPPED")
        return PaperOpenPositionReport(
            generated_at=generated_at.isoformat(),
            count=len(items),
            would_close_count=would_close_count,
            held_count=held_count,
            skipped_count=skipped_count,
            items=items,
        )

    def _build_open_position_report_item(
        self,
        *,
        position,
        trade,
        market,
        generated_at: datetime,
        opposite_news_reason: str | None,
    ) -> PaperOpenPositionReportItem:
        signal = position.signal
        analysis = signal.analysis if signal is not None else None
        news = analysis.news_item if analysis is not None else None
        side = _enum_value(position.side)
        holding_minutes = round((generated_at - position.opened_at).total_seconds() / 60, 2)
        fair_probability = (
            float(signal.fair_probability)
            if signal is not None and signal.fair_probability is not None
            else None
        )
        entry_edge = (
            float(signal.edge)
            if signal is not None and signal.edge is not None
            else None
        )

        base = {
            "position_id": position.id,
            "trade_id": trade.id if trade is not None else None,
            "signal_id": position.signal_id,
            "analysis_id": analysis.id if analysis is not None else None,
            "news_item_id": analysis.news_item_id if analysis is not None else None,
            "news_title": news.title if news is not None else None,
            "news_source": news.source if news is not None else None,
            "market_id": position.market_id,
            "market_question": position.market_question,
            "market_query": analysis.market_query if analysis is not None else None,
            "side": side,
            "entry_price": float(position.entry_price),
            "size_usd": float(position.size_usd),
            "shares": float(position.shares),
            "fair_probability": fair_probability,
            "entry_edge": entry_edge,
            "opened_at": position.opened_at.isoformat(),
            "holding_minutes": holding_minutes,
            "opposite_news_reason": opposite_news_reason,
        }

        if trade is None:
            return PaperOpenPositionReportItem(
                **base,
                action="SKIPPED",
                error="open_trade_not_found",
            )

        if market is None:
            return PaperOpenPositionReportItem(
                **base,
                action="SKIPPED",
                close_reason="market_snapshot_not_found",
                error="market_snapshot_not_found",
            )

        current_price = select_exit_market_price(
            side=side,
            yes_price=market.yes_price,
            no_price=market.no_price,
            last_trade_price=market.last_trade_price,
        )
        market_fields = {
            "liquidity": market.liquidity,
            "best_bid": market.best_bid,
            "best_ask": market.best_ask,
            "last_trade_price": market.last_trade_price,
        }
        if current_price is None:
            return PaperOpenPositionReportItem(
                **base,
                **market_fields,
                action="SKIPPED",
                close_reason="market_price_unavailable",
                error="market_price_unavailable",
            )

        current_price_delta = round(current_price - float(trade.entry_price), 4)
        current_edge = calculate_current_edge(
            fair_probability=fair_probability,
            current_price=current_price,
        )
        edge_delta = (
            round(current_edge - entry_edge, 4)
            if current_edge is not None and entry_edge is not None
            else None
        )
        priced_fields = {
            **market_fields,
            "current_price": current_price,
            "current_price_delta": current_price_delta,
            "current_edge": current_edge,
            "edge_delta": edge_delta,
        }

        resolution = resolve_market_resolution(market)
        if resolution is not None:
            return PaperOpenPositionReportItem(
                **base,
                **priced_fields,
                action="WOULD_CLOSE",
                close_reason=f"market_resolved:{resolution.outcome_label}",
            )

        if opposite_news_reason is not None:
            return PaperOpenPositionReportItem(
                **base,
                **priced_fields,
                action="WOULD_CLOSE",
                close_reason=opposite_news_reason,
            )

        if not self.settings.paper_auto_close_enabled:
            return PaperOpenPositionReportItem(
                **base,
                **priced_fields,
                action="HELD",
                close_reason="auto_close_disabled",
            )

        should_close, close_reason, _ = evaluate_auto_close_case(
            settings=self.settings,
            entry_price=float(trade.entry_price),
            current_price=current_price,
            holding_minutes=holding_minutes,
            entry_edge=entry_edge,
            current_edge=current_edge,
        )
        return PaperOpenPositionReportItem(
            **base,
            **priced_fields,
            action="WOULD_CLOSE" if should_close else "HELD",
            close_reason=close_reason if should_close else "hold_conditions_not_met",
        )

    async def maintain_open_positions(self) -> PaperTradeMaintenanceResult:
        """Apply simple auto-close rules to every open paper position."""
        open_positions = await self.trade_repository.list_open_positions()
        auto_close_enabled = self.settings.paper_auto_close_enabled
        decisions: list[PaperTradeAutoCloseDecision] = []
        closed_trade_ids: list[int] = []
        closed_results: list[PaperTradeCloseResult] = []

        for position in open_positions:
            base_decision = {
                "position_id": position.id,
                "signal_id": position.signal_id,
                "analysis_id": position.signal.analysis.id if position.signal else None,
                "news_item_id": (
                    position.signal.analysis.news_item_id
                    if position.signal and position.signal.analysis
                    else None
                ),
                "market_id": position.market_id,
            }
            trade = await self.trade_repository.get_open_trade_for_position(position_id=position.id)
            if trade is None:
                decision = PaperTradeAutoCloseDecision(
                    **base_decision,
                    action="SKIPPED",
                    error="open_trade_not_found",
                )
                decisions.append(decision)
                continue

            holding_minutes = round(
                (datetime.now(UTC) - trade.opened_at).total_seconds() / 60,
                2,
            )
            market = await self.market_client.fetch_market(position.market_id)
            if market is None:
                decisions.append(
                    PaperTradeAutoCloseDecision(
                        **base_decision,
                        trade_id=trade.id,
                        action="SKIPPED",
                        close_reason="market_snapshot_not_found",
                        holding_minutes=holding_minutes,
                    )
                )
                continue

            resolution = resolve_market_resolution(market)
            current_price = select_exit_market_price(
                side=position.side.value,
                yes_price=market.yes_price,
                no_price=market.no_price,
                last_trade_price=market.last_trade_price,
            )
            if current_price is None:
                decisions.append(
                    PaperTradeAutoCloseDecision(
                        **base_decision,
                        trade_id=trade.id,
                        action="SKIPPED",
                        close_reason="market_price_unavailable",
                        holding_minutes=holding_minutes,
                    )
                )
                continue

            if resolution is not None:
                close_reason = f"market_resolved:{resolution.outcome_label}"
                current_edge = calculate_current_edge(
                    fair_probability=(
                        float(position.signal.fair_probability)
                        if position.signal is not None
                        else None
                    ),
                    current_price=current_price,
                )
                entry_edge = (
                    float(position.signal.edge)
                    if position.signal is not None and position.signal.edge is not None
                    else None
                )
                close_result = await self.close_position(
                    position_id=position.id,
                    exit_price=current_price,
                    close_reason=close_reason,
                    holding_minutes=holding_minutes,
                    current_price_delta=round(current_price - float(trade.entry_price), 4),
                    resolution_outcome=resolution.outcome_label,
                    resolved_at=resolution.resolved_at,
                )
                closed_trade_ids.append(close_result.trade_id)
                closed_results.append(close_result)
                decisions.append(
                    PaperTradeAutoCloseDecision(
                        **base_decision,
                        trade_id=close_result.trade_id,
                        action="CLOSED",
                        close_reason=close_reason,
                        current_price=current_price,
                        current_price_delta=round(current_price - float(trade.entry_price), 4),
                        current_edge=current_edge,
                        edge_delta=(
                            round(current_edge - entry_edge, 4)
                            if current_edge is not None and entry_edge is not None
                            else None
                        ),
                        holding_minutes=holding_minutes,
                    )
                )
                continue

            if not auto_close_enabled:
                current_edge = calculate_current_edge(
                    fair_probability=(
                        float(position.signal.fair_probability)
                        if position.signal is not None
                        else None
                    ),
                    current_price=current_price,
                )
                entry_edge = (
                    float(position.signal.edge)
                    if position.signal is not None and position.signal.edge is not None
                    else None
                )
                decisions.append(
                    PaperTradeAutoCloseDecision(
                        **base_decision,
                        trade_id=trade.id,
                        action="HELD",
                        close_reason="auto_close_disabled",
                        current_price=current_price,
                        current_price_delta=round(current_price - float(trade.entry_price), 4),
                        current_edge=current_edge,
                        edge_delta=(
                            round(current_edge - entry_edge, 4)
                            if current_edge is not None and entry_edge is not None
                            else None
                        ),
                        holding_minutes=holding_minutes,
                    )
                )
                continue

            current_edge = calculate_current_edge(
                fair_probability=(
                    float(position.signal.fair_probability)
                    if position.signal is not None
                    else None
                ),
                current_price=current_price,
            )
            entry_edge = (
                float(position.signal.edge)
                if position.signal is not None and position.signal.edge is not None
                else None
            )
            opposite_news_reason = await self._opposite_news_exit_reason(position=position)
            if opposite_news_reason is not None:
                current_price_delta = round(current_price - float(trade.entry_price), 4)
                edge_delta = (
                    round(current_edge - entry_edge, 4)
                    if current_edge is not None and entry_edge is not None
                    else None
                )
                close_result = await self.close_position(
                    position_id=position.id,
                    exit_price=current_price,
                    close_reason=opposite_news_reason,
                    holding_minutes=holding_minutes,
                    current_price_delta=current_price_delta,
                )
                closed_trade_ids.append(close_result.trade_id)
                closed_results.append(close_result)
                decisions.append(
                    PaperTradeAutoCloseDecision(
                        **base_decision,
                        trade_id=close_result.trade_id,
                        action="CLOSED",
                        close_reason=opposite_news_reason,
                        current_price=current_price,
                        current_price_delta=current_price_delta,
                        current_edge=current_edge,
                        edge_delta=edge_delta,
                        holding_minutes=holding_minutes,
                    )
                )
                continue

            should_close, close_reason, current_price_delta = evaluate_auto_close_case(
                settings=self.settings,
                entry_price=float(trade.entry_price),
                current_price=current_price,
                holding_minutes=holding_minutes,
                entry_edge=entry_edge,
                current_edge=current_edge,
            )
            edge_delta = (
                round(current_edge - entry_edge, 4)
                if current_edge is not None and entry_edge is not None
                else None
            )

            if not should_close:
                decisions.append(
                    PaperTradeAutoCloseDecision(
                        **base_decision,
                        trade_id=trade.id,
                        action="HELD",
                        close_reason="hold_conditions_not_met",
                        current_price=current_price,
                        current_price_delta=current_price_delta,
                        current_edge=current_edge,
                        edge_delta=edge_delta,
                        holding_minutes=holding_minutes,
                    )
                )
                continue

            close_result = await self.close_position(
                position_id=position.id,
                exit_price=current_price,
                close_reason=close_reason,
                holding_minutes=holding_minutes,
                current_price_delta=current_price_delta,
            )
            closed_trade_ids.append(close_result.trade_id)
            closed_results.append(close_result)
            decisions.append(
                PaperTradeAutoCloseDecision(
                    **base_decision,
                    trade_id=close_result.trade_id,
                    action="CLOSED",
                    close_reason=close_reason,
                    current_price=current_price,
                    current_price_delta=current_price_delta,
                    current_edge=current_edge,
                    edge_delta=edge_delta,
                    holding_minutes=holding_minutes,
                )
            )

        for decision in decisions:
            log_event(
                logger,
                "paper_trade_auto_close_evaluated",
                position_id=decision.position_id,
                trade_id=decision.trade_id,
                signal_id=decision.signal_id,
                analysis_id=decision.analysis_id,
                news_item_id=decision.news_item_id,
                market_id=decision.market_id,
                action=decision.action,
                close_reason=decision.close_reason,
                current_price=decision.current_price,
                current_price_delta=decision.current_price_delta,
                current_edge=decision.current_edge,
                edge_delta=decision.edge_delta,
                holding_minutes=decision.holding_minutes,
                error=decision.error,
            )

        observation_sync = await self.sync_resolved_signal_observations(
            signal_statuses=[SignalStatus.ACTIONABLE]
        )
        result = PaperTradeMaintenanceResult(
            evaluated_positions=len(open_positions),
            closed_positions=len(closed_trade_ids),
            skipped_positions=len(decisions) - len(closed_trade_ids),
            closed_trade_ids=closed_trade_ids,
            closed_results=closed_results,
            decisions=decisions,
            observation_sync=observation_sync,
        )
        log_event(
            logger,
            "paper_trade_maintenance_completed",
            evaluated_positions=result.evaluated_positions,
            closed_positions=result.closed_positions,
            skipped_positions=result.skipped_positions,
            closed_trade_ids=result.closed_trade_ids,
            observation_sync_evaluated_signals=observation_sync.evaluated_signals,
            observation_sync_synced_observations=observation_sync.synced_observations,
            observation_sync_unresolved_signals=observation_sync.unresolved_signals,
            observation_sync_skipped_signals=observation_sync.skipped_signals,
        )
        return result

    async def _opposite_news_exit_reason(self, *, position) -> str | None:
        if not self.settings.paper_opposite_news_exit_enabled:
            return None
        if position.signal is None or position.signal.analysis is None:
            return None

        since = max(
            position.opened_at,
            datetime.now(UTC)
            - timedelta(minutes=self.settings.paper_opposite_news_lookback_minutes),
        )
        analyses = await self.analysis_repository.list_with_context(since=since)
        position_query = position.signal.analysis.market_query

        for analysis in reversed(analyses):
            if analysis.id == position.signal.analysis.id:
                continue
            should_close, reason = evaluate_opposite_news_exit_case(
                settings=self.settings,
                position_side=_enum_value(position.side),
                position_query=position_query,
                candidate_direction=_enum_value(analysis.direction),
                candidate_query=analysis.market_query,
                candidate_confidence=float(analysis.confidence),
                candidate_relevance=float(analysis.relevance),
            )
            if should_close:
                return f"{reason},analysis_id={analysis.id}"

        return None

    async def sync_resolved_signal_observations(
        self,
        *,
        signal_statuses: list[SignalStatus] | None = None,
    ) -> ForecastObservationSyncResult:
        """Persist resolved outcomes for signals even when no paper trade was opened."""
        signals = await self.signal_repository.list_without_observation(
            signal_statuses=signal_statuses,
        )
        result = ForecastObservationSyncResult(evaluated_signals=len(signals))
        if not signals:
            return result

        market_ids = sorted({signal.market_id for signal in signals})
        markets = await asyncio.gather(
            *[self.market_client.fetch_market(market_id) for market_id in market_ids]
        )
        resolution_map = {
            market_id: resolution
            for market_id, market in zip(market_ids, markets, strict=False)
            if market is not None
            if (resolution := resolve_market_resolution(market)) is not None
        }

        for signal in signals:
            analysis = signal.analysis
            if analysis is None or analysis.direction == VerdictDirection.NONE:
                result.skipped_signals += 1
                continue

            resolution = resolution_map.get(signal.market_id)
            if resolution is None:
                result.unresolved_signals += 1
                continue

            outcome_value = self._select_outcome_value_for_direction(
                direction=analysis.direction.value,
                yes_outcome_value=resolution.yes_outcome_value,
            )
            await self.forecast_observation_repository.upsert_for_signal(
                signal_id=signal.id,
                analysis_id=analysis.id,
                position_id=None,
                market_id=signal.market_id,
                provider=self._select_provider(analysis),
                model=self._select_model(analysis),
                side=analysis.direction.value,
                raw_probability=self._select_raw_probability(signal),
                calibrated_probability=float(signal.fair_probability),
                market_price=float(signal.market_price),
                execution_price=self._select_execution_price(signal),
                outcome_value=outcome_value,
                outcome_label=resolution.outcome_label,
                brier_score=calculate_brier_score(
                    probability=float(signal.fair_probability),
                    outcome_value=outcome_value,
                ),
                resolved_at=resolution.resolved_at,
            )
            result.synced_observations += 1
            result.synced_signal_ids.append(signal.id)

        log_event(
            logger,
            "forecast_observation_sync_completed",
            evaluated_signals=result.evaluated_signals,
            synced_observations=result.synced_observations,
            unresolved_signals=result.unresolved_signals,
            skipped_signals=result.skipped_signals,
            synced_signal_ids=result.synced_signal_ids,
        )
        return result

    def _serialize_trade_row(
        self,
        *,
        trade,
        since: datetime | None,
    ) -> dict[str, object]:
        signal = trade.signal
        analysis = signal.analysis if signal is not None else None
        news_item = analysis.news_item if analysis is not None else None
        opened_in_period = since is None or trade.opened_at >= since
        closed_in_period = (
            trade.closed_at is not None and (since is None or trade.closed_at >= since)
        )
        holding_minutes = 0.0
        if trade.closed_at is not None:
            holding_minutes = round(
                (trade.closed_at - trade.opened_at).total_seconds() / 60,
                2,
            )

        return {
            "trade_id": trade.id,
            "signal_id": trade.signal_id,
            "analysis_id": analysis.id if analysis is not None else None,
            "news_item_id": analysis.news_item_id if analysis is not None else None,
            "market_id": trade.market_id,
            "market_question": (
                signal.market_question if signal is not None and signal.market_question else trade.market_id
            ),
            "news_source": news_item.source if news_item is not None else "unknown",
            "status": trade.status.value,
            "opened_at": trade.opened_at,
            "closed_at": trade.closed_at,
            "opened_date": trade.opened_at.date().isoformat(),
            "closed_date": trade.closed_at.date().isoformat() if trade.closed_at else None,
            "opened_in_period": opened_in_period,
            "closed_in_period": closed_in_period,
            "pnl": float(trade.pnl or 0.0),
            "holding_minutes": holding_minutes,
        }

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

    def _select_raw_probability(self, signal) -> float:
        raw_probability = signal.raw_fair_probability
        if raw_probability is None:
            raw_probability = signal.fair_probability
        return float(raw_probability)

    def _select_execution_price(self, signal) -> float:
        execution_price = signal.execution_price
        if execution_price is None:
            execution_price = signal.market_price
        return float(execution_price)

    def _select_provider(self, analysis) -> str | None:
        raw_response = analysis.raw_response or {}
        if analysis.llm_provider is not None:
            return analysis.llm_provider
        provider = raw_response.get("provider")
        return str(provider) if provider is not None else None

    def _select_model(self, analysis) -> str | None:
        raw_response = analysis.raw_response or {}
        if analysis.llm_model is not None:
            return analysis.llm_model
        model = raw_response.get("model")
        return str(model) if model is not None else None

    def _select_outcome_value_for_direction(
        self,
        *,
        direction: str,
        yes_outcome_value: float,
    ) -> float:
        if direction == VerdictDirection.YES.value:
            return round(yes_outcome_value, 4)
        if direction == VerdictDirection.NO.value:
            return round(1 - yes_outcome_value, 4)
        raise PaperTraderError(f"Unsupported direction for forecast observation sync: {direction}")


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
        forecast_observation_repository=ForecastObservationRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        market_client=build_market_client(settings),
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
        forecast_observation_repository=ForecastObservationRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        market_client=build_market_client(settings),
    )
    return await trader.close_position(
        position_id=position_id,
        exit_price=exit_price,
    )


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
        forecast_observation_repository=ForecastObservationRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        market_client=build_market_client(settings),
    )
    return await trader.get_stats()


async def get_paper_trade_analytics(
    session: AsyncSession,
    settings: Settings,
    *,
    days: int | None = 7,
) -> PaperTradeAnalytics:
    """Convenience entrypoint to calculate paper-trading analytics."""
    trader = PaperTrader(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        trade_repository=TradeRepository(session),
        forecast_observation_repository=ForecastObservationRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        market_client=build_market_client(settings),
    )
    return await trader.get_analytics(days=days)


async def get_paper_open_position_report(
    session: AsyncSession,
    settings: Settings,
) -> PaperOpenPositionReport:
    """Convenience entrypoint to inspect open paper positions without side effects."""
    trader = PaperTrader(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        trade_repository=TradeRepository(session),
        forecast_observation_repository=ForecastObservationRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        market_client=build_market_client(settings),
    )
    return await trader.inspect_open_positions()


async def run_paper_trade_maintenance(
    session: AsyncSession,
    settings: Settings,
) -> PaperTradeMaintenanceResult:
    """Convenience entrypoint to auto-close paper positions when exit rules trigger."""
    trader = PaperTrader(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        trade_repository=TradeRepository(session),
        forecast_observation_repository=ForecastObservationRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        market_client=build_market_client(settings),
    )
    return await trader.maintain_open_positions()


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

    subparsers.add_parser(
        "maintain",
        help="Evaluate open paper positions and auto-close the ones that hit exit rules.",
    )
    subparsers.add_parser(
        "positions",
        help="Show read-only diagnostics for open paper positions.",
    )
    subparsers.add_parser("stats", help="Show paper-trading statistics.")
    analytics_parser = subparsers.add_parser(
        "analytics",
        help="Show paper-trading analytics for the last N days.",
    )
    analytics_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Analytics window in days. Use 0 for today only.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        alerting_service = AlertingService(
            settings=settings,
            client=build_alert_client(settings),
        )

        if args.command == "open":
            result = await open_paper_position(
                session,
                settings,
                signal_id=args.signal_id,
            )
            await alerting_service.send_trade_opened(
                cycle_id="manual_cli",
                trade=result,
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
            await alerting_service.send_trade_closed(
                cycle_id="manual_cli",
                trade=result,
            )
            print(result.model_dump_json())
            return

        if args.command == "maintain":
            result = await run_paper_trade_maintenance(session, settings)
            print(result.model_dump_json())
            return

        if args.command == "positions":
            result = await get_paper_open_position_report(session, settings)
            print(result.model_dump_json())
            return

        if args.command == "analytics":
            result = await get_paper_trade_analytics(
                session,
                settings,
                days=args.days,
            )
            print(result.model_dump_json())
            return

        result = await get_paper_trade_stats(session, settings)
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
