import argparse
import asyncio
import json

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.repositories.runtime_flag_repo import RuntimeFlagRepository
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.services.operator import OperatorService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print recent signal audit rows.")
    parser.add_argument("--limit", type=int, default=10, help="Number of recent signals to print.")
    return parser.parse_args()


async def _main() -> None:
    settings = get_settings()
    args = _parse_args()

    async with AsyncSessionLocal() as session:
        service = OperatorService(
            settings=settings,
            news_repository=NewsRepository(session),
            analysis_repository=AnalysisRepository(session),
            signal_repository=SignalRepository(session),
            trade_repository=TradeRepository(session),
            runtime_flag_repository=RuntimeFlagRepository(session),
            operator_state_repository=OperatorStateRepository(session),
            scheduler_cycle_repository=SchedulerCycleRepository(session),
        )
        report = await service.get_signal_audit(limit=args.limit)
        print(json.dumps(report.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
