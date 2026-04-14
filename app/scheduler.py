import argparse
import asyncio
import logging
from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.enums import SignalStatus
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.schemas.scheduler import PipelineItemResult, SchedulerCycleResult
from app.services.alerting import AlertingService, build_alert_client
from app.services.daily_report import run_daily_report
from app.services.llm_analyzer import run_llm_analysis
from app.services.market_client import run_market_matching
from app.services.news_fetcher import run_news_ingestion
from app.services.paper_trader import (
    PaperTradingDisabledError,
    open_paper_position,
    run_paper_trade_maintenance,
)
from app.services.risk_engine import run_risk_engine
from app.services.signal_engine import run_signal_engine


logger = logging.getLogger(__name__)


class SchedulerError(Exception):
    """Raised when the scheduler cannot complete a pipeline cycle."""


class PipelineScheduler:
    """Simple asyncio-based scheduler for the full paper-trading pipeline."""

    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings
        self.alerting_service = AlertingService(
            settings=settings,
            client=build_alert_client(settings),
        )
        self._last_daily_report_date: str | None = None

    async def run_cycle(self) -> SchedulerCycleResult:
        """Run one full pipeline cycle."""
        started_at = datetime.now(UTC)
        cycle_id = started_at.strftime("%Y%m%dT%H%M%S%fZ")

        async with AsyncSessionLocal() as session:
            operator_state_repository = OperatorStateRepository(session)
            await operator_state_repository.mark_cycle_started(started_at=started_at)

            maintenance_result = await run_paper_trade_maintenance(session, self.settings)
            for close_result in maintenance_result.closed_results:
                await self.alerting_service.send_trade_closed(
                    cycle_id=cycle_id,
                    trade=close_result,
                )

            ingestion_result = await run_news_ingestion(session, self.settings)
            pending_news = await NewsRepository(session).list_without_analysis(
                limit=self.settings.scheduler_news_batch_limit
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

                    market_result = await run_market_matching(
                        session,
                        self.settings,
                        analysis_id=analysis_result.analysis_id,
                    )
                    item_result.market_candidate_count = market_result.candidate_count

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

                    for signal in actionable_signals:
                        decision = await run_risk_engine(
                            session,
                            self.settings,
                            signal_id=signal.signal_id,
                        )
                        if not decision.allow:
                            item_result.blocked_signal_count += 1
                            continue

                        item_result.approved_signal_count += 1
                        approved_signal_count += 1

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
                        await self.alerting_service.send_trade_opened(
                            cycle_id=cycle_id,
                            trade=trade_result,
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
        return result

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
            cycle_number += 1
            try:
                result = await self.run_cycle()
                print(result.model_dump_json())
                await self._maybe_send_daily_report(cycle_number=cycle_number)
            except Exception as exc:
                log_event(
                    logger,
                    "scheduler_cycle_failed",
                    cycle_number=cycle_number,
                    error=str(exc),
                )
                try:
                    async with AsyncSessionLocal() as session:
                        await OperatorStateRepository(session).mark_cycle_failed(
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
                    cycle_number=cycle_number,
                )
                await self._maybe_send_daily_report(cycle_number=cycle_number)
                if not self.settings.scheduler_continue_on_item_error:
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
                await asyncio.sleep(max(interval, 0.0) * 60)
                continue

            if max_cycles is not None and cycle_number >= max_cycles:
                return

            next_run_at = datetime.now(UTC).timestamp() + max(interval, 0.0) * 60
            log_event(
                logger,
                "scheduler_sleeping",
                cycle_number=cycle_number,
                sleep_seconds=round(max(interval, 0.0) * 60, 2),
                next_run_at=datetime.fromtimestamp(next_run_at, tz=UTC).isoformat(),
            )
            await asyncio.sleep(max(interval, 0.0) * 60)

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
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    scheduler = PipelineScheduler(settings=settings)

    if args.once:
        result = await scheduler.run_cycle()
        print(result.model_dump_json())
        return

    await scheduler.run_loop(
        interval_minutes=args.interval_minutes,
        max_cycles=args.max_cycles,
    )


if __name__ == "__main__":
    asyncio.run(_main())
