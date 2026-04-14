import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
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
        news_repository: NewsRepository,
        trade_repository: TradeRepository,
    ) -> None:
        self.settings = settings
        self.operator_state_repository = operator_state_repository
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
        if not state or not state.last_cycle_finished_at:
            return VerificationLayer(
                status="warning",
                message="No cycle history found in operator_state.",
                details={}
            )

        now = datetime.now(UTC)
        # Use simple UTC objects to avoid offset issues
        last_cycle = state.last_cycle_finished_at
        if last_cycle.tzinfo is None:
            last_cycle = last_cycle.replace(tzinfo=UTC)

        delta = now - last_cycle
        # Warning if no cycle in 2x interval, Error if 4x interval
        interval_sec = self.settings.scheduler_interval_minutes * 60
        
        details = {
            "last_cycle_finished_at": last_cycle.isoformat(),
            "seconds_since_last_cycle": delta.total_seconds(),
            "scheduler_interval_minutes": self.settings.scheduler_interval_minutes
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
        
        return VerificationLayer(status="ok", message="Pipeline is active", details=details)

    async def verify_layer_c_data(self) -> VerificationLayer:
        """Layer C: Data is being saved (DB check)."""
        now = datetime.now(UTC)
        since_24h = now - timedelta(hours=24)
        
        news_count = await self.news_repository.count_created_since(since=since_24h)
        
        details = {
            "news_saved_last_24h": news_count,
            "lookback_hours": 24
        }

        if news_count == 0:
            return VerificationLayer(
                status="warning",
                message="No new news items saved in the last 24 hours.",
                details=details
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
