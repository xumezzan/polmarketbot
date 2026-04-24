import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.repositories.news_repo import NewsRepository
from app.services.risk_engine import resolve_news_age_limit_minutes


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete stale unanalyzed news rows that will never be processed."
    )
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=None,
        help="Override the stale threshold. Defaults to the active risk news-age limit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print how many rows would be deleted.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    max_age_minutes = (
        args.max_age_minutes
        if args.max_age_minutes is not None
        else resolve_news_age_limit_minutes(settings)
    )
    cutoff = datetime.now(UTC) - timedelta(minutes=max_age_minutes)

    async with AsyncSessionLocal() as session:
        news_repository = NewsRepository(session)
        stale_count = await news_repository.count_stale_without_analysis(cutoff=cutoff)
        if args.dry_run:
            print(
                f"dry_run=true max_age_minutes={max_age_minutes} "
                f"cutoff={cutoff.isoformat()} stale_unanalyzed_news={stale_count}"
            )
            return

        deleted_count = await news_repository.delete_stale_without_analysis(cutoff=cutoff)
        print(
            f"dry_run=false max_age_minutes={max_age_minutes} "
            f"cutoff={cutoff.isoformat()} deleted_stale_unanalyzed_news={deleted_count}"
        )


if __name__ == "__main__":
    asyncio.run(_main())
