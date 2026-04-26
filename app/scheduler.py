import argparse
import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Iterable

import sqlalchemy as sa

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal, engine
from app.logging_utils import configure_logging, log_event
from app.models.news import NewsItem
from app.models.enums import SignalStatus
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.schemas.news import NewsImportResult
from app.schemas.scheduler import PipelineItemResult, SchedulerCycleResult
from app.services.alerting import AlertingService, build_alert_client
from app.services.daily_report import run_daily_report
from app.services.live_execution import (
    CircuitBreakerTriggeredError,
    LiveExecutionError,
    LiveKillSwitchEnabledError,
    LiveTradingDisabledError,
    place_live_order,
    simulate_execution_intent,
)
from app.services.llm_analyzer import run_llm_analysis
from app.services.market_client import run_market_matching
from app.services.news_fetcher import run_news_ingestion
from app.services.paper_trader import (
    PaperTradingDisabledError,
    open_paper_position,
    run_paper_trade_maintenance,
)
from app.services.risk_engine import resolve_news_age_limit_minutes, run_risk_engine
from app.services.signal_engine import run_signal_engine

if TYPE_CHECKING:
    from app.services.scheduler_dashboard import SchedulerDashboard


logger = logging.getLogger(__name__)


class SchedulerError(Exception):
    """Raised when the scheduler cannot complete a pipeline cycle."""


class SchedulerLockNotAcquired(Exception):
    """Raised when another scheduler instance already holds the advisory lock."""


def _news_age_minutes(
    *,
    published_at: datetime | None,
    now: datetime,
) -> int | None:
    if published_at is None:
        return None

    published = published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)

    return int(max((now - published).total_seconds(), 0.0) // 60)


def select_pending_news_for_cycle(
    *,
    items: Iterable[NewsItem],
    settings: Settings,
    now: datetime,
) -> tuple[list[NewsItem], list[NewsItem]]:
    freshness_limit = resolve_news_age_limit_minutes(settings)
    selected: list[NewsItem] = []
    stale: list[NewsItem] = []

    for item in items:
        age_minutes = _news_age_minutes(published_at=item.published_at, now=now)
        if age_minutes is not None and age_minutes > freshness_limit:
            stale.append(item)
            continue

        selected.append(item)
        if len(selected) >= settings.scheduler_news_batch_limit:
            break

    return selected, stale


def should_skip_market_pipeline_for_direction(direction: str) -> bool:
    """Return True when a verdict can never produce a tradable signal."""
    return direction == "NONE"


class PipelineScheduler:
    """Simple asyncio-based scheduler for the full paper-trading pipeline."""

    def __init__(
        self,
        *,
        settings: Settings,
        dashboard: "SchedulerDashboard | None" = None,
    ) -> None:
        self.settings = settings
        self.dashboard = dashboard
        self.alerting_service = AlertingService(
            settings=settings,
            client=build_alert_client(settings),
        )
        self._last_daily_report_date: str | None = None
        self._active_cycle_id: str | None = None

    async def run_cycle(self, *, cycle_number: int = 1) -> SchedulerCycleResult:
        """Run one full pipeline cycle."""
        started_at = datetime.now(UTC)
        cycle_id = started_at.strftime("%Y%m%dT%H%M%S%fZ")
        self._active_cycle_id = cycle_id
        if self.dashboard is not None:
            self.dashboard.cycle_started(cycle_number=cycle_number, cycle_id=cycle_id)
            await self.dashboard.refresh_data()
            self.dashboard.refresh()
        should_fetch_news = should_run_news_ingestion(
            cycle_number=cycle_number,
            every_n_cycles=self.settings.scheduler_news_fetch_every_n_cycles,
        )

        lock_connection = await self._acquire_scheduler_lock(cycle_id=cycle_id)
        if self.settings.scheduler_lock_enabled and lock_connection is None:
            finished_at = datetime.now(UTC)
            self._active_cycle_id = None
            self._dashboard_log("SKIP", "scheduler lock not acquired", style="yellow")
            if self.dashboard is not None:
                self.dashboard.status = "IDLE"
                self.dashboard.active_cycle_id = None
                self.dashboard.refresh()
            return SchedulerCycleResult(
                cycle_id=cycle_id,
                started_at=started_at.isoformat(),
                finished_at=finished_at.isoformat(),
                source_mode=self.settings.news_fetch_mode.lower(),
                llm_mode=self.settings.llm_mode.lower(),
                fetch_mode=self.settings.market_fetch_mode.lower(),
                inserted_news_count=0,
                pending_news_count=0,
                processed_news_count=0,
                actionable_signal_count=0,
                approved_signal_count=0,
                opened_position_count=0,
                auto_close_evaluated_count=0,
                closed_position_count=0,
                error_count=0,
                item_results=[],
                closed_trade_ids=[],
            )

        try:
            async with AsyncSessionLocal() as session:
                operator_state_repository = OperatorStateRepository(session)
                scheduler_cycle_repository = SchedulerCycleRepository(session)
                await operator_state_repository.mark_cycle_started(started_at=started_at)
                await scheduler_cycle_repository.create_started(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    source_mode=self.settings.news_fetch_mode.lower(),
                    llm_mode=self.settings.llm_mode.lower(),
                    fetch_mode=self.settings.market_fetch_mode.lower(),
                )

                maintenance_result = await run_paper_trade_maintenance(session, self.settings)
                for close_result in maintenance_result.closed_results:
                    self._dashboard_log(
                        "WIN" if close_result.pnl >= 0 else "LOSS",
                        (
                            f"trade #{close_result.trade_id} closed {close_result.side} "
                            f"{self._format_price(close_result.exit_price)} "
                            f"{self._format_money(close_result.pnl)}"
                        ),
                        style="green" if close_result.pnl >= 0 else "red",
                    )
                    await self.alerting_service.send_trade_closed(
                        cycle_id=cycle_id,
                        trade=close_result,
                    )

                if should_fetch_news:
                    ingestion_result = await run_news_ingestion(session, self.settings)
                else:
                    ingestion_result = build_skipped_ingestion_result(
                        source_mode=self.settings.news_fetch_mode.lower()
                    )
                    log_event(
                        logger,
                        "news_ingestion_skipped_by_schedule",
                        cycle_id=cycle_id,
                        cycle_number=cycle_number,
                        every_n_cycles=self.settings.scheduler_news_fetch_every_n_cycles,
                    )
                self._dashboard_log(
                    "FETCH",
                    (
                        f"fetched={ingestion_result.fetched_count} "
                        f"inserted={ingestion_result.inserted_count} "
                        f"source={ingestion_result.source_mode}"
                    ),
                    style="cyan",
                )
                pending_candidates = await NewsRepository(session).list_without_analysis()
                pending_news, stale_pending_news = select_pending_news_for_cycle(
                    items=pending_candidates,
                    settings=self.settings,
                    now=started_at,
                )
                if stale_pending_news:
                    log_event(
                        logger,
                        "scheduler_stale_pending_news_skipped",
                        cycle_id=cycle_id,
                        stale_count=len(stale_pending_news),
                        stale_news_item_ids=[item.id for item in stale_pending_news[:20]],
                        freshness_limit_minutes=resolve_news_age_limit_minutes(self.settings),
                    )
                    self._dashboard_log(
                        "SKIP",
                        f"stale pending news skipped={len(stale_pending_news)}",
                        style="yellow",
                    )

                item_results: list[PipelineItemResult] = []
                actionable_signal_count = 0
                approved_signal_count = 0
                opened_position_count = 0
                closed_position_count = maintenance_result.closed_positions

                for news_item in pending_news:
                    item_result = PipelineItemResult(news_item_id=news_item.id)
                    try:
                        analysis_result = await run_llm_analysis(
                            session,
                            self.settings,
                            news_item_id=news_item.id,
                        )
                        item_result.analysis_id = analysis_result.analysis_id

                        if should_skip_market_pipeline_for_direction(
                            analysis_result.verdict.direction
                        ):
                            item_result.market_candidate_count = 0
                            log_event(
                                logger,
                                "scheduler_news_item_skipped_neutral_verdict",
                                cycle_id=cycle_id,
                                news_item_id=news_item.id,
                                analysis_id=analysis_result.analysis_id,
                                market_query=analysis_result.verdict.market_query,
                            )
                            self._dashboard_log(
                                "SKIP",
                                (
                                    f"news #{news_item.id} analysis "
                                    f"#{analysis_result.analysis_id} direction=NONE"
                                ),
                                style="yellow",
                            )
                            item_results.append(item_result)
                            continue

                        market_result = await run_market_matching(
                            session,
                            self.settings,
                            analysis_id=analysis_result.analysis_id,
                        )
                        item_result.market_candidate_count = market_result.candidate_count
                        self._dashboard_log(
                            "MATCH",
                            (
                                f"news #{news_item.id} analysis #{analysis_result.analysis_id} "
                                f"candidates={market_result.candidate_count}"
                            ),
                            style="blue",
                        )

                        signal_result = await run_signal_engine(
                            session,
                            self.settings,
                            analysis_id=analysis_result.analysis_id,
                        )

                        actionable_signals = [
                            signal
                            for signal in signal_result.signals
                            if signal.signal_status == SignalStatus.ACTIONABLE.value
                        ]
                        item_result.actionable_signal_count = len(actionable_signals)
                        actionable_signal_count += len(actionable_signals)
                        self._dashboard_log(
                            "SIGNL",
                            (
                                f"news #{news_item.id} actionable={len(actionable_signals)} "
                                f"total_signals={len(signal_result.signals)}"
                            ),
                            style="magenta",
                        )

                        for signal in actionable_signals:
                            decision = await run_risk_engine(
                                session,
                                self.settings,
                                signal_id=signal.signal_id,
                            )
                            if not decision.allow:
                                item_result.blocked_signal_count += 1
                                blocker = decision.blockers[0] if decision.blockers else "blocked"
                                self._dashboard_log(
                                    "BLOCK",
                                    f"signal #{signal.signal_id} {blocker}",
                                    style="yellow",
                                )
                                continue

                            item_result.approved_signal_count += 1
                            approved_signal_count += 1

                            execution_mode = self.settings.execution_mode.lower().strip()
                            if execution_mode == "paper":
                                try:
                                    trade_result = await open_paper_position(
                                        session,
                                        self.settings,
                                        signal_id=signal.signal_id,
                                        risk_decision=decision,
                                    )
                                except PaperTradingDisabledError as exc:
                                    item_result.blocked_signal_count += 1
                                    log_event(
                                        logger,
                                        "paper_trade_open_skipped",
                                        cycle_id=cycle_id,
                                        news_item_id=item_result.news_item_id,
                                        analysis_id=item_result.analysis_id,
                                        signal_id=signal.signal_id,
                                        reason=str(exc),
                                    )
                                    continue
                                item_result.opened_position_count += 1
                                item_result.opened_trade_ids.append(trade_result.trade_id)
                                opened_position_count += 1
                                self._dashboard_log(
                                    "OPEN",
                                    (
                                        f"trade #{trade_result.trade_id} {trade_result.side} "
                                        f"{self._format_price(trade_result.entry_price)} "
                                        f"size={self._format_money(trade_result.size_usd)}"
                                    ),
                                    style="green",
                                )
                                await self._dashboard_refresh_data()
                                await self.alerting_service.send_trade_opened(
                                    cycle_id=cycle_id,
                                    trade=trade_result,
                                )
                                continue

                            if execution_mode == "shadow":
                                shadow_result = await simulate_execution_intent(
                                    session,
                                    self.settings,
                                    signal_id=signal.signal_id,
                                    approved_size_usd=decision.approved_size_usd,
                                )
                                item_result.execution_intent_ids.append(shadow_result.intent.intent_id)
                                self._dashboard_log(
                                    "SHDW",
                                    (
                                        f"signal #{signal.signal_id} intent #{shadow_result.intent.intent_id} "
                                        f"{shadow_result.intent.side} "
                                        f"{self._format_price(shadow_result.intent.requested_price)} "
                                        f"size={self._format_money(shadow_result.intent.target_size_usd)}"
                                    ),
                                    style="cyan",
                                )
                                await self._dashboard_refresh_data()
                                continue

                            try:
                                live_result = await place_live_order(
                                    session,
                                    self.settings,
                                    signal_id=signal.signal_id,
                                    approved_size_usd=decision.approved_size_usd,
                                )
                            except (
                                LiveTradingDisabledError,
                                LiveKillSwitchEnabledError,
                                CircuitBreakerTriggeredError,
                            ) as exc:
                                item_result.blocked_signal_count += 1
                                log_event(
                                    logger,
                                    "live_trade_open_blocked",
                                    cycle_id=cycle_id,
                                    news_item_id=item_result.news_item_id,
                                    analysis_id=item_result.analysis_id,
                                    signal_id=signal.signal_id,
                                    reason=str(exc),
                                )
                                await self.alerting_service.send_live_order_failed(
                                    cycle_id=cycle_id,
                                    signal_id=signal.signal_id,
                                    market_id=signal.market_id,
                                    error=str(exc),
                                )
                                continue
                            except LiveExecutionError as exc:
                                item_result.errors.append(str(exc))
                                await self.alerting_service.send_live_order_failed(
                                    cycle_id=cycle_id,
                                    signal_id=signal.signal_id,
                                    market_id=signal.market_id,
                                    error=str(exc),
                                )
                                if not self.settings.scheduler_continue_on_item_error:
                                    raise SchedulerError(str(exc)) from exc
                                continue

                            item_result.execution_intent_ids.append(live_result.intent.intent_id)
                            if live_result.live_order_id is not None:
                                item_result.live_order_ids.append(live_result.live_order_id)
                            if live_result.live_position_id is not None:
                                item_result.opened_position_count += 1
                                opened_position_count += 1
                            self._dashboard_log(
                                "LIVE",
                                (
                                    f"order #{live_result.live_order_id} "
                                    f"{live_result.intent.side} "
                                    f"{self._format_price(live_result.intent.requested_price)} "
                                    f"size={self._format_money(live_result.intent.target_size_usd)} "
                                    f"status={live_result.order_status.lower()}"
                                ),
                                style="green" if live_result.live_position_id is not None else "yellow",
                            )
                            await self._dashboard_refresh_data()
                            await self.alerting_service.send_live_order_placed(
                                cycle_id=cycle_id,
                                result=live_result,
                            )

                        log_event(
                            logger,
                            "scheduler_news_item_completed",
                            cycle_id=cycle_id,
                            news_item_id=item_result.news_item_id,
                            analysis_id=item_result.analysis_id,
                            market_candidate_count=item_result.market_candidate_count,
                            actionable_signal_count=item_result.actionable_signal_count,
                            approved_signal_count=item_result.approved_signal_count,
                            blocked_signal_count=item_result.blocked_signal_count,
                            opened_position_count=item_result.opened_position_count,
                        )
                        self._dashboard_log(
                            "ITEM",
                            (
                                f"news #{news_item.id} done approved={item_result.approved_signal_count} "
                                f"opened={item_result.opened_position_count}"
                            ),
                            style="white",
                        )
                    except Exception as exc:
                        item_result.errors.append(str(exc))
                        log_event(
                            logger,
                            "scheduler_news_item_failed",
                            cycle_id=cycle_id,
                            news_item_id=news_item.id,
                            analysis_id=item_result.analysis_id,
                            error=str(exc),
                        )
                        self._dashboard_log(
                            "FAIL",
                            f"news #{news_item.id} {exc}",
                            style="red",
                        )
                        await self.alerting_service.send_scheduler_item_failure(
                            cycle_id=cycle_id,
                            item_result=item_result,
                        )
                        if not self.settings.scheduler_continue_on_item_error:
                            raise SchedulerError(str(exc)) from exc

                    item_results.append(item_result)

                finished_at = datetime.now(UTC)
                error_count = sum(len(item.errors) for item in item_results)
                await operator_state_repository.mark_cycle_completed(
                    started_at=started_at,
                    finished_at=finished_at,
                    fetched_news_count=ingestion_result.fetched_count,
                    inserted_news_count=ingestion_result.inserted_count,
                    error_count=error_count,
                )
                await scheduler_cycle_repository.mark_completed(
                    cycle_id=cycle_id,
                    finished_at=finished_at,
                    fetched_news_count=ingestion_result.fetched_count,
                    inserted_news_count=ingestion_result.inserted_count,
                    pending_news_count=len(item_results),
                    processed_news_count=sum(1 for item in item_results if not item.errors),
                    actionable_signal_count=actionable_signal_count,
                    approved_signal_count=approved_signal_count,
                    opened_position_count=opened_position_count,
                    auto_close_evaluated_count=maintenance_result.evaluated_positions,
                    closed_position_count=closed_position_count,
                    error_count=error_count,
                )

            result = SchedulerCycleResult(
                cycle_id=cycle_id,
                started_at=started_at.isoformat(),
                finished_at=finished_at.isoformat(),
                source_mode=self.settings.news_fetch_mode.lower(),
                llm_mode=self.settings.llm_mode.lower(),
                fetch_mode=self.settings.market_fetch_mode.lower(),
                inserted_news_count=ingestion_result.inserted_count,
                pending_news_count=len(item_results),
                processed_news_count=sum(1 for item in item_results if not item.errors),
                actionable_signal_count=actionable_signal_count,
                approved_signal_count=approved_signal_count,
                opened_position_count=opened_position_count,
                auto_close_evaluated_count=maintenance_result.evaluated_positions,
                closed_position_count=closed_position_count,
                error_count=error_count,
                item_results=item_results,
                closed_trade_ids=maintenance_result.closed_trade_ids,
            )

            log_event(
                logger,
                "scheduler_cycle_completed",
                cycle_id=result.cycle_id,
                started_at=result.started_at,
                finished_at=result.finished_at,
                inserted_news_count=result.inserted_news_count,
                pending_news_count=result.pending_news_count,
                processed_news_count=result.processed_news_count,
                actionable_signal_count=result.actionable_signal_count,
                approved_signal_count=result.approved_signal_count,
                opened_position_count=result.opened_position_count,
                auto_close_evaluated_count=result.auto_close_evaluated_count,
                closed_position_count=result.closed_position_count,
                closed_trade_ids=result.closed_trade_ids,
                error_count=result.error_count,
            )
            await self.alerting_service.send_cycle_summary(result)
            if self.dashboard is not None:
                self.dashboard.cycle_completed(result)
                await self.dashboard.refresh_data()
                self.dashboard.refresh()
            self._active_cycle_id = None
            return result
        finally:
            await self._release_scheduler_lock(lock_connection, cycle_id=cycle_id)

    async def run_loop(
        self,
        *,
        interval_minutes: float | None = None,
        max_cycles: int | None = None,
    ) -> None:
        """Run the scheduler until max_cycles is reached or forever if max_cycles=None."""
        interval = interval_minutes or self.settings.scheduler_interval_minutes
        cycle_number = 0

        while True:
            if self.dashboard is not None and self.dashboard.exit_requested:
                self._active_cycle_id = None
                return
            cycle_number += 1
            try:
                result = await self.run_cycle(cycle_number=cycle_number)
                if self.dashboard is None:
                    print(result.model_dump_json())
                await self._maybe_send_daily_report(cycle_number=cycle_number)
            except Exception as exc:
                log_event(
                    logger,
                    "scheduler_cycle_failed",
                    cycle_number=cycle_number,
                    error=str(exc),
                )
                if self.dashboard is not None:
                    self.dashboard.cycle_failed(cycle_number=cycle_number, error=str(exc))
                    await self.dashboard.refresh_data()
                    self.dashboard.refresh()
                try:
                    async with AsyncSessionLocal() as session:
                        await OperatorStateRepository(session).mark_cycle_failed(
                            finished_at=datetime.now(UTC),
                            error=str(exc),
                        )
                        if self._active_cycle_id is not None:
                            await SchedulerCycleRepository(session).mark_failed(
                                cycle_id=self._active_cycle_id,
                                finished_at=datetime.now(UTC),
                                error=str(exc),
                            )
                except Exception as state_exc:
                    log_event(
                        logger,
                        "operator_state_update_failed",
                        cycle_number=cycle_number,
                        error=str(state_exc),
                    )
                await self.alerting_service.send_system_error(
                    component="scheduler_cycle",
                    error=str(exc),
                    cycle_id=self._active_cycle_id,
                    cycle_number=cycle_number,
                )
                await self._maybe_send_daily_report(cycle_number=cycle_number)
                if not self.settings.scheduler_continue_on_item_error:
                    self._active_cycle_id = None
                    raise

                if max_cycles is not None and cycle_number >= max_cycles:
                    return

                next_run_at = datetime.now(UTC).timestamp() + max(interval, 0.0) * 60
                log_event(
                    logger,
                    "scheduler_sleeping_after_error",
                    cycle_number=cycle_number,
                    sleep_seconds=round(max(interval, 0.0) * 60, 2),
                    next_run_at=datetime.fromtimestamp(next_run_at, tz=UTC).isoformat(),
                )
                await self._sleep_until_next_cycle(
                    sleep_seconds=max(interval, 0.0) * 60,
                    next_run_at=datetime.fromtimestamp(next_run_at, tz=UTC),
                )
                if self.dashboard is not None and self.dashboard.exit_requested:
                    self._active_cycle_id = None
                    return
                self._active_cycle_id = None
                continue

            if max_cycles is not None and cycle_number >= max_cycles:
                self._active_cycle_id = None
                return

            next_run_at = datetime.now(UTC).timestamp() + max(interval, 0.0) * 60
            log_event(
                logger,
                "scheduler_sleeping",
                cycle_number=cycle_number,
                sleep_seconds=round(max(interval, 0.0) * 60, 2),
                next_run_at=datetime.fromtimestamp(next_run_at, tz=UTC).isoformat(),
            )
            await self._sleep_until_next_cycle(
                sleep_seconds=max(interval, 0.0) * 60,
                next_run_at=datetime.fromtimestamp(next_run_at, tz=UTC),
            )
            if self.dashboard is not None and self.dashboard.exit_requested:
                self._active_cycle_id = None
                return
            self._active_cycle_id = None

    async def _sleep_until_next_cycle(
        self,
        *,
        sleep_seconds: float,
        next_run_at: datetime,
    ) -> None:
        delay = max(sleep_seconds, 0.0)
        if self.dashboard is None:
            await asyncio.sleep(delay)
            return

        self.dashboard.set_sleep(next_run_at=next_run_at)
        self.dashboard.refresh()
        while True:
            if self.dashboard is not None and self.dashboard.exit_requested:
                return
            if self.dashboard is not None and self.dashboard.take_manual_refresh_request():
                await self.dashboard.refresh_data()
                self.dashboard.refresh()
            remaining = (next_run_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            self.dashboard.refresh()
            await asyncio.sleep(min(1.0, remaining))

    async def _dashboard_refresh_data(self) -> None:
        if self.dashboard is None:
            return
        await self.dashboard.refresh_data()
        self.dashboard.refresh()

    def _dashboard_log(self, tag: str, message: str, *, style: str = "white") -> None:
        if self.dashboard is None:
            return
        self.dashboard.log(tag, message, style=style)

    def _format_price(self, price: float) -> str:
        cents = price * 100
        numeric = f"{cents:.2f}".rstrip("0").rstrip(".")
        return f"{numeric}c"

    def _format_money(self, amount: float) -> str:
        sign = "-" if amount < 0 else ""
        return f"{sign}${abs(amount):,.2f}"

    async def _acquire_scheduler_lock(self, *, cycle_id: str):
        if not self.settings.scheduler_lock_enabled:
            return None

        connection = await engine.connect()
        result = await connection.execute(
            sa.text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": self.settings.scheduler_lock_key},
        )
        acquired = bool(result.scalar_one())
        if not acquired:
            await connection.close()
            log_event(
                logger,
                "scheduler_cycle_skipped_lock_not_acquired",
                cycle_id=cycle_id,
                lock_key=self.settings.scheduler_lock_key,
            )
            return None

        log_event(
            logger,
            "scheduler_lock_acquired",
            cycle_id=cycle_id,
            lock_key=self.settings.scheduler_lock_key,
        )
        return connection

    async def _release_scheduler_lock(self, connection, *, cycle_id: str) -> None:
        if connection is None:
            return

        try:
            await connection.execute(
                sa.text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": self.settings.scheduler_lock_key},
            )
            log_event(
                logger,
                "scheduler_lock_released",
                cycle_id=cycle_id,
                lock_key=self.settings.scheduler_lock_key,
            )
        finally:
            await connection.close()

    async def _maybe_send_daily_report(self, *, cycle_number: int) -> None:
        if not self.settings.alert_on_daily_report:
            return

        now = datetime.now(UTC)
        report_date = now.date().isoformat()
        if self._last_daily_report_date == report_date:
            return

        target_hour = min(max(self.settings.daily_report_hour_utc, 0), 23)
        target_minute = min(max(self.settings.daily_report_minute_utc, 0), 59)
        if (now.hour, now.minute) < (target_hour, target_minute):
            return

        try:
            async with AsyncSessionLocal() as session:
                report = await run_daily_report(
                    session,
                    self.settings,
                    window_hours=max(self.settings.daily_report_window_hours, 1),
                )
            dispatch = await self.alerting_service.send_daily_report(report=report)
            self._last_daily_report_date = report_date
            log_event(
                logger,
                "daily_report_sent",
                cycle_number=cycle_number,
                report_date=report_date,
                delivered=dispatch.delivered,
                mode=dispatch.mode,
                error=dispatch.error,
            )
        except Exception as exc:
            log_event(
                logger,
                "daily_report_failed",
                cycle_number=cycle_number,
                error=str(exc),
            )


def should_run_news_ingestion(*, cycle_number: int, every_n_cycles: int) -> bool:
    interval = max(every_n_cycles, 1)
    normalized_cycle = max(cycle_number, 1)
    return normalized_cycle % interval == 1 % interval


def build_skipped_ingestion_result(*, source_mode: str) -> NewsImportResult:
    return NewsImportResult(
        source_mode=source_mode,
        fetched_count=0,
        normalized_count=0,
        inserted_count=0,
        skipped_count=0,
        filtered_out_count=0,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full Polymarket bot scheduler.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle and exit.",
    )
    parser.add_argument(
        "--interval-minutes",
        type=float,
        default=None,
        help="Override SCHEDULER_INTERVAL_MINUTES for this run.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Stop after N cycles. Useful for testing.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Render a live Rich dashboard instead of printing JSON lines.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging("WARNING" if args.dashboard else settings.log_level)

    if args.dashboard:
        from app.services.scheduler_dashboard import SchedulerDashboard

        dashboard = SchedulerDashboard(settings=settings, interval_minutes=args.interval_minutes)
        scheduler = PipelineScheduler(settings=settings, dashboard=dashboard)
        await dashboard.refresh_data()
        with dashboard.run():
            if args.once:
                await scheduler.run_cycle(cycle_number=1)
                return

            await scheduler.run_loop(
                interval_minutes=args.interval_minutes,
                max_cycles=args.max_cycles,
            )
        return

    scheduler = PipelineScheduler(settings=settings)

    if args.once:
        result = await scheduler.run_cycle(cycle_number=1)
        print(result.model_dump_json())
        return

    await scheduler.run_loop(
        interval_minutes=args.interval_minutes,
        max_cycles=args.max_cycles,
    )


if __name__ == "__main__":
    asyncio.run(_main())
