import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.repositories.trade_repo import TradeRepository
from app.schemas.monitor import SystemVerificationReport, VerificationLayer


logger = logging.getLogger(__name__)


class MonitorService:
    """Service for 4-layer system verification."""

    def __init__(
        self,
        *,
        settings: Settings,
        operator_state_repository: OperatorStateRepository,
        scheduler_cycle_repository: SchedulerCycleRepository,
        news_repository: NewsRepository,
        trade_repository: TradeRepository,
    ) -> None:
        self.settings = settings
        self.operator_state_repository = operator_state_repository
        self.scheduler_cycle_repository = scheduler_cycle_repository
        self.news_repository = news_repository
        self.trade_repository = trade_repository

    async def run_full_verification(self) -> SystemVerificationReport:
        """Run all 4 layers of verification."""
        layer_a = await self.verify_layer_a_server()
        layer_b = await self.verify_layer_b_pipeline()
        layer_c = await self.verify_layer_c_data()
        layer_d = await self.verify_layer_d_automation()

        # Overall status is "ok" only if all layers are "ok"
        statuses = [layer_a.status, layer_b.status, layer_c.status, layer_d.status]
        if "error" in statuses:
            overall = "error"
        elif "warning" in statuses:
            overall = "warning"
        else:
            overall = "ok"

        return SystemVerificationReport(
            overall_status=overall,
            layer_a_server=layer_a,
            layer_b_pipeline=layer_b,
            layer_c_data=layer_c,
            layer_d_automation=layer_d,
        )

    async def verify_layer_a_server(self) -> VerificationLayer:
        """Layer A: Server is alive (HTTP check)."""
        url = f"http://{self.settings.app_host}:{self.settings.app_port}/health"
        if self.settings.app_host == "0.0.0.0":
             url = f"http://localhost:{self.settings.app_port}/health"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
            
            if response.status_code == 200:
                return VerificationLayer(
                    status="ok",
                    message="Server responded with 200 OK",
                    details={"url": url, "status_code": 200}
                )
            else:
                return VerificationLayer(
                    status="error",
                    message=f"Server responded with {response.status_code}",
                    details={"url": url, "status_code": response.status_code}
                )
        except Exception as exc:
            return VerificationLayer(
                status="error",
                message=f"Failed to connect to server: {str(exc)}",
                details={"url": url, "error": str(exc)}
            )

    async def verify_layer_b_pipeline(self) -> VerificationLayer:
        """Layer B: Pipeline is alive (Scheduler check)."""
        state = await self.operator_state_repository.get()
        now = datetime.now(UTC)
        since_24h = now - timedelta(hours=24)
        consecutive_failed_cycles = await self.scheduler_cycle_repository.count_consecutive_failed_cycles()
        consecutive_idle_cycles = await self.scheduler_cycle_repository.count_consecutive_idle_cycles()
        failed_cycles_24h = await self.scheduler_cycle_repository.count_failed_cycles_since(
            since=since_24h
        )
        latest_finished_cycle = await self.scheduler_cycle_repository.get_latest_finished()
        latest_successful_cycle = await self.scheduler_cycle_repository.get_latest_completed()
        provider_failures = await self.scheduler_cycle_repository.get_provider_failure_counts_since(
            since=since_24h,
            limit=3,
        )
        provider_cooldowns = await self.scheduler_cycle_repository.get_active_provider_cooldowns(
            now=now,
            newsapi_cooldown_minutes=self.settings.news_rate_limit_cooldown_minutes,
        )
        if (not state or not state.last_cycle_finished_at) and latest_finished_cycle is None:
            return VerificationLayer(
                status="warning",
                message="No cycle history found in operator_state.",
                details={
                    "consecutive_failed_cycles": consecutive_failed_cycles,
                    "consecutive_idle_cycles": consecutive_idle_cycles,
                    "failed_cycles_24h": failed_cycles_24h,
                },
            )

        # Use simple UTC objects to avoid offset issues
        last_cycle = state.last_cycle_finished_at if state and state.last_cycle_finished_at else latest_finished_cycle.finished_at
        if last_cycle.tzinfo is None:
            last_cycle = last_cycle.replace(tzinfo=UTC)

        delta = now - last_cycle
        # Warning if no cycle in 2x interval, Error if 4x interval
        interval_sec = self.settings.scheduler_interval_minutes * 60
        
        details = {
            "last_cycle_finished_at": last_cycle.isoformat(),
            "seconds_since_last_cycle": delta.total_seconds(),
            "scheduler_interval_minutes": self.settings.scheduler_interval_minutes,
            "failed_cycles_24h": failed_cycles_24h,
            "consecutive_failed_cycles": consecutive_failed_cycles,
            "consecutive_idle_cycles": consecutive_idle_cycles,
            "last_successful_cycle_at": (
                latest_successful_cycle.finished_at.isoformat()
                if latest_successful_cycle is not None and latest_successful_cycle.finished_at is not None
                else None
            ),
            "provider_failures_24h": {provider: count for provider, count in provider_failures},
            "provider_cooldowns": {
                provider: {
                    "cooldown_until": cooldown_until.isoformat(),
                    "remaining_seconds": remaining_seconds,
                    "reason": reason,
                }
                for provider, cooldown_until, remaining_seconds, reason in provider_cooldowns
            },
        }

        if delta.total_seconds() > interval_sec * 4:
            return VerificationLayer(
                status="error",
                message=f"Pipeline dead: last cycle was {delta.total_seconds()/60:.1f}m ago",
                details=details
            )
        elif delta.total_seconds() > interval_sec * 2:
            return VerificationLayer(
                status="warning",
                message=f"Pipeline lagging: last cycle was {delta.total_seconds()/60:.1f}m ago",
                details=details
            )

        if provider_cooldowns:
            cooldown_summary = ", ".join(
                f"{provider}({remaining_seconds}s)"
                for provider, _cooldown_until, remaining_seconds, _reason in provider_cooldowns
            )
            return VerificationLayer(
                status="warning",
                message=f"Pipeline active, but provider cooldown is active: {cooldown_summary}",
                details=details,
            )

        if consecutive_failed_cycles >= 3:
            return VerificationLayer(
                status="error",
                message=f"Pipeline failing repeatedly: {consecutive_failed_cycles} failed cycles in a row",
                details=details,
            )

        if consecutive_failed_cycles > 0:
            return VerificationLayer(
                status="warning",
                message=f"Latest cycle failed; consecutive_failed_cycles={consecutive_failed_cycles}",
                details=details,
            )
        
        return VerificationLayer(status="ok", message="Pipeline is active", details=details)

    async def verify_layer_c_data(self) -> VerificationLayer:
        """Layer C: Data is being saved (DB check)."""
        now = datetime.now(UTC)
        since_24h = now - timedelta(hours=24)
        
        news_count = await self.news_repository.count_created_since(since=since_24h)
        consecutive_idle_cycles = await self.scheduler_cycle_repository.count_consecutive_idle_cycles()
        failed_cycles_24h = await self.scheduler_cycle_repository.count_failed_cycles_since(
            since=since_24h
        )
        provider_failures = await self.scheduler_cycle_repository.get_provider_failure_counts_since(
            since=since_24h,
            limit=3,
        )
        provider_cooldowns = await self.scheduler_cycle_repository.get_active_provider_cooldowns(
            now=now,
            newsapi_cooldown_minutes=self.settings.news_rate_limit_cooldown_minutes,
        )
        
        details = {
            "news_saved_last_24h": news_count,
            "lookback_hours": 24,
            "consecutive_idle_cycles": consecutive_idle_cycles,
            "failed_cycles_24h": failed_cycles_24h,
            "provider_failures_24h": {provider: count for provider, count in provider_failures},
            "provider_cooldowns": {
                provider: {
                    "cooldown_until": cooldown_until.isoformat(),
                    "remaining_seconds": remaining_seconds,
                    "reason": reason,
                }
                for provider, cooldown_until, remaining_seconds, reason in provider_cooldowns
            },
        }

        if news_count == 0:
            if provider_cooldowns:
                return VerificationLayer(
                    status="warning",
                    message="No new news items in 24h and provider cooldown is active.",
                    details=details
                )
            if failed_cycles_24h > 0:
                return VerificationLayer(
                    status="warning",
                    message="No new news items in 24h and provider failures were detected.",
                    details=details
                )
            return VerificationLayer(
                status="warning",
                message="No new news items saved in the last 24 hours.",
                details=details
            )

        if consecutive_idle_cycles >= 3:
            return VerificationLayer(
                status="warning",
                message=f"Data flow is idle: {consecutive_idle_cycles} consecutive idle cycles.",
                details=details,
            )
            
        return VerificationLayer(
            status="ok",
            message=f"Data flow healthy: {news_count} news items in 24h",
            details=details
        )

    async def verify_layer_d_automation(self) -> VerificationLayer:
        """Layer D: Automation is working (Trade management check)."""
        now = datetime.now(UTC)
        
        # Check for "Stuck" positions (older than max_hold + 30m buffer)
        buffer_min = 30
        threshold_min = self.settings.paper_max_hold_minutes + buffer_min
        threshold_dt = now - timedelta(minutes=threshold_min)
        
        open_positions = await self.trade_repository.list_open_positions()
        stuck_positions = []
        for p in open_positions:
             opened_at = p.opened_at
             if opened_at.tzinfo is None:
                 opened_at = opened_at.replace(tzinfo=UTC)
             if opened_at < threshold_dt:
                 stuck_positions.append({"id": p.id, "opened_at": opened_at.isoformat()})

        details = {
            "open_positions_count": len(open_positions),
            "stuck_positions_count": len(stuck_positions),
            "stuck_positions": stuck_positions,
            "max_hold_minutes": self.settings.paper_max_hold_minutes
        }

        if stuck_positions:
            return VerificationLayer(
                status="error",
                message=f"Found {len(stuck_positions)} stuck positions that should have been auto-closed.",
                details=details
            )
            
        return VerificationLayer(
            status="ok",
            message="Automation verified: no stuck positions found.",
            details=details
        )
