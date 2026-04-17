import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.logging_utils import configure_logging
from app.scheduler import PipelineScheduler
from app.services.scheduler_dashboard import SchedulerDashboard


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the live Polymarket terminal dashboard.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scheduler cycle and exit.",
    )
    parser.add_argument(
        "--interval-minutes",
        type=float,
        default=None,
        help="Override scheduler interval for dashboard-run mode.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Stop after N cycles.",
    )
    parser.add_argument(
        "--monitor-only",
        action="store_true",
        help="Do not run the scheduler; only refresh the dashboard from database state.",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=2.0,
        help="Refresh cadence for monitor-only mode.",
    )
    return parser.parse_args()


async def _run_monitor_only(
    *,
    dashboard: SchedulerDashboard,
    refresh_seconds: float,
) -> None:
    with dashboard.run():
        while True:
            if dashboard.exit_requested:
                return
            await dashboard.refresh_data()
            dashboard.set_sleep(
                next_run_at=datetime.now(UTC) + timedelta(seconds=max(refresh_seconds, 0.5))
            )
            dashboard.refresh()
            deadline = datetime.now(UTC) + timedelta(seconds=max(refresh_seconds, 0.5))
            while True:
                if dashboard.exit_requested:
                    return
                if dashboard.take_manual_refresh_request():
                    break
                if datetime.now(UTC) >= deadline:
                    break
                dashboard.refresh()
                await asyncio.sleep(0.5)


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging("WARNING")

    dashboard = SchedulerDashboard(settings=settings, interval_minutes=args.interval_minutes)
    await dashboard.refresh_data()

    if args.monitor_only:
        await _run_monitor_only(
            dashboard=dashboard,
            refresh_seconds=args.refresh_seconds,
        )
        return

    scheduler = PipelineScheduler(settings=settings, dashboard=dashboard)
    with dashboard.run():
        if args.once:
            await scheduler.run_cycle(cycle_number=1)
            return

        await scheduler.run_loop(
            interval_minutes=args.interval_minutes,
            max_cycles=args.max_cycles,
        )


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
